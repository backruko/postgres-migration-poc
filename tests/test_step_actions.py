# tests/test_step_actions.py
"""
ステップの操作（5.4）のテスト:
- オーダーの開始で最初のステップが開く。提出で次が開く（is_blocking のステップは承認で次が開く）
- 承認・差し戻しは提出者以外（四つの目）。差し戻しはそのステップだけを todo に戻す（理由必須）
- 最後の承認でオーダーが自動で完了する（矛盾フラグがあれば解決まで待つ）。ステップのあるオーダーは手動で完了にできない
- ステップの操作は作業中のオーダーのみ（保留中は止まる）

ステップテンプレート: 1（通常）/ 2（is_blocking）/ 3（通常）。すべて always。
※ テストごとに一意な顧客コードを使ってデータを分離しています。
"""
import uuid
from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import select

from tests.client_codes import new_client_code
from src.coordination import services as ops_services
from src.core.error_codes import ErrorCode
from src.core.exceptions import BadRequestError, ForbiddenError
from src.domain_biz.models import SOPStatus, SOPStepTemplate, SOPVersion
from src.domain_biz.schemas import BizMasterCreate
from src.domain_client.schemas import ClientCreate
from src.domain_ope import services as ope
from src.domain_ope.models import EventSource, OpeOrder, OrderEventType, OrderStatus, StepState
from src.domain_ope.schemas import ConflictAction, ConflictResolveIn
from src.domain_plan import services as plan_services
from src.domain_plan.models import TemplateType
from src.domain_plan.schemas import RRuleTemplateCreate
from src.domain_staff import services as staff_services
from src.domain_staff.models import RoleCode, StaffStatus
from src.domain_staff.schemas import StaffCreate
from src.infrastructure.database import SessionLocal
from src.infrastructure.repositories import biz_master_repo, client_repo

JST = timezone(timedelta(hours=9))
LOCKED, TODO, SUBMITTED, APPROVED = StepState.LOCKED, StepState.TODO, StepState.SUBMITTED, StepState.APPROVED


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
def staff(db):
    def make(*roles: RoleCode) -> int:
        return staff_services.create_staff(
            db, StaffCreate(login_id="m-" + uuid.uuid4().hex[:10], name="step " + "+".join(r.value for r in roles), roles=list(roles)),
            actor=None, status=StaffStatus.ACTIVE,
        ).id
    return {
        "operator": make(RoleCode.OPERATOR), "checker": make(RoleCode.CHECKER), "checker2": make(RoleCode.CHECKER),
        "both": make(RoleCode.OPERATOR, RoleCode.CHECKER), "releaser": make(RoleCode.RELEASER),
    }


@pytest.fixture
def started(db, staff, monkeypatch):
    """ステップ3件のオーダーをリリースして開始したもの（予定日を「今日」にして開始する）"""
    code = new_client_code("M")  # 他のテストの顧客とコードが衝突しないよう、別の先頭文字を使う
    client_repo.create(db, obj_in=ClientCreate(code=code, name="step action client"))
    biz = biz_master_repo.create(db, obj_in=BizMasterCreate(client_code=code, name="step biz", business_seq=1, manager_email=None, description=None))
    version = SOPVersion(biz_master_id=biz.id, version_number=1, version_tag="v1.0.0", status=SOPStatus.APPROVED,
                         effective_at=datetime(2026, 1, 1, tzinfo=JST))
    db.add(version)
    db.flush()
    db.add_all([
        SOPStepTemplate(sop_version_id=version.id, step_num=n, title=f"手順{n}", content=None, is_blocking=(n == 2))
        for n in (1, 2, 3)
    ])
    db.commit()
    template = plan_services.create_template(db, RRuleTemplateCreate(
        biz_master_id=biz.id, name="Daily", template_type=TemplateType.RRULE, timezone="Asia/Tokyo",
        start_time=time(9, 0), duration_minutes=60, anchor_date=date(2026, 1, 1), rrule="FREQ=DAILY",
    ))
    db.expire_all()
    order = db.scalars(select(OpeOrder).where(OpeOrder.template_id == template.id).order_by(OpeOrder.scheduled_start).offset(2)).first()
    assert order is not None
    ope.release_order(db, order.id, staff["releaser"])
    monkeypatch.setattr(ope, "_now", lambda: order.scheduled_start)
    return ope.start_order(db, order.id, staff["operator"])


