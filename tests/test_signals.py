# tests/test_signals.py
"""
信号の受け取りと照合（7.3）のテスト:
- 顧客 + 信号の種類 + ルール上の日付で、ready / 作業中のオーダーを完了にする（matched。システムの記録に信号の ID）
- 同じ信号をもう一度 → duplicate（何もしない）
- 業務なし・日付でない・オーダーなし・未リリース・保留中・矛盾あり → unmatched（理由付き）。未完了が複数 → ambiguous
- 照合できなかった信号への人の対応（オーダーを指定して完了 / 対応済みにするだけ。一度だけ）
- 信号で完了する業務を手で完了にするには理由が必須。LIGHTWEIGHT は ready から完了（受領）できる

※ テストごとに一意な顧客コードを使ってデータを分離しています。
"""
import uuid
from datetime import date, datetime, time, timedelta, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from src.core.exceptions import BadRequestError, NotFoundError
from src.domain_biz import services as biz_services
from src.domain_biz.models import CompletionMode, ExecutionType
from src.domain_biz.schemas import BizMasterCreate, BizMasterUpdate
from src.domain_client.schemas import ClientCreate
from src.domain_ope import services as ope
from src.domain_ope import signals
from src.domain_ope.models import EventSource, OpeOrder, OrderStatus, SignalResult
from src.domain_ope.schemas import SignalHandleIn, SignalIn
from src.domain_plan import services as plan_services
from src.domain_plan.models import TemplateType
from src.domain_plan.schemas import PlanOneOffCreate, RRuleTemplateCreate
from src.domain_staff import services as staff_services
from src.domain_staff.models import RoleCode, StaffStatus
from src.domain_staff.schemas import StaffCreate
from src.infrastructure.database import SessionLocal
from src.infrastructure.repositories import biz_master_repo, client_repo

JST = timezone(timedelta(hours=9))
TYPE = "data_received"


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def staff(db):
    def make(*roles: RoleCode) -> int:
        return staff_services.create_staff(
            db, StaffCreate(login_id="j-" + uuid.uuid4().hex[:10], name="signal actor", roles=list(roles)),
            actor=None, status=StaffStatus.ACTIVE,
        ).id
    return {"sender": make(RoleCode.SIGNAL_SENDER), "operator": make(RoleCode.OPERATOR), "releaser": make(RoleCode.RELEASER)}


@pytest.fixture
def setup(db):
    """信号で完了する顧客側の出来事（LIGHTWEIGHT・AUTOMATIC）の業務と、毎日の予定"""
    code = "J" + uuid.uuid4().hex[:3].upper()  # 他のテストの顧客とコードが衝突しないよう、別の先頭文字を使う
    client_repo.create(db, obj_in=ClientCreate(code=code, name="signal client"))
    biz = biz_master_repo.create(db, obj_in=BizMasterCreate(client_code=code, name="データ受領", business_seq=1, manager_email=None, description=None))
    biz_services.update_biz(db, biz.id, BizMasterUpdate.model_validate(
        {"execution_type": ExecutionType.LIGHTWEIGHT, "completion_mode": CompletionMode.AUTOMATIC, "signal_type": TYPE},
    ))
    template = plan_services.create_template(db, RRuleTemplateCreate(
        biz_master_id=biz.id, name="Data received", template_type=TemplateType.RRULE, timezone="Asia/Tokyo",
        start_time=time(9, 0), duration_minutes=60, anchor_date=date(2026, 1, 1), rrule="FREQ=DAILY",
    ))
    return {"code": code, "biz_id": biz.id, "template_id": template.id}


def orders(db, setup) -> list[OpeOrder]:
    db.expire_all()
    return list(db.scalars(select(OpeOrder).where(OpeOrder.biz_master_id == setup["biz_id"]).order_by(OpeOrder.scheduled_start)))


def send(db, setup, staff, key: str, **overrides):
    values = dict(type=TYPE, client_code=setup["code"].lower(), key=key, received_at=datetime.now(JST))
    values.update(overrides)
    return signals.receive_signal(db, SignalIn.model_validate(values), staff["sender"])


# ==========================================
# 照合して完了
# ==========================================
def test_signal_finishes_the_ready_order_and_a_resend_is_a_duplicate(db, setup, staff):
    order = orders(db, setup)[2]
    ope.release_order(db, order.id, staff["releaser"])
    key = order.occurrence_date.isoformat()

    s = send(db, setup, staff, key, payload={"file": "data.csv", "rows": 120})
    assert (s.result, s.matched_order_id, s.client_code, s.payload) == (SignalResult.MATCHED, order.id, setup["code"], {"file": "data.csv", "rows": 120})
    d = ope.get_order(db, order.id)
    finished = d.events[-1]
    assert d.status == OrderStatus.FINISHED and finished.source == EventSource.SYSTEM
    assert finished.detail == {"cause": "signal", "signal_id": s.id}

    again = send(db, setup, staff, key)
    assert (again.result, again.matched_order_id) == (SignalResult.DUPLICATE, order.id)
    assert ope.get_order(db, order.id).events[-1].id == finished.id  # 何も起きない


