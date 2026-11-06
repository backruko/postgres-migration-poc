from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from src.core.schemas.error import ErrorResponse, COMMON_ERRORS
from src.core.error_codes import ErrorCode
from src.infrastructure.database import get_db
from src.domain_biz.services import (
    create_biz, get_biz, get_bizes, update_biz, delete_biz, restore_biz, clone_active_to_draft, 
    create_step_template, approve_and_schedule_version, cancel_future_version,
    get_current_active_version, list_approved_versions, create_new_sop_version, list_dependencies, list_client_dependencies, create_dependency,
    delete_dependency,
)
from src.domain_biz.schemas import (
    BizMasterCreate, BizMasterUpdate, BizMasterResponse, SOPVersionCreate, 
    SOPVersionResponse, SOPVersionScheduleIn, SOPStepTemplateCreate, SOPStepTemplateResponse,
    BizDependencyCreate, BizDependencyResponse,
)
from src.core.schemas.error import AUTH_ERRORS
from src.domain_staff.auth import require_roles
from src.domain_staff.models import RoleCode

router = APIRouter(prefix="/biz", tags=["biz"])

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
def update_biz_endpoint(biz_id: int, biz_in: BizMasterUpdate, db: Session = Depends(get_db)):
    return update_biz(db=db, biz_id=biz_id, biz_in=biz_in)

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
@require_roles([RoleCode.ADMIN])
def clone_active_to_draft_endpoint(biz_id: int, version_in: SOPVersionCreate, db: Session = Depends(get_db)):
    """
    【複数並行OK】現在有効な現行バージョンをベースに、ユーザーが指定した自由なタグ名で新しい草稿(DRAFT)を生成します。
    """
    return clone_active_to_draft(db=db, biz_id=biz_id, version_in=version_in)

@router.get("/{biz_id}/versions", response_model=list[SOPVersionResponse], responses={**COMMON_ERRORS})
def list_approved_versions_endpoint(biz_id: int, db: Session = Depends(get_db)):
    """
    承認済み（草稿以外）のバージョンを新しい順に返します。オーダーに指定（pin）できるバージョンの選択肢です。
    status は保存された値です（有効かどうかは effective_at / expired_at で判断してください）。
    """
    return list_approved_versions(db=db, biz_id=biz_id)

@router.get("/{biz_id}/versions/current-active", response_model=SOPVersionResponse, responses={**COMMON_ERRORS})
def get_current_active_version_endpoint(biz_id: int, db: Session = Depends(get_db)):
    """
    【現在稼働中】現在の日時を基準にして、自動的に『今現場で使われている有効なバージョン』を1件取得します。
    """
    return get_current_active_version(db=db, biz_id=biz_id)

@router.post("/{biz_id}/versions/{draft_version_id}/approve", response_model=SOPVersionResponse, responses={**COMMON_ERRORS, **AUTH_ERRORS})
@require_roles([RoleCode.ADMIN])
def approve_and_schedule_version_endpoint(biz_id: int, draft_version_id: int, schedule_in: SOPVersionScheduleIn, db: Session = Depends(get_db)):
    """
    【業務管理者】草稿を承認し、effective_date（明日以降）の 0:00（日本時間）から有効にします（状態は「予定」）。
    予定の版は業務ごとに 1 つまで。ステップの無い草稿は承認できません。今有効な版は、この発効日時で終わります。
    """
    return approve_and_schedule_version(
        db=db,
        biz_id=biz_id,
        draft_version_id=draft_version_id,
        effective_date=schedule_in.effective_date
    )

@router.post("/{biz_id}/versions/{future_version_id}/cancel", response_model=SOPVersionResponse, responses={**COMMON_ERRORS, **AUTH_ERRORS})
@require_roles([RoleCode.ADMIN])
def cancel_future_version_endpoint(biz_id: int, future_version_id: int, db: Session = Depends(get_db)):
    """
    【業務管理者】まだ発効していない（予定の）版の承認を取り消し、草稿に戻します。今有効な版の終了日時は外れます。
    発効した版・オーダーが指定（pin）している版・リリースで固定した版は取り消せません（400）。
    """
    return cancel_future_version(db=db, biz_id=biz_id, future_version_id=future_version_id)

@router.post("/{biz_id}/versions", response_model=SOPVersionResponse, status_code=status.HTTP_201_CREATED, responses={**COMMON_ERRORS, **AUTH_ERRORS})
@require_roles([RoleCode.ADMIN])
def create_new_sop_version_endpoint(biz_id: int, version_in: SOPVersionCreate, db: Session = Depends(get_db)):
    """
    【新規立ち上げ】特定の業務に、完全に新しい最初の草稿(DRAFT)を新規作成します。
    """
    return create_new_sop_version(db=db, biz_id=biz_id, version_in=version_in)

# ==========================================
# SOPStepTemplate エンドポイント
# ==========================================

@router.post("/versions/{version_id}/steps", response_model=SOPStepTemplateResponse, status_code=status.HTTP_201_CREATED, responses={**COMMON_ERRORS, **AUTH_ERRORS})
@require_roles([RoleCode.ADMIN])
def create_step_template_endpoint(version_id: int, step_in: SOPStepTemplateCreate, db: Session = Depends(get_db)):
    """
    【現行・未来ロック】特定のSOPバージョン内に、作業手順(ステップ)を新規作成します。
    ※対象のバージョンがすでに現行(ACTIVE)または予約済(FUTURE)の場合はエラーになります。
    """
    return create_step_template(db=db, version_id=version_id, step_in=step_in)


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