def states(detail) -> list[StepState | None]:
    return [s.state for s in detail.steps]


def step_id(detail, num: int) -> int:
    found = next(s.id for s in detail.steps if s.step_num == num)
    assert found is not None
    return found


# ==========================================
# 開始・提出・承認の流れ
# ==========================================
def test_start_opens_the_first_step(started):
    assert started.status == OrderStatus.IN_PROGRESS and states(started) == [TODO, LOCKED, LOCKED]
    opened = started.steps[0].events[-1]
    assert (opened.source, opened.from_state, opened.to_state, opened.detail) == (
        EventSource.SYSTEM, LOCKED, TODO, {"cause": "order started"},
    )


def test_full_flow_with_a_blocking_step_finishes_the_order(db, started, staff):
    oid = started.id
    # 1 は通常: 提出で 2 が開く
    r = ope.submit_step(db, step_id(started, 1), staff["operator"], "完了しました")
    assert states(r) == [SUBMITTED, TODO, LOCKED]
    # 2 は is_blocking: 提出しても 3 は開かない、承認で開く
    r = ope.submit_step(db, step_id(started, 2), staff["operator"])
    assert states(r) == [SUBMITTED, SUBMITTED, LOCKED]
    r = ope.approve_step(db, step_id(started, 2), staff["checker"])
    assert states(r) == [SUBMITTED, APPROVED, TODO]
    assert r.steps[2].events[-1].detail == {"cause": "previous step approved", "after_step": 2}
    r = ope.submit_step(db, step_id(started, 3), staff["operator"])
    r = ope.approve_step(db, step_id(started, 1), staff["checker"])
    assert r.status == OrderStatus.IN_PROGRESS  # まだ 3 が承認待ち

    # 最後の承認でオーダーが完了（システムの記録）
    r = ope.approve_step(db, step_id(started, 3), staff["checker2"])
    assert states(r) == [APPROVED, APPROVED, APPROVED] and r.status == OrderStatus.FINISHED
    finished = r.events[-1]
    assert (finished.event_type, finished.source, finished.from_status, finished.to_status, finished.detail) == (
        OrderEventType.STATUS_CHANGED, EventSource.SYSTEM, OrderStatus.IN_PROGRESS, OrderStatus.FINISHED,
        {"cause": "all steps approved"},
    )
    # 提出・承認は操作した職員の記録
    submit = r.steps[0].events[2]
    assert (submit.to_state, submit.source, submit.actor_staff_id, submit.reason) == (SUBMITTED, EventSource.HUMAN, staff["operator"], "完了しました")
    assert db.get(OpeOrder, oid).status == OrderStatus.FINISHED  # type: ignore[union-attr]


def test_events_carry_the_actor_name(db, started, staff):
    r = ope.submit_step(db, step_id(started, 1), staff["operator"])
    # オーダーの記録: リリース（リリース担当）・開始（作業者）は名前付き、作成（システム）は None
    by_type = {(e.event_type, e.to_status): e for e in r.events}
    assert by_type[(OrderEventType.STATUS_CHANGED, OrderStatus.READY)].actor_name == "step releaser"
    assert by_type[(OrderEventType.STATUS_CHANGED, OrderStatus.IN_PROGRESS)].actor_name == "step operator"
    assert all(e.actor_name is None for e in r.events if e.source == EventSource.SYSTEM)
    # ステップの記録: 開いたのはシステム、提出は作業者
    opened, submitted = r.steps[0].events[1:3]
    assert (opened.source, opened.actor_name) == (EventSource.SYSTEM, None)
    assert (submitted.to_state, submitted.actor_name) == (SUBMITTED, "step operator")


