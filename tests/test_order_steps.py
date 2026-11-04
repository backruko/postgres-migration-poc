# tests/test_order_steps.py
"""
オーダーのステップの組み立て（5.3）のテスト:
- リリース前はプレビュー（条件 always / month_in / first_in_week を予定日で判定）
- リリースで条件に合うステップだけを作る（locked。作ったもの・外れたものを記録に残す）
- リリース済み・未開始のオーダーは、予定の変更で条件の結果が変わると矛盾フラグ（同じ週の他の予定の変更でも）
- 矛盾の解決: accept_plan はステップを作り直し（古い行は retired）、keep はそのまま

テンプレートは毎日 9:00（日本時間）。日付は今日から計算する（オーダーは今以降の予定にだけ作られるため）。
※ テストごとに一意な顧客コードを使ってデータを分離しています。
"""
import uuid
from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import select

from src.core.exceptions import BadRequestError
from src.domain_biz.models import SOPStatus, SOPStepTemplate, SOPVersion
from src.domain_biz.schemas import BizMasterCreate
from src.domain_client.schemas import ClientCreate
from src.domain_ope import services as ope
from src.domain_ope.models import (
    EventSource, OpeOrder, OpeOrderEvent, OpeStep, OpeStepEvent, OrderEventType, OrderStatus, StepEventType, StepState,
)
from src.domain_ope.schemas import ConflictAction, ConflictResolveIn
from src.domain_plan import services as plan_services
from src.domain_plan.models import TemplateType
from src.domain_plan.schemas import PlanInstanceCancel, PlanInstanceOverride, PlanTemplateUpdate, RRuleTemplateCreate
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
def staff(db):
    def make(role: RoleCode) -> int:
        return staff_services.create_staff(
            db, StaffCreate(login_id="k-" + uuid.uuid4().hex[:10], name=role.value, roles=[role]),
            actor=None, status=StaffStatus.ACTIVE,
        ).id
    return {"releaser": make(RoleCode.RELEASER), "operator": make(RoleCode.OPERATOR)}


def base_tuesday() -> date:
    """2週間以上先の火曜日（その週の月曜日の予定も今より後になる）"""
    day = datetime.now(JST).date() + timedelta(days=14)
    return day + timedelta(days=(1 - day.weekday()) % 7)


def next_month_tuesday(day: date) -> date:
    """day の翌月の最初の火曜日"""
    first = (day.replace(day=1) + timedelta(days=32)).replace(day=1)
    return first + timedelta(days=(1 - first.weekday()) % 7)


@pytest.fixture
def setup(db):
    """
    毎日の予定と、承認済みバージョンのステップテンプレート4件:
    1 always / 2 month_in（base_tuesday の翌月のみ）/ 3 first_in_week / 4 always（is_blocking）
    """
    code = "K" + uuid.uuid4().hex[:3].upper()  # 他のテストの顧客とコードが衝突しないよう、別の先頭文字を使う
    client_repo.create(db, obj_in=ClientCreate(code=code, name="step client"))
    biz = biz_master_repo.create(db, obj_in=BizMasterCreate(client_code=code, name="step biz", business_seq=1, manager_email=None, description=None))
    tuesday = base_tuesday()
    later = next_month_tuesday(tuesday)
    version = SOPVersion(biz_master_id=biz.id, version_number=1, version_tag="v1.0.0", status=SOPStatus.ACTIVE,
                         effective_at=datetime(2026, 1, 1, tzinfo=JST))
    db.add(version)
    db.flush()
    conditions = [{"type": "always"}, {"type": "month_in", "months": [later.month]}, {"type": "first_in_week"}, {"type": "always"}]
    db.add_all([
        SOPStepTemplate(sop_version_id=version.id, step_num=n, title=f"手順{n}", content=None, condition=c, is_blocking=(n == 4))
        for n, c in enumerate(conditions, start=1)
    ])
    db.commit()
    template = plan_services.create_template(db, RRuleTemplateCreate(
        biz_master_id=biz.id, name="Daily", template_type=TemplateType.RRULE, timezone="Asia/Tokyo",
        start_time=time(9, 0), duration_minutes=60, anchor_date=date(2026, 1, 1), rrule="FREQ=DAILY",
    ))
    return {"template_id": template.id, "tuesday": tuesday, "later": later}


def order_on(db, setup, day: date) -> OpeOrder:
    db.expire_all()
    order = db.scalars(select(OpeOrder).where(
        OpeOrder.template_id == setup["template_id"], OpeOrder.occurrence_date == day, OpeOrder.status != OrderStatus.CANCELED,
    )).one()
    return order


