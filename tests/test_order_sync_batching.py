# tests/test_order_sync_batching.py
"""
オーダーの追従は、発生枠の数に比例して問い合わせを増やさない（2 年先まで作ったテンプレートの承認が 10 秒かかったことの回帰テスト）。
- テンプレートの時刻の変更で 400 枠が動いても、文の数は枠の数に比例しない（以前は 1 枠に 3 文: 1,200 文以上）
- 同じ日付・同じシート日付のつながりは、時刻だけが動いても変わらない（読み直さない）。つながりはそのまま残る
- 「直前の上流」（予定開始で突き合わせる）のつながりは、時刻が動いた数百のオーダーでもまとめて直す
※ 顧客コードの先頭文字は空きがないため "I" を共有する
"""
import uuid
from datetime import datetime, time, timedelta, timezone

import pytest
from sqlalchemy import event, select

from tests.client_codes import new_client_code
from src.core.clock import LOCAL_TIMEZONE, local_date_of, utc_now
from src.domain_biz import services as biz_services
from src.domain_biz.models import DependencyMatchRule
from src.domain_biz.schemas import BizDependencyCreate, BizMasterCreate
from src.domain_client.schemas import ClientCreate
from src.domain_ope.models import OpeDependency, OpeOrder, OpeOrderEvent, OrderEventType, OrderStatus
from src.domain_plan import services as plan_services
from src.domain_plan.models import TemplateType
from src.domain_plan.schemas import PlanTemplateUpdate, RRuleTemplateCreate
from src.infrastructure.database import SessionLocal, engine
from src.infrastructure.repositories import biz_master_repo, client_repo

JST = timezone(timedelta(hours=9))
HORIZON_DAYS = 400


def _early() -> datetime:
    """このファイルの「いま」: 今日の 0:30（日本時間）。今日の枠がまだ始まっていない時刻に決める
    （本当の時刻だと、今日の枠がもう始まっている時間帯は「始まった枠は動かさない」決まりが混ざり、結果が時刻で変わる）"""
    return datetime.combine(local_date_of(utc_now()), time(0, 30), tzinfo=LOCAL_TIMEZONE)


@pytest.fixture(autouse=True)
def fixed_now(frozen_clock):
    # 止めた時計の「今日」の 0:30 にする（アプリのどのモジュールも同じ「いま」になる。以前は 2 つだけ差し替えていた）
    early = datetime.combine(frozen_clock.today, time(0, 30), tzinfo=LOCAL_TIMEZONE)
    frozen_clock.set(early)
    return early


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
        start_time=time(9, 0), duration_minutes=60, anchor_date=_early().date(), rrule="FREQ=DAILY",
    )))
    # カレンダーを先まで見たときのように、生成済みの範囲を延ばす
    template = plan_services._get_locked_template(db, t.id)
    # 範囲の終わりは HORIZON_DAYS 日後の 9:30（日本時間）に決める: 最後の日の 9:00 の枠は範囲の中で、上流を 10:00 に
    # 動かすと境目をまたぐ（境目をまたいだ枠も動く = 取り消されない、を確かめる）
    until = datetime.combine(_early().date() + timedelta(days=HORIZON_DAYS), time(9, 30), tzinfo=LOCAL_TIMEZONE)
    plan_services._ensure_materialized(db, template, until)
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
        OpeOrder.scheduled_start > _early() + timedelta(days=1),
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