def test_wrong_state_and_order_status_are_refused(db, started, staff):
    with pytest.raises(BadRequestError, match="it is locked"):
        ope.submit_step(db, step_id(started, 2), staff["operator"])
    with pytest.raises(BadRequestError, match="it is todo"):
        ope.approve_step(db, step_id(started, 1), staff["checker"])
    # ステップのあるオーダーは手動で完了にできない
    with pytest.raises(BadRequestError, match="finishes automatically"):
        ope.finish_order(db, started.id, staff["operator"])


# ==========================================
# 四つの目・差し戻し
# ==========================================
def test_the_submitter_cannot_approve_or_reject(db, started, staff):
    sid = step_id(started, 1)
    ope.submit_step(db, sid, staff["both"])
    for action in (lambda: ope.approve_step(db, sid, staff["both"]), lambda: ope.reject_step(db, sid, staff["both"], "x")):
        with pytest.raises(ForbiddenError) as err:
            action()
        assert err.value.error_code == ErrorCode.FOUR_EYES
        db.rollback()

    # 別の確認者が差し戻す → そのステップだけ todo（提出で開いた 2 はそのまま）
    r = ope.reject_step(db, sid, staff["checker"], "金額の確認漏れ")
    assert states(r) == [TODO, TODO, LOCKED]
    assert r.steps[0].events[-1].reason == "金額の確認漏れ"

    # 再提出は別の作業者でもよく、最初の提出者（両方のロールを持つ人）はそのあと承認できる
    ope.submit_step(db, sid, staff["operator"])
    r = ope.approve_step(db, sid, staff["both"])
    assert states(r) == [APPROVED, TODO, LOCKED]


def test_rejecting_a_blocking_step_keeps_the_next_locked(db, started, staff):
    ope.submit_step(db, step_id(started, 1), staff["operator"])
    ope.submit_step(db, step_id(started, 2), staff["operator"])
    r = ope.reject_step(db, step_id(started, 2), staff["checker"], "添付漏れ")
    assert states(r) == [SUBMITTED, TODO, LOCKED]


# ==========================================
# 保留・矛盾
# ==========================================
def test_steps_are_frozen_while_the_order_is_on_hold(db, started, staff):
    ope.hold_order(db, started.id, staff["operator"], "顧客確認待ち")
    with pytest.raises(BadRequestError, match="on_hold"):
        ope.submit_step(db, step_id(started, 1), staff["operator"])
    ope.resume_order(db, started.id, staff["operator"])
    assert states(ope.submit_step(db, step_id(started, 1), staff["operator"])) == [SUBMITTED, TODO, LOCKED]


def test_last_approval_waits_for_an_open_conflict(db, started, staff):
    order = db.get(OpeOrder, started.id)
    assert order is not None
    order.conflict, order.conflict_reason = True, "作業中に予定の日時が変わりました"
    db.commit()

    # 矛盾があっても作業は続けられる
    r = started
    for n in (1, 2, 3):
        ope.submit_step(db, step_id(started, n), staff["operator"])
        r = ope.approve_step(db, step_id(started, n), staff["checker"])
    assert states(r) == [APPROVED, APPROVED, APPROVED] and r.status == OrderStatus.IN_PROGRESS

    # 解決（そのまま続ける）で完了
    r = ope.resolve_conflict(db, started.id, staff["releaser"], ConflictResolveIn(action=ConflictAction.KEEP))
    assert r.status == OrderStatus.FINISHED and not r.conflict
    assert [e.event_type for e in r.events[-2:]] == [OrderEventType.CONFLICT_RESOLVED, OrderEventType.STATUS_CHANGED]


# ==========================================
# ダッシュボードの進み具合（5.5）
# ==========================================
def test_dashboard_rows_carry_the_step_progress(db, started, staff):
    ope.submit_step(db, step_id(started, 1), staff["operator"])
    ope.approve_step(db, step_id(started, 1), staff["checker"])
    day = started.scheduled_start.astimezone(JST).date()
    dash = ops_services.get_daily_dashboard(db, day, "Asia/Tokyo")
    summaries = [
        o.order for c in dash.clients for b in c.businesses for o in b.occurrences if o.order and o.order.id == started.id
    ]
    assert len(summaries) == 1 and (summaries[0].steps_total, summaries[0].steps_approved) == (3, 1)