def nums(detail) -> list[int]:
    return [s.step_num for s in detail.steps]


def step_rows(db, order_id: int) -> list[OpeStep]:
    return list(db.scalars(select(OpeStep).where(OpeStep.order_id == order_id).order_by(OpeStep.id)))


def last_event(db, order_id: int) -> OpeOrderEvent:
    return db.scalars(select(OpeOrderEvent).where(OpeOrderEvent.order_id == order_id).order_by(OpeOrderEvent.id.desc())).first()  # type: ignore[return-value]


# ==========================================
# プレビューとリリース
# ==========================================
def test_preview_follows_the_conditions_and_release_creates_the_steps(db, setup, staff):
    tuesday, later = setup["tuesday"], setup["later"]
    monday = tuesday - timedelta(days=1)

    # リリース前はプレビュー（行は作らない）: 月曜 = 週の最初、翌月 = month_in に合う
    preview = ope.get_order(db, order_on(db, setup, monday).id)
    assert preview.steps_preview and nums(preview) == [1, 3, 4] and all(s.id is None and s.state is None for s in preview.steps)
    assert nums(ope.get_order(db, order_on(db, setup, tuesday).id)) == [1, 4]
    assert nums(ope.get_order(db, order_on(db, setup, later).id)) == [1, 2, 4]

    # リリースで条件に合うステップだけを作る（すべて locked・番号はテンプレートのまま）
    order = order_on(db, setup, monday)
    r = ope.release_order(db, order.id, staff["releaser"])
    assert not r.steps_preview and nums(r) == [1, 3, 4]
    assert all(s.id is not None and s.state == StepState.LOCKED for s in r.steps)
    assert [s.is_blocking for s in r.steps] == [False, False, True]
    release = r.events[-1]
    assert release.detail is not None and release.detail["steps"]["created"] == [1, 3, 4]
    assert [s["step_num"] for s in release.detail["steps"]["skipped"]] == [2]

    # ステップごとに作成の記録（リリースした職員）
    created = list(db.scalars(select(OpeStepEvent).where(OpeStepEvent.step_id.in_([s.id for s in r.steps]))))
    assert len(created) == 3 and all(
        e.event_type == StepEventType.CREATED and e.to_state == StepState.LOCKED
        and e.source == EventSource.HUMAN and e.actor_staff_id == staff["releaser"]
        for e in created
    )


def test_release_without_a_version_has_no_steps(db, staff):
    code = "K" + uuid.uuid4().hex[:3].upper()
    client_repo.create(db, obj_in=ClientCreate(code=code, name="no sop client"))
    biz = biz_master_repo.create(db, obj_in=BizMasterCreate(client_code=code, name="no sop", business_seq=1, manager_email=None, description=None))
    template = plan_services.create_template(db, RRuleTemplateCreate(
        biz_master_id=biz.id, name="Daily", template_type=TemplateType.RRULE, timezone="Asia/Tokyo",
        start_time=time(9, 0), duration_minutes=60, anchor_date=date(2026, 1, 1), rrule="FREQ=DAILY",
    ))
    db.expire_all()
    order = db.scalars(select(OpeOrder).where(OpeOrder.template_id == template.id).order_by(OpeOrder.scheduled_start)).first()
    assert order is not None
    assert ope.get_order(db, order.id).steps == []
    r = ope.release_order(db, order.id, staff["releaser"])
    assert r.steps == [] and r.events[-1].detail is not None and r.events[-1].detail["steps"] == {"created": [], "skipped": []}


