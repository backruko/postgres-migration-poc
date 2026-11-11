# tests/test_template_approval.py
"""
テンプレートの下書きの承認・差し戻しと、業務管理者の直接の保存の記録（Level 2、D.3）。
- 承認はすぐ反映され、直接の保存と同じ処理（オーダーも同じように追従する）。変更前の値・反映後の revision を残す
- 当てはめで誤りになれば何も変わらず、下書きは承認待ちのまま
- 差し戻しは理由つき、テンプレートは変わらない。決まった下書きはもう承認・差し戻しできない
- 直接の保存は API（actor あり）のときだけ「下書き即承認」として残る。何も変わらない保存は残さない
- 承認待ちの下書きがある業務は削除できない
※ 顧客コードの先頭文字は空きがないため "I" を共有する（fixture と helper は test_template_drafts.py のもの）
"""
from datetime import datetime, time, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from tests.test_template_drafts import (  # noqa: F401  (fixture は名前で使う)
    JST, UTC, _future, admin, biz_id, dated, daily, db, refused, releaser, rrule_body, state, update,
)
from src.core.error_codes import ErrorCode
from src.core.exceptions import BadRequestError, BusinessException, NotFoundError
from src.domain_biz import services as biz_services
from src.domain_ope.models import OpeOrder, OrderStatus
from src.domain_plan import services as plan_services
from src.domain_plan import template_changes
from src.domain_plan.models import PlanTemplate, PlanTemplateChange, TemplateChangeKind, TemplateChangeStatus
from src.domain_plan.schemas import (
    ExplicitDatesTemplateCreate, PlanTemplateChangeResponse, PlanTemplateUpdate, RRuleTemplateCreate,
    TemplateChangeReject, TemplateCreateDraft, TemplateDeleteDraft,
)
from src.infrastructure.repositories import plan_template_change_repo


def future_orders(db, biz_id: int, template_id: int | None = None) -> list[OpeOrder]:
    db.expire_all()
    stmt = select(OpeOrder).where(
        OpeOrder.biz_master_id == biz_id, OpeOrder.scheduled_start > datetime.now(UTC) + timedelta(days=1),
    )
    if template_id is not None:
        stmt = stmt.where(OpeOrder.template_id == template_id)
    return list(db.scalars(stmt.order_by(OpeOrder.scheduled_start)).all())


def changes_of(db, biz_id: int) -> list[PlanTemplateChange]:
    db.expire_all()
    return list(db.scalars(
        select(PlanTemplateChange).where(PlanTemplateChange.biz_master_id == biz_id).order_by(PlanTemplateChange.id)
    ).all())


# ==========================================
# 承認: すぐ反映。オーダーも直接の保存と同じように追従する
# ==========================================
def test_approving_an_update_applies_it_now_and_orders_follow(db, biz_id, daily, releaser, admin):
    revision_before = daily.revision
    draft = template_changes.create_draft(db, update(daily.id, {"start_time": "10:30:00"}), releaser)
    approved = template_changes.approve_draft(db, draft.id, admin, note="確認しました")

    assert approved.status == TemplateChangeStatus.APPROVED
    assert (approved.decided_by_id, approved.decision_note, approved.is_direct) == (admin, "確認しました", False)
    assert approved.before_values is not None and approved.before_values["start_time"] == "09:00:00"
    db.expire_all()
    template = db.get(PlanTemplate, daily.id)
    assert template is not None and template.start_time == time(10, 30)
    assert template.revision > revision_before and approved.applied_revision == template.revision
    assert PlanTemplateChangeResponse.model_validate(approved).self_approved is False

    orders = future_orders(db, biz_id, daily.id)
    assert orders and all(o.scheduled_start.astimezone(JST).time() == time(10, 30) for o in orders if o.status != OrderStatus.CANCELED)

    # 承認されたら、テンプレートはまた直接変えられる
    assert plan_services.update_template(db, daily.id, PlanTemplateUpdate(name="Next")).name == "Next"


def test_approving_a_create_makes_the_template_and_its_orders(db, biz_id, releaser, admin):
    body = rrule_body(biz_id, name="Approved new", rrule="FREQ=WEEKLY;BYDAY=WE")
    draft = template_changes.create_draft(db, TemplateCreateDraft.model_validate({"kind": "create", "template": body}), releaser)
    approved = template_changes.approve_draft(db, draft.id, admin)

    assert approved.template_id is not None and approved.applied_revision == 1 and approved.before_values is None
    template = db.get(PlanTemplate, approved.template_id)
    assert template is not None and template.name == "Approved new" and template.rrule == "FREQ=WEEKLY;BYDAY=WE"
    assert future_orders(db, biz_id, approved.template_id)
    assert [c.id for c in plan_template_change_repo.get_history(db, template_id=approved.template_id)] == [approved.id]


