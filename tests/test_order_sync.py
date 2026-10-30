# tests/test_order_sync.py
"""
plan → オーダーの追従（4.3: coordination.order_sync）のテスト。
設計書の「Propagating plan changes」の表を1行ずつ確かめる。受け手は conftest.py で登録済み（アプリと同じ）。

※ テストごとに一意な顧客コードを使ってデータを分離しています。
"""
import uuid
from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import select

from src.coordination import order_sync
from src.core.error_codes import ErrorCode
from src.core.exceptions import DuplicatedRecordError
from src.domain_biz.schemas import BizMasterCreate
from src.domain_client.schemas import ClientCreate
from src.domain_ope.models import EventSource, OpeOrder, OpeOrderEvent, OrderEventType, OrderStatus
from src.domain_plan import services as plan_services
from src.domain_plan import slot_events
from src.domain_plan.models import TemplateType
from src.domain_plan.schemas import (
    PlanInstanceCancel, PlanInstanceOverride, PlanInstanceRevert, PlanOneOffCreate, PlanTemplateUpdate, RRuleTemplateCreate,
)
from src.domain_staff import services as staff_services
from src.domain_staff.models import RoleCode, StaffStatus
from src.domain_staff.schemas import StaffCreate
from src.infrastructure.database import SessionLocal
from src.infrastructure.repositories import biz_master_repo, client_repo, ope_order_repo

UTC = timezone.utc


# ==========================================
# Fixtures / Helpers
# ==========================================
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
    # 他のテストの顧客とコードが衝突しないよう、別の先頭文字を使う
    code = "S" + uuid.uuid4().hex[:3].upper()
    client_repo.create(db, obj_in=ClientCreate(code=code, name="sync test client"))
    return biz_master_repo.create(
        db, obj_in=BizMasterCreate(client_code=code, name="sync biz", business_seq=1, manager_email=None, description=None)
    ).id


@pytest.fixture
def staff(db):
    return staff_services.create_staff(
        db, StaffCreate(login_id="s-" + uuid.uuid4().hex[:10], name="sync tester", roles=[RoleCode.OPERATOR]),
        actor=None, status=StaffStatus.ACTIVE,
    )


def daily(db, biz_id, **kwargs):
    data = dict(
        biz_master_id=biz_id, name="Daily", template_type=TemplateType.RRULE, timezone="Asia/Tokyo",
        start_time=time(9, 0), duration_minutes=60, anchor_date=date(2026, 1, 1), rrule="FREQ=DAILY",
    )
    data.update(kwargs)
    return plan_services.create_template(db, RRuleTemplateCreate.model_validate(data))


def live(db, template_id):
    return sorted(
        (i for i in plan_services.get_instances(db, template_id, limit=1000) if not i.voided), key=lambda i: i.scheduled_start
    )


def orders(db, template_id, *, include_canceled=False) -> list[OpeOrder]:
    db.expire_all()
    stmt = select(OpeOrder).where(OpeOrder.template_id == template_id).order_by(OpeOrder.scheduled_start, OpeOrder.id)
    return [o for o in db.scalars(stmt) if include_canceled or o.status != OrderStatus.CANCELED]


def order_of(db, instance_id) -> OpeOrder:
    db.expire_all()
    return db.scalars(select(OpeOrder).where(OpeOrder.current_instance_id == instance_id, OpeOrder.status != OrderStatus.CANCELED)).one()


def events(db, order) -> list[OpeOrderEvent]:
    return ope_order_repo.get_events(db, order_id=order.id)


def set_status(db, order, status, before=None):
    """4.4（状態の操作）の代わりに、テストではオーダーの状態を直接変える"""
    order = db.get(OpeOrder, order.id)
    if status in (OrderStatus.READY, OrderStatus.IN_PROGRESS) or before in (OrderStatus.READY, OrderStatus.IN_PROGRESS):
        order.released_at = datetime.now(UTC)
    order.status = status
    order.status_before_hold = before
    db.commit()
    return order


