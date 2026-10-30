# tests/test_order_actions.py
"""
オーダーの状態の操作（4.4: domain_ope.services）のテスト。
オーダーは plan の予定から自動で作られる（4.3。受け手は conftest.py で登録済み）。

※ テストごとに一意な顧客コードを使ってデータを分離しています。
"""
import uuid
from datetime import date, datetime, time, timedelta, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from src.core.exceptions import BadRequestError, DuplicatedRecordError
from src.domain_biz.schemas import BizMasterCreate
from src.domain_client.schemas import ClientCreate
from src.domain_ope import services as ope
from src.domain_ope.models import EventSource, OpeOrder, OrderEventType, OrderStatus
from src.domain_ope.schemas import ConflictAction, ConflictResolveIn, OrderReasonIn
from src.domain_plan import services as plan_services
from src.domain_plan.models import TemplateType
from src.domain_plan.schemas import PlanTemplateUpdate, RRuleTemplateCreate
from src.domain_staff import services as staff_services
from src.domain_staff.models import RoleCode, StaffStatus
from src.domain_staff.schemas import StaffCreate
from src.infrastructure.database import SessionLocal
from src.infrastructure.repositories import biz_master_repo, client_repo

JST = timezone(timedelta(hours=9))


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
def setup(db):
    """顧客・業務・毎日 9:00 の予定（オーダーは自動で作られる）と職員"""
    code = "A" + uuid.uuid4().hex[:3].upper()  # 他のテストの顧客とコードが衝突しないよう、別の先頭文字を使う
    client_repo.create(db, obj_in=ClientCreate(code=code, name="actions client"))
    biz = biz_master_repo.create(db, obj_in=BizMasterCreate(client_code=code, name="actions biz", business_seq=1, manager_email=None, description=None))
    template = plan_services.create_template(db, RRuleTemplateCreate(
        biz_master_id=biz.id, name="Daily", template_type=TemplateType.RRULE, timezone="Asia/Tokyo",
        start_time=time(9, 0), duration_minutes=60, anchor_date=date(2026, 1, 1), rrule="FREQ=DAILY",
    ))

    def staff(role):
        return staff_services.create_staff(
            db, StaffCreate(login_id=f"{role.value}-" + uuid.uuid4().hex[:8], name=role.value, roles=[role]),
            actor=None, status=StaffStatus.ACTIVE,
        ).id

    db.expire_all()
    orders = list(db.scalars(
        select(OpeOrder).where(OpeOrder.template_id == template.id).order_by(OpeOrder.scheduled_start)
    ))
    return {"template_id": template.id, "orders": orders, "operator": staff(RoleCode.OPERATOR),
            "releaser": staff(RoleCode.RELEASER), "admin": staff(RoleCode.ADMIN)}


def pretend_today(monkeypatch, day: date, hour: int = 8):
    """オーダーの操作の「今」（日本時間）をずらす"""
    monkeypatch.setattr(ope, "_now", lambda: datetime.combine(day, time(hour), tzinfo=JST))


def day_of(order) -> date:
    return order.scheduled_start.astimezone(JST).date()


# ==========================================
# 状態の遷移
# ==========================================
def test_happy_path_release_start_hold_resume_finish(db, setup, monkeypatch):
    s = setup
    order = s["orders"][5]
    pretend_today(monkeypatch, day_of(order))

    r = ope.release_order(db, order.id, s["releaser"], comment="SOP 確認済み")
    assert r.status == OrderStatus.READY and r.released_at is not None
    r = ope.start_order(db, order.id, s["operator"])
    assert r.status == OrderStatus.IN_PROGRESS
    r = ope.hold_order(db, order.id, s["operator"], reason="顧客の回答待ち")
    assert r.status == OrderStatus.ON_HOLD and r.status_before_hold == OrderStatus.IN_PROGRESS
    r = ope.resume_order(db, order.id, s["operator"])
    assert r.status == OrderStatus.IN_PROGRESS and r.status_before_hold is None  # 保留前の状態に戻る
    r = ope.finish_order(db, order.id, s["operator"], comment="完了")
    assert r.status == OrderStatus.FINISHED

    changes = [(e.from_status, e.to_status, e.actor_staff_id) for e in r.events if e.event_type == OrderEventType.STATUS_CHANGED]
    assert changes == [
        (OrderStatus.PENDING, OrderStatus.READY, s["releaser"]),
        (OrderStatus.READY, OrderStatus.IN_PROGRESS, s["operator"]),
        (OrderStatus.IN_PROGRESS, OrderStatus.ON_HOLD, s["operator"]),
        (OrderStatus.ON_HOLD, OrderStatus.IN_PROGRESS, s["operator"]),
        (OrderStatus.IN_PROGRESS, OrderStatus.FINISHED, s["operator"]),
    ]
    assert all(e.source == EventSource.HUMAN for e in r.events if e.event_type == OrderEventType.STATUS_CHANGED)
    assert r.events[0].event_type == OrderEventType.CREATED  # 4.3 の作成の記録が先頭
    assert [e.reason for e in r.events][1:4] == ["SOP 確認済み", None, "顧客の回答待ち"]


