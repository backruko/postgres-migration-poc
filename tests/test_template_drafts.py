# tests/test_template_drafts.py
"""
テンプレートの変更の下書き: 作る・直す・取り下げる（Level 2、D.2）。
- 下書きは本物の保存と同じ処理で確かめ（試し実行）、何も保存しない。誤りは直接の保存と同じ例外。何も変えない下書きは 400
- 下書きの中身は、承認のときに同じ本文として読み直せる
- 承認待ちはテンプレートごとに 1 件。その間、直接の変更・削除・指定日・別の下書きは 409。1 件ずつの予定の変更は止めない
- 直す・取り下げるのは書いた本人だけ。決まった下書きはもう直せない
※ 顧客コードの先頭文字は空きがないため "I" を共有する（1 回の実行の中では重複しない）
"""
import uuid
from datetime import date, datetime, time, timedelta, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from tests.client_codes import new_client_code
from tests.openapi_auth import needs_login
from src.core.error_codes import ErrorCode
from src.core.exceptions import BadRequestError, BusinessException, ForbiddenError, NotFoundError
from src.domain_biz.schemas import BizMasterCreate
from src.domain_client.schemas import ClientCreate
from src.domain_ope.models import OpeOrder, OpeOrderEvent
from src.domain_plan import services as plan_services
from src.domain_plan import template_changes
from src.domain_plan.models import (
    PlanInstance, PlanInstanceVoid, PlanTemplate, PlanTemplateChange, TemplateChangeKind, TemplateChangeStatus,
    TemplateType,
)
from src.domain_plan.schemas import (
    ExplicitDatesTemplateCreate, PlanInstanceCancel, PlanInstanceOverride, PlanOneOffCreate, PlanTemplateChangeResponse,
    PlanTemplateUpdate, RRuleTemplateCreate, TemplateCreateDraft, TemplateDeleteDraft, TemplateUpdateDraft,
)
from src.domain_staff import services as staff_services
from src.domain_staff.models import RoleCode, StaffStatus
from src.domain_staff.schemas import StaffCreate
from src.infrastructure.database import SessionLocal
from src.infrastructure.repositories import biz_master_repo, client_repo

UTC = timezone.utc
JST = timezone(timedelta(hours=9))


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
    code = new_client_code("I")
    client_repo.create(db, obj_in=ClientCreate(code=code, name="draft test client"))
    return biz_master_repo.create(
        db, obj_in=BizMasterCreate(client_code=code, name="draft biz", business_seq=1, manager_email=None, description=None)
    ).id


def _staff(db, role: RoleCode) -> int:
    return staff_services.create_staff(
        db, StaffCreate(login_id="d-" + uuid.uuid4().hex[:10], name=f"draft {role.value}", roles=[role]),
        actor=None, status=StaffStatus.ACTIVE,
    ).id


@pytest.fixture
def releaser(db):
    return _staff(db, RoleCode.RELEASER)


@pytest.fixture
def admin(db):
    return _staff(db, RoleCode.ADMIN)


def rrule_body(biz_id: int, **kwargs) -> dict:
    data = dict(
        biz_master_id=biz_id, name="Daily", template_type="rrule", timezone="Asia/Tokyo",
        start_time="09:00:00", duration_minutes=60, anchor_date="2026-01-01", rrule="FREQ=DAILY",
    )
    data.update(kwargs)
    return data


@pytest.fixture
def daily(db, biz_id) -> PlanTemplate:
    return plan_services.create_template(db, RRuleTemplateCreate.model_validate(rrule_body(biz_id)))


def _future(days: int) -> date:
    return datetime.now(JST).date() + timedelta(days=days)


@pytest.fixture
def dated(db, biz_id) -> PlanTemplate:
    return plan_services.create_template(db, ExplicitDatesTemplateCreate.model_validate(dict(
        biz_master_id=biz_id, name="Dated", template_type="explicit_dates", timezone="Asia/Tokyo",
        start_time="10:00:00", duration_minutes=30, dates=[_future(10), _future(20)],
    )))