# ==========================================
# 照合できない
# ==========================================
def test_unmatched_reasons(db, setup, staff, monkeypatch):
    pending, held, conflicted = orders(db, setup)[3:6]
    ope.release_order(db, held.id, staff["releaser"])
    ope.hold_order(db, held.id, staff["operator"], "確認中")
    c = db.get(OpeOrder, conflicted.id)
    assert c is not None
    c.conflict, c.conflict_reason = True, "予定が動いた"
    db.commit()

    cases = {
        "no business": send(db, setup, staff, pending.occurrence_date.isoformat(), type="other_type"),
        "unknown client": send(db, setup, staff, pending.occurrence_date.isoformat(), client_code="ZZZZZ"),
        "not a date": send(db, setup, staff, "Nov 10"),
        "no open order": send(db, setup, staff, "1999-01-01"),
        "not released": send(db, setup, staff, pending.occurrence_date.isoformat()),
        "on hold": send(db, setup, staff, held.occurrence_date.isoformat()),
        "conflict": send(db, setup, staff, conflicted.occurrence_date.isoformat()),
    }
    notes = {"no business": "completes by signal type", "unknown client": "no business of client ZZZZZ", "not a date": "is not a date",
             "no open order": "no open order", "not released": "not released yet", "on hold": "on hold", "conflict": "has a conflict"}
    for case, s in cases.items():
        assert s.result == SignalResult.UNMATCHED and s.matched_order_id is None, case
        assert notes[case] in (s.result_note or ""), (case, s.result_note)
    assert all(o.status != OrderStatus.FINISHED for o in orders(db, setup))


def test_two_open_orders_on_one_day_are_ambiguous(db, setup, staff):
    base = orders(db, setup)[2]
    plan_services.add_one_off_instance(db, setup["template_id"], PlanOneOffCreate(scheduled_start=base.scheduled_start + timedelta(hours=4)))
    s = send(db, setup, staff, base.occurrence_date.isoformat())
    assert s.result == SignalResult.AMBIGUOUS and "2 open orders" in (s.result_note or "")


# ==========================================
# 人の対応
# ==========================================
def test_handling_an_unmatched_signal(db, setup, staff):
    order = orders(db, setup)[2]
    s = send(db, setup, staff, order.occurrence_date.isoformat())  # 未リリース → unmatched
    assert s.result == SignalResult.UNMATCHED

    with pytest.raises(BadRequestError, match="only ready / in-progress"):
        signals.handle_signal(db, s.id, staff["operator"], SignalHandleIn(note="受領済み", order_id=order.id))
    db.rollback()
    ope.release_order(db, order.id, staff["releaser"])
    h = signals.handle_signal(db, s.id, staff["operator"], SignalHandleIn(note="リリース後に受領を確認", order_id=order.id))
    assert h.handled_by_staff_id == staff["operator"] and h.handled_note == "リリース後に受領を確認"
    d = ope.get_order(db, order.id)
    assert d.status == OrderStatus.FINISHED
    assert (d.events[-1].source, d.events[-1].actor_staff_id, d.events[-1].detail) == (
        EventSource.HUMAN, staff["operator"], {"cause": "signal handled", "signal_id": s.id},
    )
    with pytest.raises(BadRequestError, match="already handled"):
        signals.handle_signal(db, s.id, staff["operator"], SignalHandleIn(note="二重"))
    db.rollback()

    # 誤送信: オーダーを指定せず対応済みにするだけ。照合できた信号・別の顧客のオーダーは対応できない
    wrong = send(db, setup, staff, "Nov 10")
    assert signals.handle_signal(db, wrong.id, staff["operator"], SignalHandleIn(note="誤送信")).handled_at is not None
    dup = send(db, setup, staff, order.occurrence_date.isoformat())
    assert dup.result == SignalResult.DUPLICATE
    with pytest.raises(BadRequestError, match="only unmatched"):
        signals.handle_signal(db, dup.id, staff["operator"], SignalHandleIn(note="x"))
    db.rollback()
    other = send(db, setup, staff, "1999-01-01")
    with pytest.raises(NotFoundError):
        signals.handle_signal(db, other.id, staff["operator"], SignalHandleIn(note="x", order_id=999_999_999))
    db.rollback()


# ==========================================
# 手での完了・入力の検証・権限
# ==========================================
def test_manual_finish_of_client_events(db, setup, staff):
    automatic, manual_biz_order = orders(db, setup)[2:4]
    ope.release_order(db, automatic.id, staff["releaser"])
    with pytest.raises(BadRequestError, match="reason is required"):
        ope.finish_order(db, automatic.id, staff["operator"])
    db.rollback()
    r = ope.finish_order(db, automatic.id, staff["operator"], "信号が来なかったため手で確認")
    assert r.status == OrderStatus.FINISHED  # ready から（受領。開始しない）

    # 手動（MANUAL）の顧客側の出来事は理由なしで受領できる
    biz_services.update_biz(db, setup["biz_id"], BizMasterUpdate.model_validate({"completion_mode": CompletionMode.MANUAL, "signal_type": None}))
    ope.release_order(db, manual_biz_order.id, staff["releaser"])
    assert ope.finish_order(db, manual_biz_order.id, staff["operator"]).status == OrderStatus.FINISHED


def test_input_validation_and_guard():
    from main import app

    with pytest.raises(ValidationError, match="timezone"):
        SignalIn.model_validate(dict(type=TYPE, client_code="ABCD", key="2026-11-10", received_at="2026-11-10T09:00:00"))
    with pytest.raises(ValidationError, match="at most"):
        SignalIn.model_validate(dict(type=TYPE, client_code="ABCD", key="2026-11-10", received_at="2026-11-10T09:00:00+09:00",
                                     payload={"x": "a" * 20_000}))
    paths = app.openapi()["paths"]
    for path in ("/ope/signals", "/ope/signals/{signal_id}/handle"):
        assert "x-staff-id" in {p["name"] for p in paths[path]["post"].get("parameters", [])}, path