def test_a_rule_change_creates_hundreds_of_orders_with_a_handful_of_statements(db):
    """新しい枠のオーダーは 1 件ずつ INSERT しない（業務も 1 件ごとに読み直さない）。作成の記録も 1 件ずつ付く"""
    code = new_client_code("I")
    client_repo.create(db, obj_in=ClientCreate(code=code, name="batching client"))
    template_id = _daily(db, _biz(db, code, 1), "Daily")
    plan_services.update_template(db, template_id, PlanTemplateUpdate(rrule="FREQ=WEEKLY;BYDAY=MO,WE,FR"))
    before = {o.id for o in open_orders(db, template_id)}

    with StatementCounter() as counter:
        plan_services.update_template(db, template_id, PlanTemplateUpdate(rrule="FREQ=DAILY"))

    new = [o for o in open_orders(db, template_id) if o.id not in before]
    assert len(new) > 200
    events = db.scalars(select(OpeOrderEvent).where(
        OpeOrderEvent.order_id.in_([o.id for o in new]), OpeOrderEvent.event_type == OrderEventType.CREATED,
    )).all()
    assert sorted(e.order_id for e in events) == sorted(o.id for o in new)
    assert all(o.deadline_at is not None and o.client_code == code for o in new)
    assert counter.count < 60, f"{counter.count} statements for {len(new)} new orders (one INSERT per order again?)"


@pytest.mark.parametrize("side", ["downstream", "upstream"])
def test_new_orders_get_their_links_with_a_handful_of_statements(db, side):
    """依存関係のある業務でも、新しいオーダーのつながりを 1 件ずつ直さない（定義ごとに数回の問い合わせ）"""
    code = new_client_code("I")
    client_repo.create(db, obj_in=ClientCreate(code=code, name="batching client"))
    upstream_biz, downstream_biz = _biz(db, code, 1), _biz(db, code, 2)
    upstream = _daily(db, upstream_biz, "Upstream " + uuid.uuid4().hex[:4])
    downstream = _daily(db, downstream_biz, "Downstream " + uuid.uuid4().hex[:4])
    biz_services.create_dependency(
        db, downstream_biz, BizDependencyCreate(upstream_biz_id=upstream_biz, match_rule=DependencyMatchRule.SAME_OCCURRENCE_DATE),
    )
    changed = downstream if side == "downstream" else upstream
    plan_services.update_template(db, changed, PlanTemplateUpdate(rrule="FREQ=WEEKLY;BYDAY=MO,WE,FR"))

    with StatementCounter() as counter:
        plan_services.update_template(db, changed, PlanTemplateUpdate(rrule="FREQ=DAILY"))

    # 下流のオーダーはそれぞれ、同じ日の取消されていない上流を待つ
    ups = {o.occurrence_date: o.id for o in open_orders(db, upstream)}
    downs = open_orders(db, downstream)
    linked = {
        link.downstream_order_id: link.upstream_order_id
        for link in db.scalars(select(OpeDependency).where(
            OpeDependency.downstream_order_id.in_([o.id for o in downs]), OpeDependency.removed_at.is_(None),
        ))
    }
    assert len(downs) > 350 and all(linked[o.id] == ups[o.occurrence_date] for o in downs)
    assert counter.count < 60, f"{counter.count} statements ({side} side: links resolved per order?)"


@pytest.mark.parametrize("side", ["none", "downstream", "upstream"])
def test_a_rule_change_cancels_hundreds_of_orders_with_a_handful_of_statements(db, side):
    """取消も 1 件ずつ書き出さない（依存関係のつながりは最後にまとめて直す）。取消済みの上流を待つ下流は、それを指したまま"""
    code = new_client_code("I")
    client_repo.create(db, obj_in=ClientCreate(code=code, name="batching client"))
    upstream_biz, downstream_biz = _biz(db, code, 1), _biz(db, code, 2)
    upstream = _daily(db, upstream_biz, "Upstream " + uuid.uuid4().hex[:4])
    downstream = _daily(db, downstream_biz, "Downstream " + uuid.uuid4().hex[:4])
    if side != "none":
        biz_services.create_dependency(
            db, downstream_biz, BizDependencyCreate(upstream_biz_id=upstream_biz, match_rule=DependencyMatchRule.SAME_OCCURRENCE_DATE),
        )
    changed = upstream if side == "upstream" else downstream
    before = {o.id for o in open_orders(db, changed)}

    with StatementCounter() as counter:
        plan_services.update_template(db, changed, PlanTemplateUpdate(rrule="FREQ=WEEKLY;BYDAY=MO,WE,FR"))

    db.expire_all()
    cancelled = db.scalars(select(OpeOrder).where(OpeOrder.id.in_(before), OpeOrder.status == OrderStatus.CANCELED)).all()
    assert len(cancelled) > 200
    if side == "upstream":
        waiting = {
            link.upstream_order_id
            for link in db.scalars(select(OpeDependency).where(
                OpeDependency.upstream_order_id.in_([o.id for o in cancelled]), OpeDependency.removed_at.is_(None),
            ))
        }
        assert waiting == {o.id for o in cancelled}  # 「上流が取消」のまま待つ（人が判断する）
    assert counter.count < 60, f"{counter.count} statements for {len(cancelled)} cancelled orders ({side})"