def update(template_id: int, changes: dict | None = None, **kwargs) -> TemplateUpdateDraft:
    return TemplateUpdateDraft.model_validate(dict(kind="update", template_id=template_id, changes=changes or {}, **kwargs))


def state(db, biz_id: int) -> dict:
    """業務の予定・オーダーの状態（下書きで何も変わらないことを確かめる用）"""
    db.expire_all()
    template_ids = select(PlanTemplate.id).where(PlanTemplate.biz_master_id == biz_id).execution_options(include_deleted=True)
    instances = select(PlanInstance.id).where(PlanInstance.template_id.in_(template_ids))
    templates = db.scalars(
        select(PlanTemplate).where(PlanTemplate.biz_master_id == biz_id).execution_options(include_deleted=True)
        .order_by(PlanTemplate.id)
    ).all()
    return {
        "templates": [(t.id, t.revision, t.is_deleted, plan_services.template_settings(t)) for t in templates],
        "instances": db.scalar(select(func.count()).where(PlanInstance.template_id.in_(template_ids))),
        "voids": db.scalar(select(func.count()).select_from(PlanInstanceVoid).where(PlanInstanceVoid.instance_id.in_(instances))),
        "orders": db.scalar(select(func.count()).select_from(OpeOrder).where(OpeOrder.biz_master_id == biz_id)),
        "order_events": db.scalar(
            select(func.count()).select_from(OpeOrderEvent).join(OpeOrder, OpeOrder.id == OpeOrderEvent.order_id)
            .where(OpeOrder.biz_master_id == biz_id)
        ),
    }


def refused(exc_info, code: ErrorCode, status: int = 409) -> None:
    assert isinstance(exc_info.value, BusinessException)
    assert (exc_info.value.status_code, exc_info.value.error_code) == (status, code)


# ==========================================
# 作る: 何も保存しない・中身は承認のときに読み直せる
# ==========================================
def test_an_update_draft_changes_nothing_until_it_is_approved(db, biz_id, daily, releaser):
    before = state(db, biz_id)
    change = template_changes.create_draft(
        db, update(daily.id, {"start_time": "10:30:00", "ui_metadata": {"bg_color": "#FF0000", "icon": "x"}}), releaser
    )

    assert (change.kind, change.status, change.template_id, change.biz_master_id) == (
        TemplateChangeKind.UPDATE, TemplateChangeStatus.DRAFT, daily.id, biz_id
    )
    assert change.base_revision == before["templates"][0][1]
    assert change.drafted_by_id == releaser and change.decided_by_id is None
    # 送った項目だけを残す（承認のときに PATCH と同じ本文として読み直せる）
    assert change.changes is not None and set(change.changes) == {"start_time", "ui_metadata"}
    again = PlanTemplateUpdate.model_validate(change.changes)
    assert again.model_fields_set == {"start_time", "ui_metadata"}
    assert again.start_time == time(10, 30) and again.ui_metadata is not None and again.ui_metadata.to_dict() == {
        "bg_color": "#FF0000", "icon": "x"
    }
    assert state(db, biz_id) == before

    response = PlanTemplateChangeResponse.model_validate(change)
    assert response.self_approved is False and response.add_dates == [] and response.before_values is None


def test_a_create_draft_creates_no_template_and_keeps_the_whole_body(db, biz_id, releaser):
    before = state(db, biz_id)
    body = rrule_body(biz_id, name="Weekly", rrule="FREQ=WEEKLY;BYDAY=MO", ui_metadata={"is_bold": True})
    change = template_changes.create_draft(db, TemplateCreateDraft.model_validate({"kind": "create", "template": body}), releaser)

    assert change.kind == TemplateChangeKind.CREATE and change.template_id is None and change.base_revision is None
    assert state(db, biz_id) == before  # テンプレートも予定もできていない
    again = TemplateCreateDraft.model_validate({"kind": "create", "template": change.changes})
    assert again.template == RRuleTemplateCreate.model_validate(body)