def test_approving_a_delete_removes_the_template_and_cancels_its_open_orders(db, biz_id, daily, releaser, admin):
    assert future_orders(db, biz_id, daily.id)
    draft = template_changes.create_draft(db, TemplateDeleteDraft(kind=TemplateChangeKind.DELETE, template_id=daily.id), releaser)
    approved = template_changes.approve_draft(db, draft.id, admin)

    assert approved.before_values is not None and approved.before_values["name"] == "Daily"
    db.expire_all()
    deleted = db.scalars(select(PlanTemplate).where(PlanTemplate.id == daily.id).execution_options(include_deleted=True)).one()
    assert deleted.is_deleted
    assert all(o.status == OrderStatus.CANCELED for o in future_orders(db, biz_id, daily.id))


def test_approving_a_dates_draft_changes_the_dates(db, dated, releaser, admin):
    draft = template_changes.create_draft(db, update(dated.id, add_dates=[_future(30)], remove_dates=[_future(10)]), releaser)
    template_changes.approve_draft(db, draft.id, admin)
    db.expire_all()
    template = db.get(PlanTemplate, dated.id)
    assert template is not None and sorted(d.local_date for d in template.explicit_dates) == [_future(20), _future(30)]


def test_name_and_display_only_drafts_are_kept_and_applied(db, daily, releaser, admin):
    # 回帰: 名前・表示だけの変更は何も書き込まないため、行ロックの取り直しで消えて「何も変えない」と断られていた
    for changes, check in (
        ({"name": "Renamed"}, lambda t: t.name == "Renamed"),
        ({"ui_metadata": {"bg_color": "#00FF00"}}, lambda t: t.ui_metadata == {"bg_color": "#00FF00"}),
        ({"warn_before_minutes": 30}, lambda t: t.warn_before_minutes == 30),
    ):
        draft = template_changes.create_draft(db, update(daily.id, changes), releaser)
        template_changes.approve_draft(db, draft.id, admin)
        db.expire_all()
        template = db.get(PlanTemplate, daily.id)
        assert template is not None and check(template), changes


def test_an_admin_may_approve_their_own_draft_and_it_shows_as_self_approved(db, daily, admin):
    draft = template_changes.create_draft(db, update(daily.id, {"duration_minutes": 45}), admin)
    approved = template_changes.approve_draft(db, draft.id, admin)
    response = PlanTemplateChangeResponse.model_validate(approved)
    assert response.self_approved is True and response.is_direct is False


def test_a_failing_approval_changes_nothing_and_the_draft_stays_pending(db, biz_id, dated, releaser, admin, monkeypatch):
    # 項目の変更を当てはめた後、指定日の当てはめで誤りになる → 項目の変更も含めて何も残らない
    draft = template_changes.create_draft(
        db, update(dated.id, {"start_time": "11:00:00"}, add_dates=[_future(30)]), releaser
    )
    before = state(db, biz_id)

    def fail(*args, **kwargs):
        raise BadRequestError("simulated failure while applying dates")

    monkeypatch.setattr(plan_services, "_apply_explicit_dates", fail)
    with pytest.raises(BadRequestError, match="simulated"):
        template_changes.approve_draft(db, draft.id, admin)
    db.rollback()
    monkeypatch.undo()

    assert state(db, biz_id) == before
    pending = db.get(PlanTemplateChange, draft.id)
    assert pending is not None and pending.status == TemplateChangeStatus.DRAFT and pending.before_values is None
    # 直したあとは承認できる
    assert template_changes.approve_draft(db, draft.id, admin).status == TemplateChangeStatus.APPROVED


# ==========================================
# 差し戻し・決まった下書き
# ==========================================
def test_rejecting_keeps_the_template_and_frees_it(db, biz_id, daily, releaser, admin):
    draft = template_changes.create_draft(db, update(daily.id, {"start_time": "10:00:00"}), releaser)
    before = state(db, biz_id)
    rejected = template_changes.reject_draft(db, draft.id, admin, "時刻は 9:00 のまま")

    assert (rejected.status, rejected.decided_by_id, rejected.decision_note) == (
        TemplateChangeStatus.REJECTED, admin, "時刻は 9:00 のまま"
    )
    assert state(db, biz_id) == before
    assert plan_services.update_template(db, daily.id, PlanTemplateUpdate(name="Free")).name == "Free"
    with pytest.raises(ValidationError):
        TemplateChangeReject(reason="   ")