# ==========================================
# 新しい枠 → 新しい pending オーダー
# ==========================================
def test_every_future_occurrence_gets_one_pending_order(db, biz_id):
    t = daily(db, biz_id)
    instances, os = live(db, t.id), orders(db, t.id)
    assert len(os) == len(instances) > 80
    assert [o.current_instance_id for o in os] == [i.id for i in instances]
    first = os[0]
    assert first.status == OrderStatus.PENDING and first.client_code and first.biz_master_id == biz_id
    assert first.occurrence_date == instances[0].occurrence_date and first.source_sheet_event_id is None
    [created] = events(db, first)
    assert created.event_type == OrderEventType.CREATED and created.to_status == OrderStatus.PENDING
    assert created.source == EventSource.SYSTEM and created.actor_staff_id is None


# ==========================================
# 人が日時を変更・取消
# ==========================================
def test_person_reschedule_and_cancel(db, biz_id, staff):
    t = daily(db, biz_id)
    inst = live(db, t.id)
    moved_from, canceled_inst, held_inst = inst[3], inst[5], inst[7]

    # 日時の変更 → オーダーが追従（記録は職員・理由付き）
    ov = plan_services.override_instance(db, moved_from.id, PlanInstanceOverride(
        scheduled_start=moved_from.scheduled_start + timedelta(hours=2), note="顧客都合で11時に",
    ), actor_staff_id=staff.id)
    order = order_of(db, ov.id)
    assert order.scheduled_start == ov.scheduled_start and order.occurrence_date == moved_from.occurrence_date
    resched = events(db, order)[-1]
    assert resched.event_type == OrderEventType.RESCHEDULED and resched.source == EventSource.HUMAN
    assert resched.actor_staff_id == staff.id and resched.reason == "顧客都合で11時に"
    assert (resched.detail or {})["before"]["instance_id"] == moved_from.id and (resched.detail or {})["after"]["instance_id"] == ov.id

    # 取消 → オーダーも取消（理由付き）。保留中のオーダーも同じ
    canceled_order = order_of(db, canceled_inst.id)
    plan_services.cancel_instance(db, canceled_inst.id, PlanInstanceCancel(note="今月は無し"), actor_staff_id=staff.id)
    db.expire_all()
    canceled_order = db.get(OpeOrder, canceled_order.id)
    assert canceled_order.status == OrderStatus.CANCELED
    last = events(db, canceled_order)[-1]
    assert (last.from_status, last.to_status, last.reason, last.actor_staff_id) == (
        OrderStatus.PENDING, OrderStatus.CANCELED, "今月は無し", staff.id,
    )
    held = set_status(db, order_of(db, held_inst.id), OrderStatus.ON_HOLD, before=OrderStatus.READY)
    plan_services.cancel_instance(db, held_inst.id, PlanInstanceCancel(), actor_staff_id=staff.id)
    db.expire_all()
    held = db.get(OpeOrder, held.id)
    assert held.status == OrderStatus.CANCELED and held.status_before_hold is None

    # 職員の分からない呼び出し（スクリプト等）は、人の変更でもシステムの記録になる
    other = inst[9]
    plan_services.cancel_instance(db, other.id, PlanInstanceCancel(note="x"))
    db.expire_all()
    rec = events(db, db.scalars(select(OpeOrder).where(OpeOrder.current_instance_id == other.id)).one())[-1]
    assert rec.source == EventSource.SYSTEM and rec.actor_staff_id is None and (rec.detail or {})["cause"] == "manual plan change"