@pytest.mark.parametrize("side", ["downstream", "upstream"])
def test_a_time_change_with_latest_before_relinks_hundreds_of_orders_with_a_handful_of_statements(db, side):
    """
    「直前の上流」のつながりは時刻で決まるので、時刻が動くと直す。以前は動いたオーダーごとに直していた（1 件に数文）。
    下流 9:00 / 上流 9:00 の毎日 → 片方の時刻を動かすと、下流はそれぞれ「前の日の上流」を待つようになる
    """
    code = new_client_code("I")
    client_repo.create(db, obj_in=ClientCreate(code=code, name="batching client"))
    upstream_biz, downstream_biz = _biz(db, code, 1), _biz(db, code, 2)
    upstream = _daily(db, upstream_biz, "Upstream " + uuid.uuid4().hex[:4])
    downstream = _daily(db, downstream_biz, "Downstream " + uuid.uuid4().hex[:4])
    biz_services.create_dependency(db, downstream_biz, BizDependencyCreate(
        upstream_biz_id=upstream_biz, match_rule=DependencyMatchRule.LATEST_BEFORE, lookback_days=2,
    ))
    changed, new_time = (downstream, time(8, 0)) if side == "downstream" else (upstream, time(10, 0))

    with StatementCounter() as counter:
        plan_services.update_template(db, changed, PlanTemplateUpdate(start_time=new_time))

    db.expire_all()
    # 直前の上流: 下流の予定開始以前で最新の上流のオーダー。取消済みも候補（人が判断するまで待つ）。
    # 同じ時刻なら id の大きいもの
    ups = sorted(db.scalars(select(OpeOrder).where(OpeOrder.template_id == upstream)), key=lambda o: (o.scheduled_start, o.id))
    # 時刻の変更で取り消された上流は無い（範囲の最後の日の枠も動く: 生成済みの範囲の境目 9:00 をまたいでも）
    assert [u.occurrence_date for u in ups if u.status == OrderStatus.CANCELED] == []
    downs = open_orders(db, downstream)
    linked = {
        link.downstream_order_id: link.upstream_order_id
        for link in db.scalars(select(OpeDependency).where(
            OpeDependency.downstream_order_id.in_([o.id for o in downs]), OpeDependency.removed_at.is_(None),
        ))
    }

    def expected(down: OpeOrder) -> int | None:
        before = [u for u in ups if u.scheduled_start <= down.scheduled_start]
        if not before or before[-1].scheduled_start < down.scheduled_start - timedelta(days=2):
            return None
        return before[-1].id

    assert len(downs) > 350
    wrong = [(o.id, linked.get(o.id), expected(o)) for o in downs if linked.get(o.id) != expected(o)]
    assert not wrong, wrong[:5]
    # 前の日の上流を待つ（同じ日の上流は、下流より後に始まる）
    assert all(
        next(u for u in ups if u.id == linked[o.id]).scheduled_start.astimezone(JST).date()
        == o.scheduled_start.astimezone(JST).date() - timedelta(days=1)
        for o in downs
    )
    print(f"\n[{side}] {counter.count} statements for {len(downs)} relinked orders")
    assert counter.count < 60, f"{counter.count} statements for {len(downs)} moved orders ({side}: links refreshed per order?)"
