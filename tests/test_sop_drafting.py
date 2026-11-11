# tests/test_sop_drafting.py
"""
細かな権限（P.2、2026-11-11）: リリース担当が SOP の草稿を書き、承認を依頼し、業務管理者が判断する。
- ロール: 草稿の作成・タグ・削除・ステップの操作・依頼・取り下げはリリース担当と業務管理者、承認・差し戻し・取消は業務管理者
- 作った人・最後に直した人と日時、記録（作成（写し元）・依頼・取り下げ・差し戻し（理由）・承認（誰の草稿か）・取消）
- 依頼中の草稿は誰も直せない・消せない・もう一度依頼できない（409 SOP_DRAFT_SUBMITTED）。ステップの無い草稿は依頼できない
- 取り下げは依頼した本人か業務管理者（ほかのリリース担当は 403）。依頼していない草稿の取り下げ・差し戻しは 409
- 差し戻しで草稿に戻り、また直せる。承認は依頼の有無によらずでき、依頼は外れる。自分の草稿の承認は記録で分かる
※ 顧客コードの先頭文字は空きがないため "S" を共有する
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.routing import APIRoute

from tests.client_codes import new_client_code
from tests.openapi_auth import needs_login
from src.core.error_codes import ErrorCode
from src.core.exceptions import BadRequestError, BusinessException, ForbiddenError
from src.domain_biz import services as biz
from src.domain_biz.models import SOPStatus, SOPVersion, SOPVersionEventType
from src.domain_biz.router import router as biz_router
from src.domain_biz.schemas import (
    BizMasterCreate, SOPStepOrderIn, SOPStepTemplateCreate, SOPStepTemplateUpdate, SOPVersionCreate, SOPVersionUpdate,
)
from src.domain_client.schemas import ClientCreate
from src.domain_staff import services as staff_services
from src.domain_staff.models import RoleCode, StaffStatus
from src.domain_staff.schemas import StaffCreate
from src.infrastructure.database import SessionLocal
from src.infrastructure.repositories import biz_master_repo, client_repo, sop_version_event_repo
from main import app

JST = timezone(timedelta(hours=9))
TOMORROW = (datetime.now(JST) + timedelta(days=1)).date()


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def world(db):
    code = new_client_code("S")
    client_repo.create(db, obj_in=ClientCreate(code=code, name="sop drafting client"))
    biz_id = biz_master_repo.create(db, obj_in=BizMasterCreate(
        client_code=code, name="sop drafting", business_seq=1, manager_email=None, description=None,
    )).id

    def staff(*roles: RoleCode) -> int:
        return staff_services.create_staff(
            db, StaffCreate(login_id="sd-" + uuid.uuid4().hex[:10], name="sop drafter", roles=list(roles)),
            actor=None, status=StaffStatus.ACTIVE,
        ).id

    return {"biz": biz_id, "releaser": staff(RoleCode.RELEASER), "other": staff(RoleCode.RELEASER), "admin": staff(RoleCode.ADMIN)}


def step(title: str = "確認する") -> SOPStepTemplateCreate:
    return SOPStepTemplateCreate.model_validate({"title": title, "condition": {"type": "always"}})


def events(db, version_id: int) -> list[tuple[SOPVersionEventType, int | None, str | None]]:
    return [(e.event_type, e.actor_id, e.note) for e in sop_version_event_repo.list_for_version(db, version_id=version_id)]


def code_of(exc: pytest.ExceptionInfo) -> str:
    assert isinstance(exc.value, BusinessException)
    return exc.value.error_code


def submitted_draft(db, w) -> SOPVersion:
    draft = biz.create_new_sop_version(db, w["biz"], SOPVersionCreate(version_tag="v1.0.0"), actor_id=w["releaser"])
    biz.create_step_template(db, draft.id, step(), actor_id=w["releaser"])
    return biz.submit_draft(db, draft.id, actor_id=w["releaser"])


# ==========================================
# ロール
# ==========================================
def test_drafting_routes_are_open_to_releasers_and_deciding_routes_to_admins():
    drafters = {
        ("/biz/{biz_id}/versions", "POST"), ("/biz/{biz_id}/versions/clone", "POST"),
        ("/biz/versions/{version_id}", "PATCH"), ("/biz/versions/{version_id}", "DELETE"),
        ("/biz/versions/{version_id}/steps", "POST"), ("/biz/versions/{version_id}/steps/order", "PUT"),
        ("/biz/steps/{step_id}", "PATCH"), ("/biz/steps/{step_id}", "DELETE"),
        ("/biz/versions/{version_id}/submit", "POST"), ("/biz/versions/{version_id}/withdraw", "POST"),
    }
    admins = {
        ("/biz/{biz_id}/versions/{draft_version_id}/approve", "POST"),
        ("/biz/{biz_id}/versions/{future_version_id}/cancel", "POST"),
        ("/biz/versions/{version_id}/reject", "POST"),
    }
    paths = app.openapi()["paths"]
    seen = set()
    for route in biz_router.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods or ():
            key = (route.path, method)
            if key in drafters:
                expected = {RoleCode.RELEASER, RoleCode.ADMIN}
            elif key in admins:
                expected = {RoleCode.ADMIN}
            else:
                continue
            seen.add(key)
            assert set(getattr(route.endpoint, "__required_roles__")) == expected, key
            op = paths[route.path][method.lower()]
            assert needs_login(op) and {"401", "403"} <= set(op["responses"]), key
    assert seen == drafters | admins


# ==========================================
# 作った人・直した人・記録
# ==========================================
def test_drafts_record_who_created_and_last_changed_them(db, world):
    first = biz.create_new_sop_version(db, world["biz"], SOPVersionCreate(version_tag="v1.0.0"), actor_id=world["releaser"])
    assert (first.created_by_id, first.updated_by_id) == (world["releaser"], world["releaser"])
    added = biz.create_step_template(db, first.id, step(), actor_id=world["other"])
    db.refresh(first)
    assert (first.created_by_id, first.updated_by_id) == (world["releaser"], world["other"])
    for change, actor in (
        (lambda: biz.update_step_template(db, added.id, SOPStepTemplateUpdate.model_validate({"title": "直した"}), actor_id=world["admin"]), world["admin"]),
        (lambda: biz.rename_draft(db, first.id, SOPVersionUpdate(version_tag="v1.0.1"), actor_id=world["releaser"]), world["releaser"]),
        (lambda: biz.reorder_steps(db, first.id, SOPStepOrderIn(step_ids=[added.id]), actor_id=world["other"]), world["other"]),
    ):
        before = first.updated_at
        change()
        db.refresh(first)
        assert first.updated_by_id == actor and before is not None and first.updated_at is not None and first.updated_at >= before
    assert events(db, first.id) == [(SOPVersionEventType.CREATED, world["releaser"], None)]

    # 承認して、その版を写した草稿: 写し元が記録に残る
    biz.approve_and_schedule_version(db, world["biz"], first.id, TOMORROW, actor_id=world["admin"])
    clone = biz.clone_active_to_draft(db, world["biz"], SOPVersionCreate(version_tag="v2.0.0"), actor_id=world["other"])
    (created,) = sop_version_event_repo.list_for_version(db, version_id=clone.id)
    assert created.event_type == SOPVersionEventType.CREATED and created.actor_id == world["other"]
    assert created.detail == {"cloned_from_version_id": first.id, "cloned_from_tag": "v1.0.1"}


# ==========================================
# 依頼
# ==========================================
def test_a_submitted_draft_cannot_be_changed_deleted_or_submitted_again(db, world):
    empty = biz.create_new_sop_version(db, world["biz"], SOPVersionCreate(version_tag="v0.1.0"), actor_id=world["releaser"])
    with pytest.raises(BadRequestError):
        biz.submit_draft(db, empty.id, actor_id=world["releaser"])  # ステップが無い

    draft = submitted_draft(db, world)
    assert (draft.submitted_by_id, draft.submitted_at is not None) == (world["releaser"], True)
    (only_step,) = draft.step_templates
    for attempt in (
        lambda: biz.submit_draft(db, draft.id, actor_id=world["releaser"]),
        lambda: biz.rename_draft(db, draft.id, SOPVersionUpdate(version_tag="v1.0.9"), actor_id=world["admin"]),
        lambda: biz.create_step_template(db, draft.id, step("足す"), actor_id=world["admin"]),
        lambda: biz.update_step_template(db, only_step.id, SOPStepTemplateUpdate.model_validate({"title": "x"}), actor_id=world["admin"]),
        lambda: biz.delete_step_template(db, only_step.id, actor_id=world["admin"]),
        lambda: biz.reorder_steps(db, draft.id, SOPStepOrderIn(step_ids=[only_step.id]), actor_id=world["admin"]),
        lambda: biz.delete_draft(db, draft.id),
    ):
        with pytest.raises(BusinessException) as exc:
            attempt()
        assert code_of(exc) == ErrorCode.SOP_DRAFT_SUBMITTED and exc.value.status_code == 409
        db.rollback()
    assert [e[0] for e in events(db, draft.id)] == [SOPVersionEventType.CREATED, SOPVersionEventType.SUBMITTED]


def test_withdrawing_is_for_the_submitter_or_an_admin(db, world):
    draft = submitted_draft(db, world)
    with pytest.raises(ForbiddenError):
        biz.withdraw_draft(db, draft.id, actor_id=world["other"], is_admin=False)
    db.rollback()
    biz.withdraw_draft(db, draft.id, actor_id=world["releaser"], is_admin=False)
    with pytest.raises(BusinessException) as exc:
        biz.withdraw_draft(db, draft.id, actor_id=world["releaser"], is_admin=False)  # もう依頼中ではない
    assert code_of(exc) == ErrorCode.SOP_DRAFT_NOT_SUBMITTED
    db.rollback()

    biz.submit_draft(db, draft.id, actor_id=world["releaser"])
    back = biz.withdraw_draft(db, draft.id, actor_id=world["admin"], is_admin=True)  # 業務管理者は誰の依頼でも
    assert (back.submitted_by_id, back.submitted_at, back.status) == (None, None, SOPStatus.DRAFT)
    assert [(e[0], e[1]) for e in events(db, draft.id)][1:] == [
        (SOPVersionEventType.SUBMITTED, world["releaser"]), (SOPVersionEventType.WITHDRAWN, world["releaser"]),
        (SOPVersionEventType.SUBMITTED, world["releaser"]), (SOPVersionEventType.WITHDRAWN, world["admin"]),
    ]


def test_a_rejected_draft_is_a_draft_again_with_the_reason_recorded(db, world):
    draft = submitted_draft(db, world)
    back = biz.reject_draft(db, draft.id, actor_id=world["admin"], reason="手順 2 が足りない")
    assert (back.submitted_at, back.status) == (None, SOPStatus.DRAFT)
    assert events(db, draft.id)[-1] == (SOPVersionEventType.REJECTED, world["admin"], "手順 2 が足りない")
    biz.create_step_template(db, draft.id, step("手順 2"), actor_id=world["releaser"])  # また直せる
    with pytest.raises(BusinessException) as exc:
        biz.reject_draft(db, draft.id, actor_id=world["admin"], reason="もう一度")
    assert code_of(exc) == ErrorCode.SOP_DRAFT_NOT_SUBMITTED


# ==========================================
# 承認・取消
# ==========================================
def test_approving_a_submitted_draft_records_whose_draft_it_was(db, world):
    draft = submitted_draft(db, world)
    approved = biz.approve_and_schedule_version(db, world["biz"], draft.id, TOMORROW, actor_id=world["admin"], note="OK")
    assert (approved.status, approved.submitted_by_id, approved.submitted_at) == (SOPStatus.APPROVED, None, None)
    last = sop_version_event_repo.list_for_version(db, version_id=draft.id)[-1]
    assert (last.event_type, last.actor_id, last.note) == (SOPVersionEventType.APPROVED, world["admin"], "OK")
    assert last.detail == {"effective_date": TOMORROW.isoformat(), "drafted_by_id": world["releaser"]}

    cancelled = biz.cancel_future_version(db, world["biz"], draft.id, actor_id=world["admin"])
    assert cancelled.status == SOPStatus.DRAFT and cancelled.submitted_at is None
    assert events(db, draft.id)[-1] == (SOPVersionEventType.CANCELLED, world["admin"], None)


def test_an_admin_approves_their_own_unsubmitted_draft(db, world):
    draft = biz.create_new_sop_version(db, world["biz"], SOPVersionCreate(version_tag="v1.0.0"), actor_id=world["admin"])
    biz.create_step_template(db, draft.id, step(), actor_id=world["admin"])
    biz.approve_and_schedule_version(db, world["biz"], draft.id, TOMORROW, actor_id=world["admin"])
    last = sop_version_event_repo.list_for_version(db, version_id=draft.id)[-1]
    assert last.event_type == SOPVersionEventType.APPROVED and last.detail is not None
    assert last.detail["drafted_by_id"] == last.actor_id == world["admin"]