def test_person_cannot_change_an_order_in_progress(db, biz_id, staff):
    t = daily(db, biz_id)
    target = live(db, t.id)[4]
    order = set_status(db, order_of(db, target.id), OrderStatus.IN_PROGRESS)

    # 作業中のオーダーの予定は、人が変更・取消できない（plan の変更ごと取り消される）
    with pytest.raises(DuplicatedRecordError) as e:
        plan_services.override_instance(db, target.id, PlanInstanceOverride(
            scheduled_start=target.scheduled_start + timedelta(hours=1),
        ), actor_staff_id=staff.id)
    assert e.value.error_code == ErrorCode.ORDER_IN_PROGRESS
    db.rollback()
    with pytest.raises(DuplicatedRecordError):
        plan_services.cancel_instance(db, target.id, PlanInstanceCancel(), actor_staff_id=staff.id)
    db.rollback()
    assert target.id in {i.id for i in live(db, t.id)}  # plan も変わっていない
    db.expire_all()
    assert db.get(OpeOrder, order.id).status == OrderStatus.IN_PROGRESS


# ==========================================
# システムが枠を動かした・枠が消えた
# ==========================================
def test_system_move_follows_or_flags_in_progress(db, biz_id):
    t = daily(db, biz_id)
    inst = live(db, t.id)
    pending, held, working = order_of(db, inst[3].id), order_of(db, inst[4].id), order_of(db, inst[5].id)
    held = set_status(db, held, OrderStatus.ON_HOLD, before=OrderStatus.PENDING)
    working = set_status(db, working, OrderStatus.IN_PROGRESS)
    working_start = working.scheduled_start

    # テンプレートの時刻を変える → 全ての枠が動く
    plan_services.update_template(db, t.id, PlanTemplateUpdate(start_time=time(14, 0)))
    db.expire_all()
    pending, held, working = (db.get(OpeOrder, o.id) for o in (pending, held, working))

    assert pending.scheduled_start.astimezone(timezone(timedelta(hours=9))).hour == 14
    rec = events(db, pending)[-1]
    assert rec.event_type == OrderEventType.RESCHEDULED and rec.source == EventSource.SYSTEM
    assert (rec.detail or {})["plan_reasons"] == ["template_revised"]
    # 保留中は追従して保留のまま
    assert held.status == OrderStatus.ON_HOLD and held.scheduled_start.astimezone(timezone(timedelta(hours=9))).hour == 14
    # 作業中は動かさず、矛盾として残す
    assert working.status == OrderStatus.IN_PROGRESS and working.scheduled_start == working_start
    assert working.conflict and "作業中" in (working.conflict_reason or "")
    assert events(db, working)[-1].event_type == OrderEventType.CONFLICT_RAISED


def test_slot_disappears_cancels_pending_and_flags_held_and_working(db, biz_id):
    t = daily(db, biz_id)
    inst = live(db, t.id)
    pending, ready, held, working = (order_of(db, inst[i].id) for i in (3, 4, 5, 6))
    ready = set_status(db, ready, OrderStatus.READY)
    held = set_status(db, held, OrderStatus.ON_HOLD, before=OrderStatus.READY)
    working = set_status(db, working, OrderStatus.IN_PROGRESS)

    plan_services.delete_template(db, t.id)
    db.expire_all()
    pending, ready, held, working = (db.get(OpeOrder, o.id) for o in (pending, ready, held, working))

    # pending / ready はシステムが取消（plan の理由付き）
    for o in (pending, ready):
        assert o.status == OrderStatus.CANCELED
        rec = events(db, o)[-1]
        assert rec.source == EventSource.SYSTEM and rec.reason == "template_deleted"
    # 保留中・作業中は自動で取り消さず、矛盾フラグ
    for o, status in ((held, OrderStatus.ON_HOLD), (working, OrderStatus.IN_PROGRESS)):
        assert o.status == status and o.conflict and "template_deleted" in (o.conflict_reason or "")


