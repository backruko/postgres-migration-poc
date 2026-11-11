"""
テンプレートの変更依頼（下書き）: 作る・直す・取り下げる（Level 2、D.2）、承認する・差し戻す（D.3）。

- 下書きはリリース担当・業務管理者が書ける。直す・取り下げるのは書いた本人だけ
- 下書きは「どう変えるか」（直接の保存と同じ本文）だけを持つ。できる日付は持たない
- 下書きは本物の保存と同じ処理で確かめる: 試し実行（dry_run）で実際に当てはめて、必ず取り消す。
  誤りは直接の保存と同じ例外になり、何も変えない下書きは 400
- 承認待ちの下書きはテンプレートごとに 1 件。その間、テンプレートの直接の変更・削除・指定日・別の下書きは 409
  （services.refuse_if_draft_pending。テンプレートの行ロックの下で確かめるので、すれ違わない）
"""
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.core.error_codes import ErrorCode
from src.core.exceptions import BadRequestError, DuplicatedRecordError, ForbiddenError, NotFoundError
from src.domain_plan import services as plan_services
from src.domain_biz.models import BizMaster
from src.domain_plan.models import PlanTemplate, PlanTemplateChange, TemplateChangeKind, TemplateChangeStatus
from src.domain_plan.schemas import (
    PlanTemplateChangeResponse, PlanTemplateUpdate, TemplateCreateDraft, TemplateDeleteDraft, TemplateDraft,
    TemplateUpdateDraft,
)
from src.domain_staff.models import StaffUser
from src.infrastructure.dry_run import dry_run
from src.infrastructure.repositories import plan_template_change_repo


def _check_draft(
    db: Session, draft: TemplateDraft, except_change_id: int | None
) -> tuple[int, int | None, dict[str, Any] | None]:
    """
    下書きを試し実行で本物と同じように当てはめ、取り消します。
    戻り値: (業務 ID, テンプレートの revision, 変更前の設定の写し)。新規作成は revision と写しが None。
    """
    try:
        with dry_run(db):
            if isinstance(draft, TemplateCreateDraft):
                plan_services._apply_template_create(db, draft.template)
                return draft.template.biz_master_id, None, None

            template = plan_services._get_locked_template(db, draft.template_id)
            plan_services.refuse_if_draft_pending(db, draft.template_id, except_change_id)
            biz_master_id, base_revision = template.biz_master_id, template.revision
            before = plan_services.template_settings(template)

            if isinstance(draft, TemplateDeleteDraft):
                plan_services._apply_template_delete(db, draft.template_id)
            else:
                if draft.changes.model_fields_set:
                    plan_services._apply_template_update(db, draft.template_id, draft.changes)
                plan_services._apply_explicit_dates(db, draft.template_id, add=draft.add_dates, remove=draft.remove_dates)
                if plan_services.template_settings(template) == before:
                    raise BadRequestError("This draft changes nothing on the template.")
            db.flush()
            return biz_master_id, base_revision, before
    except IntegrityError:
        raise DuplicatedRecordError("The change conflicts with an existing record.")


def _lock_unchanged_template(db: Session, template_id: int, before: dict[str, Any], except_change_id: int | None) -> None:
    """
    試し実行の取り消しで行ロックが外れるため、保存の前にもう一度ロックを取り、
    確かめたときから設定が変わっていないこと・ほかの下書きがないことを確かめます。
    """
    template = plan_services._get_locked_template(db, template_id)
    plan_services.refuse_if_draft_pending(db, template_id, except_change_id)
    if plan_services.template_settings(template) != before:
        raise DuplicatedRecordError(
            f"PlanTemplate(id={template_id}) changed while the draft was being checked; please try again.",
            error_code=ErrorCode.TEMPLATE_CHANGED_MEANWHILE,
        )