def test_invalid_transitions_are_refused(db, setup):
    s = setup
    order = s["orders"][6]
    with pytest.raises(BadRequestError, match="pending"):
        ope.start_order(db, order.id, s["operator"])  # リリース前は開始できない
    with pytest.raises(BadRequestError):
        ope.finish_order(db, order.id, s["operator"])
    with pytest.raises(BadRequestError):
        ope.resume_order(db, order.id, s["operator"])
    ope.release_order(db, order.id, s["releaser"])
    with pytest.raises(BadRequestError):
        ope.release_order(db, order.id, s["releaser"])  # 二重のリリース
    with pytest.raises(ValidationError):
        OrderReasonIn(reason="  ")  # 保留・取消は理由必須


# ==========================================
# 予定日より前の開始
# ==========================================
def test_early_start_needs_a_releasers_approval(db, setup, monkeypatch):
    s = setup
    order = s["orders"][7]
    pretend_today(monkeypatch, day_of(order) - timedelta(days=2))
    ope.release_order(db, order.id, s["releaser"])

    with pytest.raises(BadRequestError, match="releaser's approval"):
        ope.start_order(db, order.id, s["operator"])
    r = ope.approve_early_start(db, order.id, s["releaser"], comment="前倒し対応")
    assert r.early_start_approved and r.events[-1].event_type == OrderEventType.EARLY_START_APPROVED
    with pytest.raises(DuplicatedRecordError):
        ope.approve_early_start(db, order.id, s["releaser"])
    r = ope.start_order(db, order.id, s["operator"])
    assert r.status == OrderStatus.IN_PROGRESS and r.events[-1].detail == {"early_start": True}

    # 予定日当日は承認不要（承認しようとすると 400）
    today_order = s["orders"][8]
    pretend_today(monkeypatch, day_of(today_order))
    with pytest.raises(BadRequestError, match="no approval is needed"):
        ope.approve_early_start(db, today_order.id, s["releaser"])
    ope.release_order(db, today_order.id, s["releaser"])
    assert ope.start_order(db, today_order.id, s["operator"]).status == OrderStatus.IN_PROGRESS


# ==========================================
# 取消（plan の予定も取り消す）
# ==========================================
def test_cancel_also_cancels_the_plan_occurrence(db, setup, monkeypatch):
    s = setup
    pending, working = s["orders"][9], s["orders"][10]
    pretend_today(monkeypatch, day_of(working))
    ope.release_order(db, working.id, s["releaser"])
    ope.start_order(db, working.id, s["operator"])

    for order in (pending, working):  # 作業中のオーダーも、オーダーからなら取り消せる
        r = ope.cancel_order(db, order.id, s["admin"], reason="契約終了")
        assert r.status == OrderStatus.CANCELED and r.events[-1].reason == "契約終了"
        instance = next(i for i in plan_services.get_instances(db, s["template_id"], limit=1000) if i.id == order.current_instance_id)
        assert instance.voided and instance.void_note == "契約終了"  # カレンダーからも消える

    # plan の取消で同じ枠に新しいオーダーが作られていない
    db.expire_all()
    slot_orders = list(db.scalars(select(OpeOrder).where(
        OpeOrder.template_id == s["template_id"], OpeOrder.occurrence_date.in_([pending.occurrence_date, working.occurrence_date]),
    )))
    assert sorted(o.status for o in slot_orders) == [OrderStatus.CANCELED, OrderStatus.CANCELED]
    with pytest.raises(BadRequestError):
        ope.cancel_order(db, pending.id, s["admin"], reason="again")