# ==========================================
# 臨時の予定・戻ってきた枠
# ==========================================
def test_one_off_gets_its_own_order_and_keeps_it_when_moved(db, biz_id, staff):
    t = daily(db, biz_id)
    rule = live(db, t.id)[3]
    oo = plan_services.add_one_off_instance(db, t.id, PlanOneOffCreate(
        scheduled_start=rule.scheduled_start + timedelta(hours=4),
    ), actor_staff_id=staff.id)
    order = order_of(db, oo.id)
    assert order.one_off_instance_id == oo.id and order.occurrence_date == rule.occurrence_date
    assert events(db, order)[0].source == EventSource.HUMAN and events(db, order)[0].actor_staff_id == staff.id
    assert order_of(db, rule.id).id != order.id  # 同じ日のルールの予定とは別のオーダー

    moved = plan_services.override_instance(db, oo.id, PlanInstanceOverride(
        scheduled_start=oo.scheduled_start + timedelta(hours=1),
    ), actor_staff_id=staff.id)
    db.expire_all()
    assert db.get(OpeOrder, order.id).current_instance_id == moved.id  # 同じ枠（最初のインスタンス）のまま


def test_returning_slot_gets_a_new_order(db, biz_id, staff):
    t = daily(db, biz_id)
    inst = live(db, t.id)
    restore_target, revert_target = inst[3], inst[5]
    first_order = order_of(db, restore_target.id)

    # 取り消して「元に戻す」→ 取消済みのオーダーは残り、新しいオーダーができる
    plan_services.cancel_instance(db, restore_target.id, PlanInstanceCancel(), actor_staff_id=staff.id)
    restored = plan_services.restore_cancelled_instance(db, restore_target.id, actor_staff_id=staff.id)
    new_order = order_of(db, restored.id)
    db.expire_all()
    assert new_order.id != first_order.id and db.get(OpeOrder, first_order.id).status == OrderStatus.CANCELED
    assert new_order.status == OrderStatus.PENDING

    # 取り消して「ルールに戻す」→ 同じく新しいオーダー
    plan_services.cancel_instance(db, revert_target.id, PlanInstanceCancel(), actor_staff_id=staff.id)
    result = plan_services.revert_instance(db, revert_target.id, PlanInstanceRevert(), actor_staff_id=staff.id)
    assert result.restored_instance is not None
    slot_orders = [o for o in orders(db, t.id, include_canceled=True) if o.occurrence_date == revert_target.occurrence_date]
    assert sorted(o.status for o in slot_orders) == sorted([OrderStatus.CANCELED, OrderStatus.PENDING])


# ==========================================
# 運用開始時のオーダー作成
# ==========================================
def test_create_missing_orders_backfills_future_only_and_is_idempotent(db, biz_id, monkeypatch):
    # 受け手を登録していない状態（運用開始前）で予定を作る
    monkeypatch.setattr(slot_events, "_listeners", [])
    t = daily(db, biz_id)
    monkeypatch.undo()
    assert orders(db, t.id) == []

    created = order_sync.create_missing_orders(db)
    assert created >= len(live(db, t.id))
    assert len(orders(db, t.id)) == len(live(db, t.id))
    assert all(o.scheduled_start >= datetime.now(UTC) - timedelta(minutes=5) for o in orders(db, t.id))
    assert events(db, orders(db, t.id)[0])[0].detail == {"cause": "initial order creation"}
    assert order_sync.create_missing_orders(db) == 0  # 何度実行しても同じ


def test_plan_manual_endpoints_require_an_acting_staff():
    from main import app

    paths = app.openapi()["paths"]
    for path in ("/plans/instances/{instance_id}/override", "/plans/instances/{instance_id}/cancel",
                 "/plans/instances/{instance_id}/revert", "/plans/instances/{instance_id}/restore",
                 "/plans/templates/{template_id}/instances"):
        params = {p["name"] for p in paths[path]["post"].get("parameters", [])}
        assert "x-staff-id" in params, path
    # 参照・テンプレートの設定は対象外（変更なし）
    assert "x-staff-id" not in {p["name"] for p in paths["/plans/clients/{client_code}/calendar"]["get"].get("parameters", [])}