def test_a_delete_draft_leaves_the_template_and_its_plans(db, biz_id, daily, releaser):
    before = state(db, biz_id)
    change = template_changes.create_draft(db, TemplateDeleteDraft(kind=TemplateChangeKind.DELETE, template_id=daily.id), releaser)

    assert change.kind == TemplateChangeKind.DELETE and change.changes is None
    assert state(db, biz_id) == before


def test_a_dates_draft_is_checked_like_the_real_date_endpoints(db, biz_id, dated, releaser):
    before = state(db, biz_id)
    change = template_changes.create_draft(
        db, update(dated.id, add_dates=[_future(30), _future(30)], remove_dates=[_future(10)]), releaser
    )
    assert change.add_dates == [_future(30)] and change.remove_dates == [_future(10)] and change.changes == {}
    assert state(db, biz_id) == before


# ==========================================
# 誤りは直接の保存と同じ。何も変えない下書きは断る
# ==========================================
def test_draft_errors_are_the_same_as_the_real_save(db, biz_id, daily, dated, releaser):
    before = state(db, biz_id)
    with pytest.raises(NotFoundError):  # 無いテンプレート
        template_changes.create_draft(db, update(999_999_999, {"start_time": "10:00:00"}), releaser)
    with pytest.raises(NotFoundError):
        template_changes.create_draft(db, TemplateDeleteDraft(kind=TemplateChangeKind.DELETE, template_id=999_999_999), releaser)
    with pytest.raises(BadRequestError):  # 型に合わない項目
        template_changes.create_draft(db, update(daily.id, {"offset_days": 2}), releaser)
    with pytest.raises(NotFoundError):  # 無い営業日カレンダー
        template_changes.create_draft(db, update(daily.id, {"holiday_policy": "following", "business_calendar_id": 999_999_999}), releaser)
    with pytest.raises(BadRequestError):  # 指定日はほかの型には足せない
        template_changes.create_draft(db, update(daily.id, add_dates=[_future(5)]), releaser)
    with pytest.raises(NotFoundError):  # 登録されていない指定日は外せない
        template_changes.create_draft(db, update(dated.id, remove_dates=[_future(11)]), releaser)
    with pytest.raises(NotFoundError):  # 無い業務に新規作成
        template_changes.create_draft(
            db, TemplateCreateDraft.model_validate({"kind": "create", "template": rrule_body(999_999_999)}), releaser
        )
    with pytest.raises(ValidationError):  # 同じ日を足して外す
        update(dated.id, add_dates=[_future(30)], remove_dates=[_future(30)])

    assert state(db, biz_id) == before
    assert db.scalar(select(func.count()).select_from(PlanTemplateChange).where(PlanTemplateChange.biz_master_id == biz_id)) == 0


def test_a_draft_that_changes_nothing_is_refused(db, daily, dated, releaser):
    for draft in (
        update(daily.id),                                     # 中身なし
        update(daily.id, {"start_time": "09:00:00", "name": "Daily"}),  # 今と同じ値
        update(dated.id, add_dates=[_future(10)]),            # 登録済みの日付だけ
    ):
        with pytest.raises(BadRequestError, match="changes nothing"):
            template_changes.create_draft(db, draft, releaser)


