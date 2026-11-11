"""
テンプレートの変更依頼（下書き）: 作る・直す・取り下げる（Level 2、D.2）。承認・差し戻しは D.3。

- 下書きはリリース担当・業務管理者が書ける。直す・取り下げるのは書いた本人だけ
- 下書きは「どう変えるか」（直接の保存と同じ本文）だけを持つ。できる日付は持たない
- 下書きは本物の保存と同じ処理で確かめる: 試し実行（dry_run）で実際に当てはめて、必ず取り消す。
  誤りは直接の保存と同じ例外になり、何も変えない下書きは 400
- 承認待ちの下書きはテンプレートごとに 1 件。その間、テンプレートの直接の変更・削除・指定日・別の下書きは 409
  （services.refuse_if_draft_pending。テンプレートの行ロックの下で確かめるので、すれ違わない）
"""
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.core.error_codes import ErrorCode
from src.core.exceptions import BadRequestError, DuplicatedRecordError, ForbiddenError, NotFoundError
from src.domain_plan import services as plan_services
from src.domain_plan.models import PlanTemplateChange, TemplateChangeKind, TemplateChangeStatus
from src.domain_plan.schemas import TemplateCreateDraft, TemplateDeleteDraft, TemplateDraft, TemplateUpdateDraft
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


def withdraw_draft(db: Session, change_id: int, actor_id: int) -> PlanTemplateChange:
    """本人が承認待ちの下書きを取り下げます（記録は残る）。"""
    change = _get_own_draft(db, change_id, actor_id, "withdraw")
    change.status = TemplateChangeStatus.WITHDRAWN
    change.decided_by_id = actor_id
    change.decided_at = datetime.now(timezone.utc)
    return _save(db, change, change.template_id)
