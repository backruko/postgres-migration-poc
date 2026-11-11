# tests/test_order_sync_batching.py
"""
オーダーの追従は、発生枠の数に比例して問い合わせを増やさない（2026-11-11、承認が 10 秒かかった件の回帰テスト）。
- テンプレートの時刻の変更で 400 枠が動いても、文の数は枠の数に比例しない（以前は 1 枠に 3 文: 1,200 文以上）
- 同じ日付・同じシート日付のつながりは、時刻だけが動いても変わらない（読み直さない）。つながりはそのまま残る
※ 顧客コードの先頭文字は空きがないため "I" を共有する
"""
import uuid
from datetime import datetime, time, timedelta, timezone

import pytest
from sqlalchemy import event, select

from tests.client_codes import new_client_code
from src.domain_biz import services as biz_services
from src.domain_biz.models import DependencyMatchRule
from src.domain_biz.schemas import BizDependencyCreate, BizMasterCreate
from src.domain_client.schemas import ClientCreate
from src.domain_ope.models import OpeDependency, OpeOrder, OrderStatus
from src.domain_plan import services as plan_services
from src.domain_plan.models import TemplateType
from src.domain_plan.schemas import PlanTemplateUpdate, RRuleTemplateCreate
from src.infrastructure.database import SessionLocal, engine
from src.infrastructure.repositories import biz_master_repo, client_repo

UTC = timezone.utc
JST = timezone(timedelta(hours=9))
HORIZON_DAYS = 400


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _biz(db, code: str, seq: int) -> int:
    return biz_master_repo.create(
        db, obj_in=BizMasterCreate(client_code=code, name=f"batching biz {seq}", business_seq=seq, manager_email=None, description=None)
    ).id


def _daily(db, biz_id: int, name: str) -> int:
    t = plan_services.create_template(db, RRuleTemplateCreate.model_validate(dict(
        biz_master_id=biz_id, name=name, template_type=TemplateType.RRULE, timezone="Asia/Tokyo",
        start_time=time(9, 0), duration_minutes=60, anchor_date=datetime.now(JST).date(), rrule="FREQ=DAILY",
    )))
    # カレンダーを先まで見たときのように、生成済みの範囲を延ばす
    template = plan_services._get_locked_template(db, t.id)
    plan_services._ensure_materialized(db, template, datetime.now(UTC) + timedelta(days=HORIZON_DAYS))
    db.commit()
    return t.id


class StatementCounter:
    def __init__(self):
        self.count = 0

    def __call__(self, *args, **kwargs):
        self.count += 1

    def __enter__(self):
        event.listen(engine, "before_cursor_execute", self)
        return self

    def __exit__(self, *exc):
        event.remove(engine, "before_cursor_execute", self)


def open_orders(db, template_id: int) -> list[OpeOrder]:
    db.expire_all()
    return list(db.scalars(select(OpeOrder).where(
        OpeOrder.template_id == template_id, OpeOrder.status == OrderStatus.PENDING,
        OpeOrder.scheduled_start > datetime.now(UTC) + timedelta(days=1),
    )))


def test_a_time_change_moves_hundreds_of_orders_with_a_handful_of_statements(db):
    code = new_client_code("I")
    client_repo.create(db, obj_in=ClientCreate(code=code, name="batching client"))
    template_id = _daily(db, _biz(db, code, 1), "Daily")
    assert len(open_orders(db, template_id)) > 350

    with StatementCounter() as counter:
        plan_services.update_template(db, template_id, PlanTemplateUpdate(start_time=time(10, 30)))

    orders = open_orders(db, template_id)
    assert all(o.scheduled_start.astimezone(JST).time() == time(10, 30) for o in orders)
    assert counter.count < 60, f"{counter.count} statements for {len(orders)} moved orders (N+1 is back?)"


def test_a_time_change_keeps_same_date_links_without_reading_them_again(db):
    code = new_client_code("I")
    client_repo.create(db, obj_in=ClientCreate(code=code, name="batching client"))
    upstream_biz, downstream_biz = _biz(db, code, 1), _biz(db, code, 2)
    _daily(db, upstream_biz, "Upstream")
    downstream_template = _daily(db, downstream_biz, "Downstream " + uuid.uuid4().hex[:4])
    biz_services.create_dependency(
        db, downstream_biz, BizDependencyCreate(upstream_biz_id=upstream_biz, match_rule=DependencyMatchRule.SAME_OCCURRENCE_DATE),
    )
    order_ids = [o.id for o in open_orders(db, downstream_template)]
    links_before = {
        (link.downstream_order_id, link.upstream_order_id)
        for link in db.scalars(select(OpeDependency).where(
            OpeDependency.downstream_order_id.in_(order_ids), OpeDependency.removed_at.is_(None),
        ))
    }
    assert len(links_before) == len(order_ids) > 350  # 下流のオーダーはそれぞれ同じ日の上流を待つ

    with StatementCounter() as counter:
        plan_services.update_template(db, downstream_template, PlanTemplateUpdate(start_time=time(11, 0)))

    db.expire_all()
    links_after = {
        (link.downstream_order_id, link.upstream_order_id)
        for link in db.scalars(select(OpeDependency).where(
            OpeDependency.downstream_order_id.in_(order_ids), OpeDependency.removed_at.is_(None),
        ))
    }
    assert links_after == links_before
    assert counter.count < 60, f"{counter.count} statements (links are read again per order?)"
