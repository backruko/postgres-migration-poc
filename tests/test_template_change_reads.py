# tests/test_template_change_reads.py
"""
テンプレートの変更依頼を読む・承認の前に確かめる（Level 2、D.4）。
- 一覧（承認待ちは古い順、ほかは新しい順）・絞り込み・件数・1 件・テンプレートの変更履歴。画面用の名前つき
- テンプレートの応答に承認待ちの下書きの id（pending_change_id）
- 下書きの確認（新規作成・項目・指定日・削除）は、承認したときの結果と同じ。何も保存しない
※ 顧客コードの先頭文字は空きがないため "I" を共有する（fixture と helper は test_template_drafts.py のもの）
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from tests.openapi_auth import needs_login
from tests.test_template_drafts import (  # noqa: F401  (fixture は名前で使う)
    JST, UTC, _future, admin, biz_id, dated, daily, db, refused, releaser, rrule_body, state, update,
)
from src.coordination.template_preview import preview_template_create, preview_template_draft, preview_template_update
from src.core.error_codes import ErrorCode
from src.core.exceptions import BusinessException, NotFoundError
from src.domain_ope.models import OpeOrder, OrderStatus
from src.domain_plan import services as plan_services
from src.domain_plan import template_changes
from src.domain_plan.models import PlanTemplate, TemplateChangeKind, TemplateChangeStatus
from src.domain_plan.schemas import (
    PlanTemplateResponse, PlanTemplateUpdate, RRuleTemplateCreate, TemplateCreateDraft, TemplateDeleteDraft,
)


def live_starts(db, template_id: int, start: datetime, end: datetime) -> list[datetime]:
    db.expire_all()
    return sorted(
        i.scheduled_start for i in plan_services.get_instances(db, template_id, limit=5000)
        if not i.voided and start <= i.scheduled_start < end
    )


def preview_starts(occurrences, start: datetime) -> list[datetime]:
    return sorted(o.scheduled_start for o in occurrences if o.scheduled_start >= start)


def create_body(biz_id: int, name: str = "Proposed"):
    return TemplateCreateDraft.model_validate({"kind": "create", "template": rrule_body(biz_id, name=name, rrule="FREQ=WEEKLY;BYDAY=TU")})


# ==========================================
# 一覧・件数・1 件・履歴
# ==========================================
def test_lists_filters_counts_and_names(db, biz_id, daily, dated, releaser, admin):
    before_drafts = template_changes.count_changes(db, status=TemplateChangeStatus.DRAFT)
    first = template_changes.create_draft(db, update(daily.id, {"start_time": "10:00:00"}), releaser)
    second = template_changes.create_draft(db, update(dated.id, add_dates=[_future(40)]), releaser)
    proposed = template_changes.create_draft(db, create_body(biz_id), admin)
    template_changes.reject_draft(db, second.id, admin, "日付が違う")

    drafts = template_changes.list_changes(db, status=TemplateChangeStatus.DRAFT, biz_master_id=biz_id)
    assert [c.id for c in drafts] == [first.id, proposed.id]  # 承認待ちは古い順
    assert template_changes.count_changes(db, status=TemplateChangeStatus.DRAFT) == before_drafts + 2
    assert template_changes.count_changes(db, status=TemplateChangeStatus.DRAFT, biz_master_id=biz_id) == 2

    row = drafts[0]
    assert (row.drafted_by_name, row.template_name, row.business_name) == ("draft releaser", "Daily", "draft biz")
    assert row.business_code is not None and row.client_code is not None and row.business_code.startswith(row.client_code)
    assert drafts[1].template_name == "Proposed" and drafts[1].template_id is None  # 新規作成は提案された名前

    mine = template_changes.list_changes(db, drafted_by_id=releaser, biz_master_id=biz_id)
    assert [c.id for c in mine] == [second.id, first.id]  # 決まったもの・承認待ちを新しい順に
    rejected = template_changes.get_change(db, second.id)
    assert (rejected.status, rejected.decided_by_name, rejected.decision_note) == (
        TemplateChangeStatus.REJECTED, "draft admin", "日付が違う"
    )
    assert [c.id for c in template_changes.list_changes(db, template_id=daily.id)] == [first.id]
    assert len(template_changes.list_changes(db, biz_master_id=biz_id, limit=1)) == 1
    with pytest.raises(NotFoundError):
        template_changes.get_change(db, 999_999_999)


def test_template_history_is_newest_first_and_survives_deletion(db, biz_id, releaser, admin):
    template = plan_services.create_template(db, RRuleTemplateCreate.model_validate(rrule_body(biz_id)), actor_id=admin)
    plan_services.update_template(db, template.id, PlanTemplateUpdate(name="Renamed"), actor_id=admin)
    draft = template_changes.create_draft(db, update(template.id, {"duration_minutes": 45}), releaser)
    template_changes.approve_draft(db, draft.id, admin)
    delete = template_changes.create_draft(db, TemplateDeleteDraft(kind=TemplateChangeKind.DELETE, template_id=template.id), releaser)
    template_changes.approve_draft(db, delete.id, admin)

    history = template_changes.template_history(db, template.id)
    assert [(c.kind, c.is_direct) for c in history] == [
        (TemplateChangeKind.DELETE, False), (TemplateChangeKind.UPDATE, False),
        (TemplateChangeKind.UPDATE, True), (TemplateChangeKind.CREATE, True),
    ]
    assert all(c.template_name == "Renamed" for c in history)  # 削除済みでも今の名前を読める
    with pytest.raises(NotFoundError):
        template_changes.template_history(db, 999_999_999)


def test_templates_carry_the_pending_draft_id(db, biz_id, daily, releaser):
    draft = template_changes.create_draft(db, update(daily.id, {"start_time": "10:00:00"}), releaser)
    listed = {PlanTemplateResponse.model_validate(t).id: PlanTemplateResponse.model_validate(t) for t in plan_services.get_templates(db, biz_id)}
    assert listed[daily.id].pending_change_id == draft.id
    template_changes.withdraw_draft(db, draft.id, releaser)
    assert PlanTemplateResponse.model_validate(plan_services.get_template(db, daily.id)).pending_change_id is None


# ==========================================
# 下書きの確認 = 承認したときの結果
# ==========================================
def test_previewing_an_update_draft_matches_the_approval(db, biz_id, daily, releaser, admin):
    draft = template_changes.create_draft(db, update(daily.id, {"start_time": "11:15:00"}), releaser)
    before = state(db, biz_id)
    preview = preview_template_draft(db, draft.id, months=2)
    assert state(db, biz_id) == before  # 何も保存しない
    assert preview.kind == TemplateChangeKind.UPDATE and preview.create is None and preview.change is not None
    # 同じ本文の直接の確認と同じ
    direct = preview_template_update(db, daily.id, PlanTemplateUpdate.model_validate({"start_time": "11:15:00"}), months=2)
    assert len(preview.change.moved) == len(direct.moved) > 0

    template_changes.approve_draft(db, draft.id, admin)
    change = preview.change
    assert live_starts(db, daily.id, change.range_start, change.range_end) == preview_starts(change.occurrences, change.range_start)


def test_previewing_a_dates_draft_shows_the_dates_and_matches_the_approval(db, biz_id, dated, releaser, admin):
    draft = template_changes.create_draft(db, update(dated.id, add_dates=[_future(30)], remove_dates=[_future(10)]), releaser)
    preview = preview_template_draft(db, draft.id)
    change = preview.change
    assert change is not None and change.generation_changed is True
    assert [o.occurrence_date for o in change.added] == [_future(30)]
    assert [o.occurrence_date for o in change.removed] == [_future(10)]
    assert [e.effect for e in change.order_effects] == ["canceled"] and change.orders_created == 1

    template_changes.approve_draft(db, draft.id, admin)
    assert live_starts(db, dated.id, change.range_start, change.range_end) == preview_starts(change.occurrences, change.range_start)


def test_previewing_a_delete_draft_shows_every_future_date_removed_and_orders_cancelled(db, biz_id, daily, releaser, admin):
    draft = template_changes.create_draft(db, TemplateDeleteDraft(kind=TemplateChangeKind.DELETE, template_id=daily.id), releaser)
    preview = preview_template_draft(db, draft.id, months=1)
    change = preview.change
    assert change is not None and change.occurrences == [] and change.added == [] and change.moved == []
    assert len(change.removed) > 20 and change.orders_created == 0
    assert {e.effect for e in change.order_effects} == {"canceled"}
    open_orders = len(change.order_effects)

    template_changes.approve_draft(db, draft.id, admin)
    db.expire_all()
    canceled = db.scalars(select(OpeOrder).where(
        OpeOrder.template_id == daily.id, OpeOrder.status == OrderStatus.CANCELED,
        OpeOrder.scheduled_start >= change.range_start,
    )).all()
    assert len(canceled) == open_orders


def test_previewing_a_create_draft_matches_the_template_it_creates(db, biz_id, releaser, admin):
    draft = template_changes.create_draft(db, create_body(biz_id, "Weekly TU"), releaser)
    before = state(db, biz_id)
    preview = preview_template_draft(db, draft.id)
    assert state(db, biz_id) == before
    assert preview.kind == TemplateChangeKind.CREATE and preview.change is None and preview.create is not None
    direct = preview_template_create(db, create_body(biz_id, "Weekly TU").template)
    assert [o.scheduled_start for o in preview.create.occurrences] == [o.scheduled_start for o in direct.occurrences]

    approved = template_changes.approve_draft(db, draft.id, admin)
    assert approved.template_id is not None
    create = preview.create
    assert live_starts(db, approved.template_id, create.range_start, create.range_end) == preview_starts(create.occurrences, create.range_start)


def test_only_pending_drafts_can_be_previewed(db, daily, releaser, admin):
    draft = template_changes.create_draft(db, update(daily.id, {"start_time": "10:00:00"}), releaser)
    template_changes.withdraw_draft(db, draft.id, releaser)
    with pytest.raises(BusinessException) as exc_info:
        preview_template_draft(db, draft.id)
    refused(exc_info, ErrorCode.TEMPLATE_CHANGE_DECIDED)
    with pytest.raises(NotFoundError):
        preview_template_draft(db, 999_999_999)


def test_read_routes_are_open_and_the_draft_preview_is_for_releasers_and_admins():
    from fastapi.routing import APIRoute

    from main import app
    from src.coordination.router import router as ops_router
    from src.domain_plan.router import router as plan_router
    from src.domain_staff.models import RoleCode

    paths = app.openapi()["paths"]
    reads = {"/plans/template-changes", "/plans/template-changes/count", "/plans/template-changes/{change_id}",
             "/plans/templates/{template_id}/changes"}
    for path in reads:
        assert needs_login(paths[path]["get"]), path
    for route in plan_router.routes:
        if isinstance(route, APIRoute) and route.path in reads and "GET" in (route.methods or ()):
            assert getattr(route.endpoint, "__required_roles__", None) is None, route.path
    preview = [r for r in ops_router.routes if isinstance(r, APIRoute) and r.path == "/ops/template-changes/{change_id}/preview"]
    assert len(preview) == 1
    assert set(getattr(preview[0].endpoint, "__required_roles__")) == {RoleCode.RELEASER, RoleCode.ADMIN}
    assert needs_login(paths["/ops/template-changes/{change_id}/preview"]["post"])
