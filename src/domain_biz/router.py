from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from src.core.schemas.error import ErrorResponse, COMMON_ERRORS
from src.core.error_codes import ErrorCode
from src.infrastructure.database import get_db
from src.domain_biz.services import (
    create_biz, get_biz, get_bizes, update_biz, delete_biz, restore_biz, clone_active_to_draft, 
    create_step_template, approve_and_schedule_version, cancel_future_version,
    get_current_active_version, list_versions, create_new_sop_version, list_dependencies, list_client_dependencies, create_dependency,
    delete_dependency, get_version, rename_draft, delete_draft, update_step_template, delete_step_template, reorder_steps,
    submit_draft, withdraw_draft, reject_draft, update_release, list_release_changes, describe_version, describe_versions,
    list_version_events, list_submitted_drafts, count_submitted_drafts,
)
from src.domain_biz.schemas import (
    BizMasterCreate, BizMasterUpdate, BizMasterResponse, SOPVersionCreate, 
    SOPVersionResponse, SOPVersionScheduleIn, SOPStepTemplateCreate, SOPStepTemplateResponse,
    BizDependencyCreate, BizDependencyResponse, SOPVersionDetailResponse, SOPVersionUpdate, SOPStepTemplateUpdate,
    SOPStepOrderIn, SOPVersionReject, BizReleaseUpdate, BizReleaseChangeResponse, SOPVersionEventResponse,
    SubmittedSOPDraftResponse, SubmittedSOPDraftCount,
)
from src.core.schemas.error import AUTH_ERRORS
from src.domain_staff.auth import get_current_staff, require_roles
from src.domain_staff.models import RoleCode, StaffUser

router = APIRouter(prefix="/biz", tags=["biz"])

# SOP の草稿を書く人（P.2、2026-11-11）: リリース担当も草稿を作る・直す・承認を依頼する。承認・差し戻し・取消は業務管理者
SOP_DRAFTERS = [RoleCode.RELEASER, RoleCode.ADMIN]

# ==========================================
# BizMaster エンドポイント
# ==========================================

@router.post("/", response_model=BizMasterResponse, status_code=status.HTTP_201_CREATED, responses={**COMMON_ERRORS, **AUTH_ERRORS})
@require_roles([RoleCode.ADMIN])
def create_biz_endpoint(biz_in: BizMasterCreate, db: Session = Depends(get_db)):
    return create_biz(db=db, biz_in=biz_in)

@router.get("/{biz_id}", response_model=BizMasterResponse, responses={**COMMON_ERRORS})
def get_biz_endpoint(biz_id: int, db: Session = Depends(get_db)):
    return get_biz(db=db, biz_id=biz_id)

@router.get("/", response_model=list[BizMasterResponse], responses={**COMMON_ERRORS})
def get_bizes_endpoint(
    skip: int = 0, limit: int = 100, client_code: str | None = None, include_deleted: bool = False, db: Session = Depends(get_db),
):
    """
    BizMaster レコードを業務コード順に一覧取得します(ペイジング対応)。
    client_code で顧客を絞り、include_deleted=true で論理削除された業務（is_deleted）も含めます。
    """
    return get_bizes(db=db, skip=skip, limit=limit, client_code=client_code, include_deleted=include_deleted)

@router.patch("/{biz_id}", response_model=BizMasterResponse, responses={**COMMON_ERRORS, **AUTH_ERRORS})
@require_roles([RoleCode.ADMIN])
def update_biz_endpoint(
    biz_id: int, biz_in: BizMasterUpdate, db: Session = Depends(get_db), current_staff: StaffUser = Depends(get_current_staff),
):
    """業務の設定を変えます（業務管理者）。リリース設定が変わったときは、その変更履歴も残ります。"""
    return update_biz(db=db, biz_id=biz_id, biz_in=biz_in, actor_id=current_staff.id)