def _content(draft: TemplateDraft) -> dict[str, Any]:
    """下書きに残す中身（承認のときに同じ本文として読み直せる JSON）"""
    if isinstance(draft, TemplateCreateDraft):
        return {"changes": draft.template.model_dump(mode="json"), "add_dates": [], "remove_dates": []}
    if isinstance(draft, TemplateUpdateDraft):
        return {
            "changes": draft.changes.model_dump(mode="json", exclude_unset=True),
            "add_dates": draft.add_dates,
            "remove_dates": draft.remove_dates,
        }
    return {"changes": None, "add_dates": [], "remove_dates": []}


def _template_id(draft: TemplateDraft) -> int | None:
    return None if isinstance(draft, TemplateCreateDraft) else draft.template_id


def _save(db: Session, change: PlanTemplateChange, template_id: int | None, new: bool = False) -> PlanTemplateChange:
    try:
        if new:
            plan_template_change_repo.add(db, change=change)
        db.flush()
        db.commit()
    except IntegrityError:
        db.rollback()
        # 同じテンプレートに同時に下書きが書かれた（承認待ちは 1 件の一意インデックス）
        raise DuplicatedRecordError(
            f"PlanTemplate(id={template_id}) already has a pending draft.", error_code=ErrorCode.TEMPLATE_DRAFT_PENDING,
        )
    db.refresh(change)
    return change


def _get_own_draft(db: Session, change_id: int, actor_id: int, action: str) -> PlanTemplateChange:
    """行ロック付きで、本人の承認待ちの下書きを取得します"""
    change = plan_template_change_repo.get_locked(db, id=change_id)
    if change is None:
        raise NotFoundError(f"PlanTemplateChange(id={change_id}) not found.")
    if change.status != TemplateChangeStatus.DRAFT:
        raise DuplicatedRecordError(
            f"PlanTemplateChange(id={change_id}) is already {change.status.value}; it can no longer be changed.",
            error_code=ErrorCode.TEMPLATE_CHANGE_DECIDED,
        )
    if change.drafted_by_id != actor_id:
        raise ForbiddenError(f"Only the staff member who wrote this draft can {action} it.")
    return change


def create_draft(db: Session, draft: TemplateDraft, actor_id: int) -> PlanTemplateChange:
    """下書きを作ります（新規作成・変更・削除）。本物の保存と同じ確かめをして、通れば承認待ちとして残します。"""
    biz_master_id, base_revision, before = _check_draft(db, draft, except_change_id=None)
    template_id = _template_id(draft)
    if template_id is not None:
        assert before is not None
        _lock_unchanged_template(db, template_id, before, except_change_id=None)

    change = PlanTemplateChange(
        biz_master_id=biz_master_id, template_id=template_id, kind=draft.kind, status=TemplateChangeStatus.DRAFT,
        base_revision=base_revision, drafted_by_id=actor_id, **_content(draft),
    )
    return _save(db, change, template_id, new=True)


def edit_draft(db: Session, change_id: int, draft: TemplateDraft, actor_id: int) -> PlanTemplateChange:
    """
    本人が承認待ちの下書きを直します（中身をまるごと置き換える）。種類と対象のテンプレートは変えられません。
    削除の下書きには直すところがありません（取り下げて書き直す）。
    """
    change = _get_own_draft(db, change_id, actor_id, "edit")
    if draft.kind != change.kind or _template_id(draft) != change.template_id:
        raise BadRequestError("A draft keeps its kind and its template; withdraw it and write a new one instead.")
    if change.kind == TemplateChangeKind.DELETE:
        raise BadRequestError("A delete draft has nothing to edit.")

    biz_master_id, _, before = _check_draft(db, draft, except_change_id=change_id)
    # 試し実行の取り消しでロックが外れたので、取り直して確かめ直す
    change = _get_own_draft(db, change_id, actor_id, "edit")
    if change.template_id is not None:
        assert before is not None
        _lock_unchanged_template(db, change.template_id, before, except_change_id=change_id)

    change.biz_master_id = biz_master_id
    for field, value in _content(draft).items():
        setattr(change, field, value)
    return _save(db, change, change.template_id)