# ==========================================
# 予定の変更で条件の結果が変わる → 矛盾フラグ → 解決
# ==========================================
def test_moving_a_released_order_into_another_month_flags_it_and_accept_plan_rebuilds(db, setup, staff):
    tuesday, later = setup["tuesday"], setup["later"]
    order = order_on(db, setup, tuesday)
    ope.release_order(db, order.id, staff["releaser"])
    old_ids = [s.id for s in step_rows(db, order.id)]

    # 人が翌月の火曜日へ動かす: 予定は追従し、month_in のステップ2が増えるので矛盾フラグ
    plan_services.override_instance(db, order.current_instance_id, PlanInstanceOverride(
        scheduled_start=datetime.combine(later, time(9, 0), tzinfo=JST), note="月をまたいで延期",
    ), actor_staff_id=staff["operator"])
    db.expire_all()
    order = db.get(OpeOrder, order.id)
    assert order is not None and order.status == OrderStatus.READY and order.occurrence_date == tuesday
    assert order.scheduled_start.astimezone(JST).date() == later
    assert order.conflict and "ステップの条件が変わりました（追加: 2）" in (order.conflict_reason or "")
    raised = last_event(db, order.id)
    assert raised.event_type == OrderEventType.CONFLICT_RAISED and raised.detail == {
        "cause": "step conditions changed", "added": [2], "removed": [],
    }
    # 解決するまでステップは固定のまま・開始できない
    assert nums(ope.get_order(db, order.id)) == [1, 4]
    with pytest.raises(BadRequestError, match="conflict"):
        ope.start_order(db, order.id, staff["operator"])

    # accept_plan: 今の予定日の条件で作り直す（古い行は retired として残る）
    r = ope.resolve_conflict(db, order.id, staff["releaser"], ConflictResolveIn(action=ConflictAction.ACCEPT_PLAN))
    assert not r.conflict and nums(r) == [1, 2, 4] and all(s.state == StepState.LOCKED for s in r.steps)
    assert r.events[-1].detail is not None and r.events[-1].detail["steps"]["added"] == [2]
    rows = {s.id: s for s in step_rows(db, order.id)}
    assert all(rows[i].retired_at is not None for i in old_ids)
    retired = list(db.scalars(select(OpeStepEvent).where(OpeStepEvent.step_id.in_(old_ids))))
    assert sorted(e.event_type.value for e in retired) == ["created", "created", "retired", "retired"]


def test_keep_leaves_the_frozen_steps(db, setup, staff):
    tuesday, later = setup["tuesday"], setup["later"]
    order = order_on(db, setup, tuesday)
    ope.release_order(db, order.id, staff["releaser"])
    plan_services.override_instance(db, order.current_instance_id, PlanInstanceOverride(
        scheduled_start=datetime.combine(later + timedelta(days=1), time(9, 0), tzinfo=JST),
    ), actor_staff_id=staff["operator"])
    db.expire_all()
    assert (o := db.get(OpeOrder, order.id)) is not None and o.conflict

    r = ope.resolve_conflict(db, order.id, staff["releaser"], ConflictResolveIn(action=ConflictAction.KEEP))
    assert not r.conflict and nums(r) == [1, 4] and len(step_rows(db, order.id)) == 2
    assert "steps" not in (r.events[-1].detail or {})


def test_cancelling_another_occurrence_in_the_week_flags_first_in_week(db, setup, staff):
    tuesday = setup["tuesday"]
    monday, wednesday = tuesday - timedelta(days=1), tuesday + timedelta(days=1)
    tue_order, wed_order = order_on(db, setup, tuesday), order_on(db, setup, wednesday)
    for o in (tue_order, wed_order):
        ope.release_order(db, o.id, staff["releaser"])

    # 月曜日の予定を取り消す → 火曜日がその週の最初になり、first_in_week のステップ3が増える
    plan_services.cancel_instance(db, order_on(db, setup, monday).current_instance_id, PlanInstanceCancel(note="休業"),
                                  actor_staff_id=staff["operator"])
    db.expire_all()
    tue_order, wed_order = db.get(OpeOrder, tue_order.id), db.get(OpeOrder, wed_order.id)
    assert tue_order is not None and wed_order is not None
    assert tue_order.conflict and "追加: 3" in (tue_order.conflict_reason or "")
    assert not wed_order.conflict  # 水曜日は最初ではないまま

    r = ope.resolve_conflict(db, tue_order.id, staff["releaser"], ConflictResolveIn(action=ConflictAction.ACCEPT_PLAN))
    assert nums(r) == [1, 3, 4]


def test_system_move_that_keeps_the_conditions_raises_nothing(db, setup, staff):
    order = order_on(db, setup, setup["tuesday"])
    ope.release_order(db, order.id, staff["releaser"])

    # テンプレートの時刻の変更（システムが全ての枠を動かす）: 日付は同じなので条件の結果も同じ
    plan_services.update_template(db, setup["template_id"], PlanTemplateUpdate(start_time=time(14, 0)))
    db.expire_all()
    moved = db.get(OpeOrder, order.id)
    assert moved is not None and moved.scheduled_start.astimezone(JST).hour == 14 and not moved.conflict
    assert last_event(db, order.id).event_type == OrderEventType.RESCHEDULED