# ==========================================
# 承認待ちは 1 件。その間、テンプレートのほかの変更はできない
# ==========================================
def test_one_pending_draft_blocks_other_drafts_and_direct_changes(db, biz_id, daily, dated, releaser, admin):
    template_changes.create_draft(db, update(daily.id, {"start_time": "10:00:00"}), releaser)
    template_changes.create_draft(db, update(dated.id, add_dates=[_future(40)]), releaser)
    before = state(db, biz_id)

    blocked = [
        lambda: template_changes.create_draft(db, update(daily.id, {"name": "Other"}), admin),
        lambda: template_changes.create_draft(db, TemplateDeleteDraft(kind=TemplateChangeKind.DELETE, template_id=daily.id), admin),
        lambda: plan_services.update_template(db, daily.id, PlanTemplateUpdate(name="Direct")),
        lambda: plan_services.delete_template(db, daily.id),
        lambda: plan_services.add_explicit_dates(db, dated.id, [_future(50)]),
        lambda: plan_services.remove_explicit_date(db, dated.id, _future(10)),
    ]
    for attempt in blocked:
        with pytest.raises(BusinessException) as exc_info:
            attempt()
        refused(exc_info, ErrorCode.TEMPLATE_DRAFT_PENDING)
        assert "draft" in exc_info.value.message and "draft releaser" in exc_info.value.message
        db.rollback()
    assert state(db, biz_id) == before

    # 新規作成の下書きは、同じ業務にいくつでも置ける
    for name in ("A", "B"):
        template_changes.create_draft(
            db, TemplateCreateDraft.model_validate({"kind": "create", "template": rrule_body(biz_id, name=name)}), admin
        )


def test_single_occurrence_changes_are_not_blocked_by_a_draft(db, biz_id, daily, releaser):
    template_changes.create_draft(db, update(daily.id, {"start_time": "10:00:00"}), releaser)
    now = datetime.now(UTC)
    live = sorted(
        (i for i in plan_services.get_instances(db, daily.id, limit=500) if not i.voided and i.scheduled_start > now + timedelta(days=2)),
        key=lambda i: i.scheduled_start,
    )
    moved = live[0].scheduled_start + timedelta(hours=1)
    plan_services.override_instance(db, live[0].id, PlanInstanceOverride(scheduled_start=moved), actor_staff_id=releaser)
    plan_services.cancel_instance(db, live[1].id, PlanInstanceCancel(note="休み"), actor_staff_id=releaser)
    plan_services.add_one_off_instance(
        db, daily.id, PlanOneOffCreate(scheduled_start=live[2].scheduled_start + timedelta(hours=3)), actor_staff_id=releaser
    )


def test_withdrawing_frees_the_template_again(db, daily, releaser, admin):
    change = template_changes.create_draft(db, update(daily.id, {"start_time": "10:00:00"}), releaser)
    withdrawn = template_changes.withdraw_draft(db, change.id, releaser)

    assert withdrawn.status == TemplateChangeStatus.WITHDRAWN
    assert withdrawn.decided_by_id == releaser and withdrawn.decided_at is not None
    assert plan_services.update_template(db, daily.id, PlanTemplateUpdate(name="Direct")).name == "Direct"
    assert template_changes.create_draft(db, update(daily.id, {"duration_minutes": 45}), admin).status == TemplateChangeStatus.DRAFT


# ==========================================
# 直す・取り下げる: 本人だけ、承認待ちのあいだだけ
# ==========================================
def test_the_drafter_edits_the_whole_draft(db, biz_id, daily, dated, releaser):
    change = template_changes.create_draft(db, update(daily.id, {"start_time": "10:00:00", "name": "Renamed"}), releaser)
    edited = template_changes.edit_draft(db, change.id, update(daily.id, {"duration_minutes": 90}), releaser)
    assert edited.id == change.id and edited.changes == {"duration_minutes": 90}  # まるごと置き換え

    dates = template_changes.create_draft(db, update(dated.id, add_dates=[_future(40)]), releaser)
    edited = template_changes.edit_draft(db, dates.id, update(dated.id, remove_dates=[_future(20)]), releaser)
    assert (edited.add_dates, edited.remove_dates) == ([], [_future(20)])

    new = template_changes.create_draft(
        db, TemplateCreateDraft.model_validate({"kind": "create", "template": rrule_body(biz_id, name="New")}), releaser
    )
    edited = template_changes.edit_draft(
        db, new.id, TemplateCreateDraft.model_validate({"kind": "create", "template": rrule_body(biz_id, name="Newer")}), releaser
    )
    assert edited.changes is not None and edited.changes["name"] == "Newer" and edited.template_id is None

    # 直した中身も確かめる。誤りなら前の中身のまま
    with pytest.raises(BadRequestError):
        template_changes.edit_draft(db, change.id, update(daily.id, {"offset_days": 1}), releaser)
    db.expire_all()
    assert db.get(PlanTemplateChange, change.id).changes == {"duration_minutes": 90}  # type: ignore[union-attr]


