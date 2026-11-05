# tests/test_order_deadlines.py
"""
オーダーの締切（N.2）のテスト: 締切はオーダーの行に保存する（作成時にテンプレートの決め方を写す）。
- 予定の終了 / 開始から / 固定の時刻 と「まもなく」の開始（warn_at）
- 予定が動いたら、オーダー自身の写した決め方で計算し直す（固定の時刻は同じ日の中なら動かない）
- テンプレートを後から変えても、作成済みのオーダーの写しは変わらない（リリース前の写し直しは N.3）
- 完了したら finished_at（完了の記録と同じ時刻）

※ テストごとに一意な顧客コードを使ってデータを分離しています。
"""
import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.domain_biz.schemas import BizMasterCreate
from src.domain_client.schemas import ClientCreate
from src.domain_ope import services as ope
from src.domain_ope.models import OpeOrder, OpeOrderEvent, OrderStatus
from src.domain_plan import services as plan_services
from src.domain_plan.models import DeadlineMode, TemplateType
from src.domain_plan.schemas import PlanInstanceOverride, PlanTemplateUpdate, RRuleTemplateCreate
from src.domain_staff import services as staff_services
from src.domain_staff.models import RoleCode, StaffStatus
from src.domain_staff.schemas import StaffCreate
from src.infrastructure.database import SessionLocal
from src.infrastructure.repositories import biz_master_repo, client_repo

JST = ZoneInfo("Asia/Tokyo")


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def biz_id(db):
    code = "Y" + uuid.uuid4().hex[:3].upper()  # 他のテストの顧客とコードが衝突しないよう、別の先頭文字を使う
    client_repo.create(db, obj_in=ClientCreate(code=code, name="deadline client"))
    return biz_master_repo.create(db, obj_in=BizMasterCreate(client_code=code, name="deadline biz", business_seq=1, manager_email=None, description=None)).id


@pytest.fixture
def staff(db):
    def make(*roles: RoleCode) -> int:
        return staff_services.create_staff(
            db, StaffCreate(login_id="y-" + uuid.uuid4().hex[:10], name="deadline actor", roles=list(roles)),
            actor=None, status=StaffStatus.ACTIVE,
        ).id
    return {"releaser": make(RoleCode.RELEASER), "operator": make(RoleCode.OPERATOR)}


def daily(db, biz_id, **kwargs):
    data = dict(
        biz_master_id=biz_id, name="Daily", template_type=TemplateType.RRULE, timezone="Asia/Tokyo",
        start_time=time(9, 0), duration_minutes=60, anchor_date=date(2026, 1, 1), rrule="FREQ=DAILY",
    )
    data.update(kwargs)
    return plan_services.create_template(db, RRuleTemplateCreate.model_validate(data))


def orders_of(db, template_id: int) -> list[OpeOrder]:
    db.expire_all()
    return list(db.scalars(select(OpeOrder).where(OpeOrder.template_id == template_id).order_by(OpeOrder.scheduled_start)))


def local_day(dt: datetime) -> date:
    return dt.astimezone(JST).date()


def test_default_deadline_is_the_scheduled_end(db, biz_id):
    t = daily(db, biz_id)
    for o in orders_of(db, t.id)[:5]:
        assert (o.deadline_mode, o.deadline_at, o.warn_at, o.finished_at) == (DeadlineMode.END, o.scheduled_end, None, None)
        assert o.deadline_timezone == "Asia/Tokyo"


def test_offset_deadline_and_warning(db, biz_id):
    t = daily(db, biz_id, deadline_mode=DeadlineMode.OFFSET, deadline_offset_minutes=300, warn_before_minutes=30)
    for o in orders_of(db, t.id)[:5]:
        assert o.deadline_at == o.scheduled_start + timedelta(minutes=300)  # 所要時間（60 分）を超えてよい
        assert o.warn_at == o.deadline_at - timedelta(minutes=30)


def test_fixed_time_deadline_follows_the_day_not_the_hour(db, biz_id, staff):
    t = daily(db, biz_id, deadline_mode=DeadlineMode.FIXED_TIME, deadline_time=time(15, 0))
    o = orders_of(db, t.id)[3]
    day = local_day(o.scheduled_start)
    assert o.deadline_at == datetime.combine(day, time(15, 0), tzinfo=JST)

    # 同じ日の 11:00 に動かす → 締切は 15:00 のまま（外部の締切は動かない）
    plan_services.override_instance(db, o.current_instance_id, PlanInstanceOverride(
        scheduled_start=datetime.combine(day, time(11, 0), tzinfo=JST),
    ), actor_staff_id=staff["releaser"])
    db.expire_all()
    moved = db.get(OpeOrder, o.id)
    assert moved is not None and moved.deadline_at == datetime.combine(day, time(15, 0), tzinfo=JST)

    # 翌々日に動かす → その日の 15:00
    plan_services.override_instance(db, moved.current_instance_id, PlanInstanceOverride(
        scheduled_start=datetime.combine(day + timedelta(days=2), time(10, 0), tzinfo=JST),
    ), actor_staff_id=staff["releaser"])
    db.expire_all()
    moved = db.get(OpeOrder, o.id)
    assert moved is not None and moved.deadline_at == datetime.combine(day + timedelta(days=2), time(15, 0), tzinfo=JST)


def test_overnight_fixed_deadline(db, biz_id):
    t = daily(db, biz_id, start_time=time(22, 0), deadline_mode=DeadlineMode.FIXED_TIME, deadline_time=time(6, 0), deadline_day_offset=1)
    o = orders_of(db, t.id)[2]
    assert o.deadline_at == datetime.combine(local_day(o.scheduled_start) + timedelta(days=1), time(6, 0), tzinfo=JST)


def test_template_changes_do_not_rewrite_existing_orders(db, biz_id):
    t = daily(db, biz_id)
    before = {o.id: o.deadline_at for o in orders_of(db, t.id)}
    plan_services.update_template(db, t.id, PlanTemplateUpdate(deadline_mode=DeadlineMode.OFFSET, deadline_offset_minutes=600))
    after = {o.id: (o.deadline_mode, o.deadline_at) for o in orders_of(db, t.id) if o.id in before}
    assert all(mode == DeadlineMode.END and at == before[oid] for oid, (mode, at) in after.items())


def test_finished_at_is_stored_with_the_finish_event(db, biz_id, staff, monkeypatch):
    t = daily(db, biz_id)
    o = orders_of(db, t.id)[2]
    ope.release_order(db, o.id, staff["releaser"])
    monkeypatch.setattr(ope, "_now", lambda: o.scheduled_start)
    ope.start_order(db, o.id, staff["operator"])
    ope.finish_order(db, o.id, staff["operator"], "完了")
    db.expire_all()
    done = db.get(OpeOrder, o.id)
    assert done is not None and done.status == OrderStatus.FINISHED and done.finished_at is not None
    finish_event = db.scalars(select(OpeOrderEvent).where(
        OpeOrderEvent.order_id == o.id, OpeOrderEvent.to_status == OrderStatus.FINISHED,
    )).one()
    assert done.finished_at == finish_event.occurred_at


def test_db_requires_finished_at_exactly_for_finished_orders(db, biz_id):
    t = daily(db, biz_id)
    o = orders_of(db, t.id)[0]
    o.finished_at = o.scheduled_end  # 完了していないのに完了日時
    with pytest.raises(IntegrityError, match="ck_ope_order_finished_at"):
        db.flush()
    db.rollback()