@router.patch("/{biz_id}/release", response_model=BizMasterResponse, responses={**COMMON_ERRORS, **AUTH_ERRORS})
@require_roles([RoleCode.RELEASER, RoleCode.ADMIN])
def update_release_endpoint(
    biz_id: int, release_in: BizReleaseUpdate, db: Session = Depends(get_db), current_staff: StaffUser = Depends(get_current_staff),
):
    """
    業務のリリース設定（release_mode / release_time / release_days_before）だけを変えます（リリース担当・業務管理者。P.3）。
    送った項目だけ変わり、すぐに効きます（まだリリースされていないオーダーの自動リリースの日時も）。
    ほかの項目は受け付けません（422）。値が変わったときは変更履歴が残ります。
    """
    return update_release(db=db, biz_id=biz_id, release_in=release_in, actor_id=current_staff.id)

@router.get("/{biz_id}/release-changes", response_model=list[BizReleaseChangeResponse], responses={**COMMON_ERRORS})
def list_release_changes_endpoint(biz_id: int, db: Session = Depends(get_db)):
    """業務のリリース設定の変更履歴（新しい順。どの API で変えたものも）"""
    return list_release_changes(db=db, biz_id=biz_id)

@router.delete("/{biz_id}", response_model=BizMasterResponse, responses={**COMMON_ERRORS, **AUTH_ERRORS})
@require_roles([RoleCode.ADMIN])
def delete_biz_endpoint(biz_id: int, db: Session = Depends(get_db)):
    return delete_biz(db=db, biz_id=biz_id)

@router.post("/{biz_id}/restore", response_model=BizMasterResponse, responses={**COMMON_ERRORS, **AUTH_ERRORS})
@require_roles([RoleCode.ADMIN])
def restore_biz_endpoint(biz_id: int, db: Session = Depends(get_db)):
    """
    論理削除された BizMaster レコードを復元します。
    """
    return restore_biz(db=db, biz_id=biz_id)

# ==========================================
# SOPVersion エンドポイント
# ==========================================

@router.post("/{biz_id}/versions/clone", response_model=SOPVersionResponse, status_code=status.HTTP_201_CREATED, responses={**COMMON_ERRORS, **AUTH_ERRORS})
@require_roles(SOP_DRAFTERS)
def clone_active_to_draft_endpoint(
    biz_id: int, version_in: SOPVersionCreate, db: Session = Depends(get_db), current_staff: StaffUser = Depends(get_current_staff),
):
    """
    【新しい版】最新の承認済みの版（予定の版があればそれ、なければ現行）のステップを写した草稿を作ります。
    承認済みの版が無い業務（最初の SOP）は空の草稿になります。草稿は複数並べて作れます。
    """
    return describe_version(db, clone_active_to_draft(db=db, biz_id=biz_id, version_in=version_in, actor_id=current_staff.id))

@router.get("/{biz_id}/versions", response_model=list[SOPVersionResponse], responses={**COMMON_ERRORS})
def list_versions_endpoint(biz_id: int, include_drafts: bool = False, db: Session = Depends(get_db)):
    """
    業務の版を新しい順に返します。既定は承認済みだけ（オーダーに指定（pin）できる選択肢）。
    include_drafts=true で草稿も含めます。state は日付から決まる状態（draft / scheduled / current / past）です。
    """
    return describe_versions(db, list_versions(db=db, biz_id=biz_id, include_drafts=include_drafts))

@router.get("/versions/{version_id}", response_model=SOPVersionDetailResponse, responses={**COMMON_ERRORS})
def get_version_endpoint(version_id: int, db: Session = Depends(get_db)):
    """版と、そのステップ（番号順）"""
    version = get_version(db=db, version_id=version_id)
    return SOPVersionDetailResponse.model_validate(
        {**describe_version(db, version).model_dump(),
         "steps": sorted(version.step_templates, key=lambda s: s.step_num)}
    )