def _get_pending_draft(db: Session, change_id: int) -> PlanTemplateChange:
    """行ロック付きで、承認待ちの下書きを取得します（承認・差し戻し用。だれの下書きでもよい）"""
    change = plan_template_change_repo.get_locked(db, id=change_id)
    if change is None:
        raise NotFoundError(f"PlanTemplateChange(id={change_id}) not found.")
    if change.status != TemplateChangeStatus.DRAFT:
        raise DuplicatedRecordError(
            f"PlanTemplateChange(id={change_id}) is already {change.status.value}.",
            error_code=ErrorCode.TEMPLATE_CHANGE_DECIDED,
        )
    return change


def approve_draft(db: Session, change_id: int, actor_id: int, note: str | None = None) -> PlanTemplateChange:
    """
    業務管理者が下書きを承認し、すぐに反映します（1 つのトランザクション）。
    下書き → テンプレートの順にロックを取り、変更前の値を残してから、直接の保存と同じ処理で当てはめます
    （オーダーも直接の保存と同じように追従する）。当てはめで誤り（参照するシートが無いなど）になれば何も変えず、
    下書きは承認待ちのまま残ります。改訂番号は確かめません（承認待ちの間はテンプレートを変えられないため）。
    自分の下書きの承認もできます（履歴では self_approved）。
    """
    change = _get_pending_draft(db, change_id)
    # 下書きの行の値は、当てはめが終わってからまとめて変える（途中の autoflush で
    # 「承認待ちなのに変更前の値がある」行を書くと、DB の CHECK 制約に断られるため）
    before: dict[str, Any] | None = None
    try:
        if change.kind == TemplateChangeKind.CREATE:
            assert change.changes is not None
            body = TemplateCreateDraft.model_validate({"kind": TemplateChangeKind.CREATE, "template": change.changes})
            template = plan_services._apply_template_create(db, body.template)
        else:
            assert change.template_id is not None
            template = plan_services._get_locked_template(db, change.template_id)
            before = plan_services.template_settings(template)
            if change.kind == TemplateChangeKind.DELETE:
                plan_services._apply_template_delete(db, change.template_id)
            else:
                update = PlanTemplateUpdate.model_validate(change.changes or {})
                if update.model_fields_set:
                    plan_services._apply_template_update(db, change.template_id, update)
                plan_services._apply_explicit_dates(
                    db, change.template_id, add=list(change.add_dates), remove=list(change.remove_dates)
                )
        change.template_id = template.id
        change.before_values = before
        change.applied_revision = template.revision
        change.status = TemplateChangeStatus.APPROVED
        change.decided_by_id = actor_id
        change.decided_at = datetime.now(timezone.utc)
        change.decision_note = note
        db.flush()
        db.commit()
    except IntegrityError:
        db.rollback()
        raise DuplicatedRecordError("The change conflicts with an existing record; nothing was applied.")
    db.refresh(change)
    return change


def reject_draft(db: Session, change_id: int, actor_id: int, reason: str) -> PlanTemplateChange:
    """業務管理者が下書きを差し戻します（理由は必須。記録は残る）。テンプレートは何も変わりません。"""
    change = _get_pending_draft(db, change_id)
    change.status = TemplateChangeStatus.REJECTED
    change.decided_by_id = actor_id
    change.decided_at = datetime.now(timezone.utc)
    change.decision_note = reason
    return _save(db, change, change.template_id)


def withdraw_draft(db: Session, change_id: int, actor_id: int) -> PlanTemplateChange:
    """本人が承認待ちの下書きを取り下げます（記録は残る）。"""
    change = _get_own_draft(db, change_id, actor_id, "withdraw")
    change.status = TemplateChangeStatus.WITHDRAWN
    change.decided_by_id = actor_id
    change.decided_at = datetime.now(timezone.utc)
    return _save(db, change, change.template_id)