def test_a_decided_draft_cannot_be_approved_or_rejected_again(db, daily, releaser, admin):
    approved = template_changes.approve_draft(
        db, template_changes.create_draft(db, update(daily.id, {"start_time": "10:00:00"}), releaser).id, admin
    )
    withdrawn = template_changes.create_draft(db, update(daily.id, {"start_time": "11:00:00"}), releaser)
    template_changes.withdraw_draft(db, withdrawn.id, releaser)

    for attempt in (
        lambda: template_changes.approve_draft(db, approved.id, admin),
        lambda: template_changes.reject_draft(db, approved.id, admin, "no"),
        lambda: template_changes.approve_draft(db, withdrawn.id, admin),
    ):
        with pytest.raises(BusinessException) as exc_info:
            attempt()
        refused(exc_info, ErrorCode.TEMPLATE_CHANGE_DECIDED)
        db.rollback()
    with pytest.raises(NotFoundError):
        template_changes.approve_draft(db, 999_999_999, admin)


# ==========================================
# 直接の保存の記録（API のときだけ・変わったときだけ）
# ==========================================
def test_direct_saves_are_recorded_only_when_something_changes(db, biz_id, admin):
    template = plan_services.create_template(db, RRuleTemplateCreate.model_validate(rrule_body(biz_id)), actor_id=admin)
    plan_services.update_template(db, template.id, PlanTemplateUpdate(start_time=time(9, 0)), actor_id=admin)  # 同じ値
    plan_services.update_template(db, template.id, PlanTemplateUpdate(start_time=time(8, 0)), actor_id=admin)
    plan_services.delete_template(db, template.id, actor_id=admin)

    rows = changes_of(db, biz_id)
    assert [(r.kind, r.is_direct, r.status) for r in rows] == [
        (TemplateChangeKind.CREATE, True, TemplateChangeStatus.APPROVED),
        (TemplateChangeKind.UPDATE, True, TemplateChangeStatus.APPROVED),
        (TemplateChangeKind.DELETE, True, TemplateChangeStatus.APPROVED),
    ]
    create, edit, delete = rows
    assert create.template_id == template.id and create.applied_revision == 1 and create.changes is not None
    assert edit.changes == {"start_time": "08:00:00"} and edit.before_values is not None
    assert edit.before_values["start_time"] == "09:00:00" and edit.base_revision == 1 and edit.applied_revision == 2
    assert delete.changes is None and delete.before_values is not None and delete.before_values["start_time"] == "08:00:00"
    assert all(r.drafted_by_id == r.decided_by_id == admin for r in rows)
    assert not any(PlanTemplateChangeResponse.model_validate(r).self_approved for r in rows)


def test_direct_date_changes_record_only_the_dates_that_changed(db, biz_id, admin):
    template = plan_services.create_template(db, ExplicitDatesTemplateCreate.model_validate(dict(
        biz_master_id=biz_id, name="Dated", template_type="explicit_dates", timezone="Asia/Tokyo",
        start_time="10:00:00", duration_minutes=30, dates=[_future(10)],
    )))  # actor なし → 残さない
    plan_services.add_explicit_dates(db, template.id, [_future(10)], actor_id=admin)               # 登録済みだけ → 残さない
    plan_services.add_explicit_dates(db, template.id, [_future(10), _future(15)], actor_id=admin)  # 15 日後だけが新しい
    plan_services.remove_explicit_date(db, template.id, _future(10), actor_id=admin)

    rows = changes_of(db, biz_id)
    assert [(r.add_dates, r.remove_dates, r.changes) for r in rows] == [
        ([_future(15)], [], {}),
        ([], [_future(10)], {}),
    ]
    assert rows[0].before_values is not None and rows[0].before_values["dates"] == [_future(10).isoformat()]


def test_service_calls_without_an_actor_record_nothing(db, biz_id, daily):
    plan_services.update_template(db, daily.id, PlanTemplateUpdate(start_time=time(7, 0)))
    plan_services.delete_template(db, daily.id)
    assert changes_of(db, biz_id) == []


# ==========================================
# 承認待ちの下書きがある業務は削除できない
# ==========================================
def test_a_business_with_pending_drafts_cannot_be_deleted(db, biz_id, releaser):
    draft = template_changes.create_draft(
        db, TemplateCreateDraft.model_validate({"kind": "create", "template": rrule_body(biz_id)}), releaser
    )
    with pytest.raises(BusinessException, match="1 template draft"):
        biz_services.delete_biz(db, biz_id)
    db.rollback()

    template_changes.withdraw_draft(db, draft.id, releaser)
    assert biz_services.delete_biz(db, biz_id).is_deleted is True


def test_approve_and_reject_are_admin_only():
    from fastapi.routing import APIRoute

    from src.domain_plan.router import router as plan_router
    from src.domain_staff.models import RoleCode

    wanted = {"/plans/template-changes/{change_id}/approve", "/plans/template-changes/{change_id}/reject"}
    routes = [r for r in plan_router.routes if isinstance(r, APIRoute) and r.path in wanted]
    assert len(routes) == 2
    for route in routes:
        assert set(getattr(route.endpoint, "__required_roles__")) == {RoleCode.ADMIN}, route.path