@router.get("/versions/{version_id}/events", response_model=list[SOPVersionEventResponse], responses={**COMMON_ERRORS})
def list_version_events_endpoint(version_id: int, db: Session = Depends(get_db)):
    """版の記録（古い順）: 作成（写し元）・承認の依頼・取り下げ・差し戻し（理由）・承認（誰の草稿か）・承認の取消"""
    return list_version_events(db=db, version_id=version_id)


@router.get("/sop-drafts/submitted", response_model=list[SubmittedSOPDraftResponse], responses={**COMMON_ERRORS})
def list_submitted_drafts_endpoint(client_code: str | None = Query(None), db: Session = Depends(get_db)):
    """承認を依頼された SOP の草稿（すべての業務。依頼の古い順。client_code で顧客を絞る）"""
    return list_submitted_drafts(db=db, client_code=client_code)


@router.get("/sop-drafts/submitted/count", response_model=SubmittedSOPDraftCount, responses={**COMMON_ERRORS})
def count_submitted_drafts_endpoint(db: Session = Depends(get_db)):
    """承認を依頼された SOP の草稿の数（業務管理者のメニューの件数）"""
    return SubmittedSOPDraftCount(count=count_submitted_drafts(db=db))

@router.patch("/versions/{version_id}", response_model=SOPVersionResponse, responses={**COMMON_ERRORS, **AUTH_ERRORS})
@require_roles(SOP_DRAFTERS)
def rename_draft_endpoint(
    version_id: int, version_in: SOPVersionUpdate, db: Session = Depends(get_db), current_staff: StaffUser = Depends(get_current_staff),
):
    """草稿のタグを変えます（承認済みの版は 400、承認を依頼中は 409 SOP_DRAFT_SUBMITTED）。"""
    return describe_version(db, rename_draft(db=db, version_id=version_id, version_in=version_in, actor_id=current_staff.id))

@router.delete("/versions/{version_id}", response_model=SOPVersionResponse, responses={**COMMON_ERRORS, **AUTH_ERRORS})
@require_roles(SOP_DRAFTERS)
def delete_draft_endpoint(version_id: int, db: Session = Depends(get_db)):
    """草稿をステップごと削除します（承認済みの版は 400、承認を依頼中は 409 SOP_DRAFT_SUBMITTED）。記録も消えます。"""
    return describe_version(db, delete_draft(db=db, version_id=version_id))

@router.get("/{biz_id}/versions/current-active", response_model=SOPVersionResponse, responses={**COMMON_ERRORS})
def get_current_active_version_endpoint(biz_id: int, db: Session = Depends(get_db)):
    """
    【現在稼働中】現在の日時を基準にして、自動的に『今現場で使われている有効なバージョン』を1件取得します。
    """
    return describe_version(db, get_current_active_version(db=db, biz_id=biz_id))

@router.post("/{biz_id}/versions/{draft_version_id}/approve", response_model=SOPVersionResponse, responses={**COMMON_ERRORS, **AUTH_ERRORS})
@require_roles([RoleCode.ADMIN])
def approve_and_schedule_version_endpoint(
    biz_id: int, draft_version_id: int, schedule_in: SOPVersionScheduleIn, db: Session = Depends(get_db),
    current_staff: StaffUser = Depends(get_current_staff),
):
    """
    【業務管理者】草稿を承認し、effective_date（明日以降）の 0:00（日本時間）から有効にします（状態は「予定」）。
    予定の版は業務ごとに 1 つまで。ステップの無い草稿は承認できません。今有効な版は、この発効日時で終わります。
    承認を依頼された草稿も、依頼されていない草稿も承認できます（自分の草稿も。記録に残る）。note は任意のひとこと。
    """
    return describe_version(db, approve_and_schedule_version(
        db=db,
        biz_id=biz_id,
        draft_version_id=draft_version_id,
        effective_date=schedule_in.effective_date,
        actor_id=current_staff.id,
        note=schedule_in.note,
    ))