# ==========================================
# 読む: 一覧・件数・1 件・テンプレートの変更履歴（D.4。ログインしている有効な職員なら誰でも）
# ==========================================
def describe(db: Session, changes: list[PlanTemplateChange]) -> list[PlanTemplateChangeResponse]:
    """
    変更依頼を、画面にそのまま出せる形にします（職員の名前・業務・テンプレートの名前を付ける）。
    名前はまとめて読む（職員・業務・テンプレートでそれぞれ 1 回。削除済みの業務・テンプレートも読む）。
    """
    staff_ids = {c.drafted_by_id for c in changes} | {c.decided_by_id for c in changes if c.decided_by_id is not None}
    biz_ids = {c.biz_master_id for c in changes}
    template_ids = {c.template_id for c in changes if c.template_id is not None}
    names: dict[int, str] = {
        row.id: row.name for row in db.execute(select(StaffUser.id, StaffUser.name).where(StaffUser.id.in_(staff_ids)))
    } if staff_ids else {}
    bizs = {
        b.id: b for b in db.scalars(
            select(BizMaster).where(BizMaster.id.in_(biz_ids)).execution_options(include_deleted=True)
        )
    } if biz_ids else {}
    templates: dict[int, str] = {
        row.id: row.name for row in db.execute(
            select(PlanTemplate.id, PlanTemplate.name).where(PlanTemplate.id.in_(template_ids))
            .execution_options(include_deleted=True)
        )
    } if template_ids else {}

    result = []
    for change in changes:
        biz = bizs.get(change.biz_master_id)
        if change.template_id is not None:
            template_name = templates.get(change.template_id)
        else:
            template_name = (change.changes or {}).get("name")
        response = PlanTemplateChangeResponse.model_validate(change)
        result.append(response.model_copy(update={
            "drafted_by_name": names.get(change.drafted_by_id),
            "decided_by_name": names.get(change.decided_by_id) if change.decided_by_id is not None else None,
            "client_code": biz.client_code if biz else None,
            "business_code": biz.business_code if biz else None,
            "business_name": biz.name if biz else None,
            "template_name": template_name,
        }))
    return result


def list_changes(
    db: Session, *, status: TemplateChangeStatus | None = None, biz_master_id: int | None = None,
    template_id: int | None = None, drafted_by_id: int | None = None, limit: int = 100,
) -> list[PlanTemplateChangeResponse]:
    """変更依頼の一覧。承認待ちは古い順（承認の順番待ち）、それ以外は新しい順。"""
    return describe(db, plan_template_change_repo.get_list(
        db, status=status, biz_master_id=biz_master_id, template_id=template_id, drafted_by_id=drafted_by_id, limit=limit,
    ))


def count_changes(
    db: Session, *, status: TemplateChangeStatus | None = None, biz_master_id: int | None = None,
    drafted_by_id: int | None = None,
) -> int:
    return plan_template_change_repo.count(db, status=status, biz_master_id=biz_master_id, drafted_by_id=drafted_by_id)


def get_change(db: Session, change_id: int) -> PlanTemplateChangeResponse:
    change = plan_template_change_repo.get(db, id=change_id)
    if change is None:
        raise NotFoundError(f"PlanTemplateChange(id={change_id}) not found.")
    return describe(db, [change])[0]


def template_history(db: Session, template_id: int) -> list[PlanTemplateChangeResponse]:
    """テンプレートの変更履歴（新しい順。削除済みのテンプレートも読める）。無いテンプレートは 404。"""
    exists = db.scalar(
        select(PlanTemplate.id).where(PlanTemplate.id == template_id).execution_options(include_deleted=True)
    )
    if exists is None:
        raise NotFoundError(f"PlanTemplate(id={template_id}) not found.")
    return describe(db, plan_template_change_repo.get_history(db, template_id=template_id))
