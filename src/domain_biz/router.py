from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from src.core.schemas.error import ErrorResponse, COMMON_ERRORS
from src.core.error_codes import ErrorCode
from src.infrastructure.database import get_db
from src.domain_biz.services import (
    create_biz, get_biz, get_bizes, update_biz, delete_biz, restore_biz, clone_active_to_draft, 
    create_step_template, approve_and_schedule_version, cancel_future_version,
    get_current_active_version, create_new_sop_version
)
from src.domain_biz.schemas import (
    BizMasterCreate, BizMasterUpdate, BizMasterResponse, SOPVersionCreate, 
    SOPVersionResponse, SOPVersionScheduleIn, SOPStepTemplateCreate, SOPStepTemplateResponse
)

router = APIRouter(prefix="/biz", tags=["biz"])

# ==========================================
# BizMaster エンドポイント
# ==========================================

@router.post("/", response_model=BizMasterResponse, status_code=status.HTTP_201_CREATED, responses={**COMMON_ERRORS})
def create_biz_endpoint(biz_in: BizMasterCreate, db: Session = Depends(get_db)):
    return create_biz(db=db, biz_in=biz_in)

@router.get("/{biz_id}", response_model=BizMasterResponse, responses={**COMMON_ERRORS})
def get_biz_endpoint(biz_id: int, db: Session = Depends(get_db)):
    return get_biz(db=db, biz_id=biz_id)

@router.get("/", response_model=list[BizMasterResponse], responses={**COMMON_ERRORS})
def get_bizes_endpoint(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    """
    BizMaster レコードを一覧取得します(ペイジング対応)。
    """
    return get_bizes(db=db, skip=skip, limit=limit)

@router.patch("/{biz_id}", response_model=BizMasterResponse, responses={**COMMON_ERRORS})
def update_biz_endpoint(biz_id: int, biz_in: BizMasterUpdate, db: Session = Depends(get_db)):
    return update_biz(db=db, biz_id=biz_id, biz_in=biz_in)

@router.delete("/{biz_id}", response_model=BizMasterResponse, responses={**COMMON_ERRORS})
def delete_biz_endpoint(biz_id: int, db: Session = Depends(get_db)):
    return delete_biz(db=db, biz_id=biz_id)

@router.post("/{biz_id}/restore", response_model=BizMasterResponse, responses={**COMMON_ERRORS})
def restore_biz_endpoint(biz_id: int, db: Session = Depends(get_db)):
    """
    論理削除された BizMaster レコードを復元します。
    """
    return restore_biz(db=db, biz_id=biz_id)

# ==========================================
# SOPVersion エンドポイント
# ==========================================

@router.post("/{biz_id}/versions/clone", response_model=SOPVersionResponse, status_code=status.HTTP_201_CREATED, responses={**COMMON_ERRORS})
def clone_active_to_draft_endpoint(biz_id: int, version_in: SOPVersionCreate, db: Session = Depends(get_db)):
    """
    【複数並行OK】現在有効な現行バージョンをベースに、ユーザーが指定した自由なタグ名で新しい草稿(DRAFT)を生成します。
    """
    return clone_active_to_draft(db=db, biz_id=biz_id, version_in=version_in)

@router.get("/{biz_id}/versions/current-active", response_model=SOPVersionResponse, responses={**COMMON_ERRORS})
def get_current_active_version_endpoint(biz_id: int, db: Session = Depends(get_db)):
    """
    【現在稼働中】現在の日時を基準にして、自動的に『今現場で使われている有効なバージョン』を1件取得します。
    """
    return get_current_active_version(db=db, biz_id=biz_id)

@router.post("/{biz_id}/versions/{draft_version_id}/approve", response_model=SOPVersionResponse, responses={**COMMON_ERRORS})
def approve_and_schedule_version_endpoint(biz_id: int, draft_version_id: int, schedule_in: SOPVersionScheduleIn, db: Session = Depends(get_db)):
    """
    【権限者専用】選ばれた特定の草稿(DRAFT)を承認し、未来の発効日に向けた予約枠(FUTURE)へ昇格させます。
    (自動的に現行版の終了期限がこの発効日の前日に連結されます)
    """
    return approve_and_schedule_version(
        db=db, 
        biz_id=biz_id, 
        draft_version_id=draft_version_id, 
        effective_at=schedule_in.effective_at
    )

@router.post("/{biz_id}/versions/{future_version_id}/cancel", response_model=SOPVersionResponse, responses={**COMMON_ERRORS})
def cancel_future_version_endpoint(biz_id: int, future_version_id: int, db: Session = Depends(get_db)):
    """
    【権限者専用】予約(FUTURE)を取り消し、現行版の期限を無期限(None)に自動復元した上で、データを再編集可能なDRAFTへと安全に引き戻します。
    """
    return cancel_future_version(db=db, biz_id=biz_id, future_version_id=future_version_id)

@router.post("/{biz_id}/versions", response_model=SOPVersionResponse, status_code=status.HTTP_201_CREATED, responses={**COMMON_ERRORS})
def create_new_sop_version_endpoint(biz_id: int, version_in: SOPVersionCreate, db: Session = Depends(get_db)):
    """
    【新規立ち上げ】特定の業務に、完全に新しい最初の草稿(DRAFT)を新規作成します。
    """
    return create_new_sop_version(db=db, biz_id=biz_id, version_in=version_in)

# ==========================================
# SOPStepTemplate エンドポイント
# ==========================================

@router.post("/versions/{version_id}/steps", response_model=SOPStepTemplateResponse, status_code=status.HTTP_201_CREATED, responses={**COMMON_ERRORS})
def create_step_template_endpoint(version_id: int, step_in: SOPStepTemplateCreate, db: Session = Depends(get_db)):
    """
    【現行・未来ロック】特定のSOPバージョン内に、作業手順(ステップ)を新規作成します。
    ※対象のバージョンがすでに現行(ACTIVE)または予約済(FUTURE)の場合はエラーになります。
    """
    return create_step_template(db=db, version_id=version_id, step_in=step_in)