@router.post("/{biz_id}/versions/{future_version_id}/cancel", response_model=SOPVersionResponse, responses={**COMMON_ERRORS, **AUTH_ERRORS})
@require_roles([RoleCode.ADMIN])
def cancel_future_version_endpoint(
    biz_id: int, future_version_id: int, db: Session = Depends(get_db), current_staff: StaffUser = Depends(get_current_staff),
):
    """
    【業務管理者】まだ発効していない（予定の）版の承認を取り消し、草稿に戻します。今有効な版の終了日時は外れます。
    発効した版・オーダーが指定（pin）している版・リリースで固定した版は取り消せません（400）。
    """
    return describe_version(db, cancel_future_version(db=db, biz_id=biz_id, future_version_id=future_version_id, actor_id=current_staff.id))

@router.post("/{biz_id}/versions", response_model=SOPVersionResponse, status_code=status.HTTP_201_CREATED, responses={**COMMON_ERRORS, **AUTH_ERRORS})
@require_roles(SOP_DRAFTERS)
def create_new_sop_version_endpoint(
    biz_id: int, version_in: SOPVersionCreate, db: Session = Depends(get_db), current_staff: StaffUser = Depends(get_current_staff),
):
    """
    【新規立ち上げ】特定の業務に、完全に新しい最初の草稿(DRAFT)を新規作成します。
    """
    return describe_version(db, create_new_sop_version(db=db, biz_id=biz_id, version_in=version_in, actor_id=current_staff.id))

# ==========================================
# SOPStepTemplate エンドポイント
# ==========================================

@router.post("/versions/{version_id}/steps", response_model=SOPStepTemplateResponse, status_code=status.HTTP_201_CREATED, responses={**COMMON_ERRORS, **AUTH_ERRORS})
@require_roles(SOP_DRAFTERS)
def create_step_template_endpoint(
    version_id: int, step_in: SOPStepTemplateCreate, db: Session = Depends(get_db), current_staff: StaffUser = Depends(get_current_staff),
):
    """
    草稿にステップを追加します（step_num を省くと最後）。承認済みの版は変えられません（400）。
    """
    return create_step_template(db=db, version_id=version_id, step_in=step_in, actor_id=current_staff.id)

@router.put("/versions/{version_id}/steps/order", response_model=list[SOPStepTemplateResponse], responses={**COMMON_ERRORS, **AUTH_ERRORS})
@require_roles(SOP_DRAFTERS)
def reorder_steps_endpoint(
    version_id: int, order_in: SOPStepOrderIn, db: Session = Depends(get_db), current_staff: StaffUser = Depends(get_current_staff),
):
    """草稿のステップを step_ids の順に並べ、番号を 1 から振り直します（すべてのステップを 1 回ずつ）。"""
    return reorder_steps(db=db, version_id=version_id, order_in=order_in, actor_id=current_staff.id)

@router.patch("/steps/{step_id}", response_model=SOPStepTemplateResponse, responses={**COMMON_ERRORS, **AUTH_ERRORS})
@require_roles(SOP_DRAFTERS)
def update_step_template_endpoint(
    step_id: int, step_in: SOPStepTemplateUpdate, db: Session = Depends(get_db), current_staff: StaffUser = Depends(get_current_staff),
):
    """草稿のステップを変えます（送った項目だけ。title / condition / is_blocking は null にできません）。"""
    return update_step_template(db=db, step_id=step_id, step_in=step_in, actor_id=current_staff.id)

@router.delete("/steps/{step_id}", response_model=SOPStepTemplateResponse, responses={**COMMON_ERRORS, **AUTH_ERRORS})
@require_roles(SOP_DRAFTERS)
def delete_step_template_endpoint(step_id: int, db: Session = Depends(get_db), current_staff: StaffUser = Depends(get_current_staff)):
    """草稿のステップを削除します（残りの番号はそのまま）。"""
    return delete_step_template(db=db, step_id=step_id, actor_id=current_staff.id)