# ==========================================
# 矛盾（plan が作業中・保留中の枠を動かした）
# ==========================================
def _conflicted(db, s, monkeypatch, index):
    order = s["orders"][index]
    pretend_today(monkeypatch, day_of(order))
    ope.release_order(db, order.id, s["releaser"])
    ope.start_order(db, order.id, s["operator"])
    ope.hold_order(db, order.id, s["operator"], reason="確認待ち")
    return order


def test_conflict_blocks_start_and_can_be_resolved(db, setup, monkeypatch):
    s = setup
    keep, accept, cancel = (_conflicted(db, s, monkeypatch, i) for i in (11, 12, 13))
    # 作業中に plan の時刻が変わる → 保留中のオーダーは追従、作業中なら矛盾（ここでは作業中に戻して矛盾を作る）
    for o in (keep, accept, cancel):
        ope.resume_order(db, o.id, s["operator"])
    plan_services.update_template(db, s["template_id"], PlanTemplateUpdate(start_time=time(15, 0)))
    db.expire_all()
    for o in (keep, accept, cancel):
        assert db.get(OpeOrder, o.id).conflict
    # 矛盾の無いオーダーは解決できない
    with pytest.raises(BadRequestError, match="no conflict"):
        ope.resolve_conflict(db, s["orders"][14].id, s["releaser"], ConflictResolveIn(action=ConflictAction.KEEP))
    # 矛盾のあるオーダーも保留はできる（keep で解決した後も保留のまま）
    ope.hold_order(db, keep.id, s["operator"], reason="x")

    r = ope.resolve_conflict(db, keep.id, s["releaser"], ConflictResolveIn(action=ConflictAction.KEEP))
    assert not r.conflict and r.status == OrderStatus.ON_HOLD and r.scheduled_start.astimezone(JST).hour == 9
    assert r.events[-1].event_type == OrderEventType.CONFLICT_RESOLVED and (r.events[-1].detail or {})["action"] == "keep"

    r = ope.resolve_conflict(db, accept.id, s["admin"], ConflictResolveIn(action=ConflictAction.ACCEPT_PLAN))
    assert not r.conflict and r.status == OrderStatus.IN_PROGRESS and r.scheduled_start.astimezone(JST).hour == 15

    with pytest.raises(BadRequestError, match="reason"):
        ope.resolve_conflict(db, cancel.id, s["admin"], ConflictResolveIn(action=ConflictAction.CANCEL))
    r = ope.resolve_conflict(db, cancel.id, s["admin"], ConflictResolveIn(action=ConflictAction.CANCEL, reason="中止"))
    assert r.status == OrderStatus.CANCELED and not r.conflict
    assert r.events[-1].from_status == OrderStatus.IN_PROGRESS and r.events[-1].to_status == OrderStatus.CANCELED


def test_conflicted_ready_order_cannot_start_until_resolved(db, setup, monkeypatch):
    s = setup
    order = s["orders"][15]
    pretend_today(monkeypatch, day_of(order))
    ope.release_order(db, order.id, s["releaser"])
    ope.hold_order(db, order.id, s["operator"], reason="x")  # ready から保留
    # 保留中に枠が消えた → 矛盾（取り消されない）
    plan_services.delete_template(db, s["template_id"])
    db.expire_all()
    o = db.get(OpeOrder, order.id)
    assert o.conflict and o.status == OrderStatus.ON_HOLD
    # 再開すると ready に戻るが、矛盾を解決するまで開始できない
    assert ope.resume_order(db, order.id, s["operator"]).status == OrderStatus.READY
    with pytest.raises(BadRequestError, match="conflict"):
        ope.start_order(db, order.id, s["operator"])
    # accept_plan で枠が無ければ取消
    r = ope.resolve_conflict(db, order.id, s["releaser"], ConflictResolveIn(action=ConflictAction.ACCEPT_PLAN))
    assert r.status == OrderStatus.CANCELED


def test_order_endpoints_guard_roles():
    from main import app

    paths = app.openapi()["paths"]
    for action in ("release", "approve-early-start", "start", "hold", "resume", "finish", "cancel", "resolve-conflict"):
        op = paths[f"/ope/orders/{{order_id}}/{action}"]["post"]
        assert "x-staff-id" in {p["name"] for p in op.get("parameters", [])}, action
        assert {"401", "403"} <= set(op["responses"])
    assert "x-staff-id" in {p["name"] for p in paths["/ope/orders/{order_id}"]["get"].get("parameters", [])}