def test_a_draft_keeps_its_kind_and_template(db, biz_id, daily, releaser):
    other = plan_services.create_template(db, RRuleTemplateCreate.model_validate(rrule_body(biz_id, name="Other")))
    change = template_changes.create_draft(db, update(daily.id, {"start_time": "10:00:00"}), releaser)
    with pytest.raises(BadRequestError):
        template_changes.edit_draft(db, change.id, update(other.id, {"start_time": "10:00:00"}), releaser)
    with pytest.raises(BadRequestError):
        template_changes.edit_draft(db, change.id, TemplateDeleteDraft(kind=TemplateChangeKind.DELETE, template_id=daily.id), releaser)

    delete = template_changes.create_draft(db, TemplateDeleteDraft(kind=TemplateChangeKind.DELETE, template_id=other.id), releaser)
    with pytest.raises(BadRequestError, match="nothing to edit"):
        template_changes.edit_draft(db, delete.id, TemplateDeleteDraft(kind=TemplateChangeKind.DELETE, template_id=other.id), releaser)


def test_only_the_drafter_edits_or_withdraws_and_only_while_pending(db, daily, releaser, admin):
    change = template_changes.create_draft(db, update(daily.id, {"start_time": "10:00:00"}), releaser)
    # 業務管理者でも、ほかの人の下書きは直せない・取り下げられない（承認か差し戻し）
    with pytest.raises(ForbiddenError):
        template_changes.edit_draft(db, change.id, update(daily.id, {"start_time": "11:00:00"}), admin)
    with pytest.raises(ForbiddenError):
        template_changes.withdraw_draft(db, change.id, admin)

    template_changes.withdraw_draft(db, change.id, releaser)
    for attempt in (
        lambda: template_changes.withdraw_draft(db, change.id, releaser),
        lambda: template_changes.edit_draft(db, change.id, update(daily.id, {"start_time": "11:00:00"}), releaser),
    ):
        with pytest.raises(BusinessException) as exc_info:
            attempt()
        refused(exc_info, ErrorCode.TEMPLATE_CHANGE_DECIDED)
        db.rollback()

    with pytest.raises(NotFoundError):
        template_changes.withdraw_draft(db, 999_999_999, releaser)


# ==========================================
# エンドポイント: ログインが必要。下書きはリリース担当・業務管理者、直接の保存は業務管理者だけのまま
# ==========================================
def test_draft_endpoints_are_for_releasers_and_admins_and_direct_writes_stay_admin_only():
    from fastapi.routing import APIRoute

    from main import app
    from src.domain_plan.router import router as plan_router

    drafts = {
        ("/plans/template-changes", "POST"),
        ("/plans/template-changes/{change_id}", "PUT"),
        ("/plans/template-changes/{change_id}/withdraw", "POST"),
    }
    admin_only = {
        ("/plans/templates", "POST"), ("/plans/templates/{template_id}", "PATCH"), ("/plans/templates/{template_id}", "DELETE"),
        ("/plans/templates/{template_id}/dates", "POST"), ("/plans/templates/{template_id}/dates/{local_date}", "DELETE"),
    }
    paths = app.openapi()["paths"]
    seen = set()
    for route in plan_router.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods or ():
            key = (route.path, method)
            if key in drafts:
                assert set(getattr(route.endpoint, "__required_roles__")) == {RoleCode.RELEASER, RoleCode.ADMIN}, key
                op = paths[route.path][method.lower()]
                assert needs_login(op) and {"401", "403"} <= set(op["responses"]), key
            elif key in admin_only:
                assert set(getattr(route.endpoint, "__required_roles__")) == {RoleCode.ADMIN}, key
            else:
                continue
            seen.add(key)
    assert seen == drafts | admin_only