# ==========================================
# SOP の草稿の承認の依頼（P.2、2026-11-11）
# ==========================================
@router.post("/versions/{version_id}/submit", response_model=SOPVersionResponse, responses={**COMMON_ERRORS, **AUTH_ERRORS})
@require_roles(SOP_DRAFTERS)
def submit_draft_endpoint(version_id: int, db: Session = Depends(get_db), current_staff: StaffUser = Depends(get_current_staff)):
    """
    草稿の承認を業務管理者に依頼します。依頼中は誰も直せません（409 SOP_DRAFT_SUBMITTED）。
    ステップの無い草稿は 400、もう依頼中なら 409 SOP_DRAFT_SUBMITTED。
    """
    return describe_version(db, submit_draft(db=db, version_id=version_id, actor_id=current_staff.id))


@router.post("/versions/{version_id}/withdraw", response_model=SOPVersionResponse, responses={**COMMON_ERRORS, **AUTH_ERRORS})
@require_roles(SOP_DRAFTERS)
def withdraw_draft_endpoint(version_id: int, db: Session = Depends(get_db), current_staff: StaffUser = Depends(get_current_staff)):
    """承認の依頼を取り下げ、草稿に戻します（依頼した本人か業務管理者。ほかの人は 403。依頼中でなければ 409 SOP_DRAFT_NOT_SUBMITTED）。"""
    return describe_version(db, withdraw_draft(
        db=db, version_id=version_id, actor_id=current_staff.id, is_admin=RoleCode.ADMIN in current_staff.role_codes,
    ))


@router.post("/versions/{version_id}/reject", response_model=SOPVersionResponse, responses={**COMMON_ERRORS, **AUTH_ERRORS})
@require_roles([RoleCode.ADMIN])
def reject_draft_endpoint(
    version_id: int, reject_in: SOPVersionReject, db: Session = Depends(get_db), current_staff: StaffUser = Depends(get_current_staff),
):
    """依頼された草稿を差し戻し、草稿に戻します（業務管理者。理由は必須。依頼中でなければ 409 SOP_DRAFT_NOT_SUBMITTED）。"""
    return describe_version(db, reject_draft(db=db, version_id=version_id, actor_id=current_staff.id, reason=reject_in.reason))


# ==========================================
# 業務間の依存関係（Phase 6）。定義の変更は業務管理者のみ（参照は他の biz エンドポイントと同じく制限なし）
# ==========================================

@router.get("/clients/{client_code}/dependencies", response_model=list[BizDependencyResponse], responses={**COMMON_ERRORS})
def list_client_dependencies_endpoint(client_code: str, db: Session = Depends(get_db)):
    """顧客の業務どうしの依存関係すべて"""
    return list_client_dependencies(db=db, client_code=client_code)

@router.get("/{biz_id}/dependencies", response_model=list[BizDependencyResponse], responses={**COMMON_ERRORS})
def list_dependencies_endpoint(biz_id: int, db: Session = Depends(get_db)):
    """この業務が上流または下流になっている依存関係"""
    return list_dependencies(db=db, biz_id=biz_id)

@router.post("/{biz_id}/dependencies", response_model=BizDependencyResponse, status_code=status.HTTP_201_CREATED, responses={**COMMON_ERRORS, **AUTH_ERRORS})
@require_roles([RoleCode.ADMIN])
def create_dependency_endpoint(biz_id: int, body: BizDependencyCreate, db: Session = Depends(get_db)):
    """この業務（下流）が上流の業務を待つ定義を作ります（同じ顧客の業務のみ。循環・二重登録は不可）。"""
    return create_dependency(db=db, downstream_biz_id=biz_id, dep_in=body)

@router.delete("/dependencies/{dependency_id}", response_model=BizDependencyResponse, responses={**COMMON_ERRORS, **AUTH_ERRORS})
@require_roles([RoleCode.ADMIN])
def delete_dependency_endpoint(dependency_id: int, db: Session = Depends(get_db)):
    """依存関係の定義を削除します。"""
    return delete_dependency(db=db, dependency_id=dependency_id)
