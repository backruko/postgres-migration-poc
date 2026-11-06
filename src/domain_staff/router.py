from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from src.core.schemas.error import AUTH_ERRORS, COMMON_ERRORS
from src.infrastructure.database import get_db
from src.domain_staff.auth import get_current_staff, require_active, require_roles
from src.domain_staff.models import RoleCode, StaffUser
from src.domain_staff.schemas import (
    ApiKeyCreate,
    ApiKeyIssuedResponse,
    ApiKeyResponse,
    RoleResponse,
    TemporaryPasswordIn,
    StaffCreate,
    StaffResponse,
    StaffRolesUpdate,
    StaffStatusUpdate,
    StaffUpdate,
)
from src.domain_staff.services import (
    create_staff, get_roles, get_staff, get_staff_list, issue_api_key, list_api_keys, revoke_api_key, set_staff_roles,
    set_staff_status, set_temporary_password, update_staff,
)

router = APIRouter(prefix="/staff", tags=["staff"])

# 職員アカウントの管理はシステム管理者のみ。一覧の参照は業務管理者・監査担当も可
STAFF_READERS = [RoleCode.SYSTEM_ADMIN, RoleCode.ADMIN, RoleCode.AUDITOR]

# ==========================================
# 操作している職員
# ==========================================

@router.get("/me", response_model=StaffResponse, responses={**COMMON_ERRORS, **AUTH_ERRORS})
def get_me_endpoint(current_staff: StaffUser = Depends(get_current_staff)):
    """ログインしている職員（仮パスワードのままでも返します。画面で「パスワードを変更」を出すため）"""
    return current_staff

# ==========================================
# Role（ロール）エンドポイント
# ==========================================

@router.get("/roles", response_model=list[RoleResponse], responses={**COMMON_ERRORS, **AUTH_ERRORS})
@require_active()
def get_roles_endpoint(db: Session = Depends(get_db)):
    return get_roles(db=db)

# ==========================================
# StaffUser（職員）エンドポイント
# ==========================================

@router.get("/", response_model=list[StaffResponse], responses={**COMMON_ERRORS, **AUTH_ERRORS})
@require_roles(STAFF_READERS)
def get_staff_list_endpoint(db: Session = Depends(get_db)):
    return get_staff_list(db=db)

@router.get("/{staff_id}", response_model=StaffResponse, responses={**COMMON_ERRORS, **AUTH_ERRORS})
@require_roles(STAFF_READERS)
def get_staff_endpoint(staff_id: int, db: Session = Depends(get_db)):
    return get_staff(db=db, staff_id=staff_id)

@router.post("/", response_model=StaffResponse, status_code=status.HTTP_201_CREATED, responses={**COMMON_ERRORS, **AUTH_ERRORS})
@require_roles([RoleCode.SYSTEM_ADMIN])
def create_staff_endpoint(
    staff_in: StaffCreate, db: Session = Depends(get_db), current_staff: StaffUser = Depends(get_current_staff)
):
    """職員を登録します（利用開始前の状態で作られます。PUT /staff/{id}/status で有効化してください）。"""
    return create_staff(db=db, staff_in=staff_in, actor=current_staff)

@router.patch("/{staff_id}", response_model=StaffResponse, responses={**COMMON_ERRORS, **AUTH_ERRORS})
@require_roles([RoleCode.SYSTEM_ADMIN])
def update_staff_endpoint(staff_id: int, staff_in: StaffUpdate, db: Session = Depends(get_db)):
    return update_staff(db=db, staff_id=staff_id, staff_in=staff_in)

@router.put("/{staff_id}/roles", response_model=StaffResponse, responses={**COMMON_ERRORS, **AUTH_ERRORS})
@require_roles([RoleCode.SYSTEM_ADMIN])
def set_staff_roles_endpoint(
    staff_id: int, roles_in: StaffRolesUpdate, db: Session = Depends(get_db),
    current_staff: StaffUser = Depends(get_current_staff),
):
    """ロールを指定した集合に置き換えます（自分のシステム管理者ロールは外せません）。"""
    return set_staff_roles(db=db, staff_id=staff_id, codes=roles_in.roles, actor=current_staff)

@router.put("/{staff_id}/status", response_model=StaffResponse, responses={**COMMON_ERRORS, **AUTH_ERRORS})
@require_roles([RoleCode.SYSTEM_ADMIN])
def set_staff_status_endpoint(
    staff_id: int, status_in: StaffStatusUpdate, db: Session = Depends(get_db),
    current_staff: StaffUser = Depends(get_current_staff),
):
    """有効化・利用停止・再開（ロールはそのまま残ります。自分の状態は変えられません）。停止するとセッションも終わります。"""
    return set_staff_status(db=db, staff_id=staff_id, status=status_in.status, actor=current_staff)

# ==========================================
# パスワード・API キー（LG.2。システム管理者）
# ==========================================

@router.put("/{staff_id}/password", response_model=StaffResponse, responses={**COMMON_ERRORS, **AUTH_ERRORS})
@require_roles([RoleCode.SYSTEM_ADMIN])
def set_temporary_password_endpoint(
    staff_id: int, body: TemporaryPasswordIn, db: Session = Depends(get_db),
    current_staff: StaffUser = Depends(get_current_staff),
):
    """仮パスワードを設定します（新しい職員・忘れたとき）。本人は次のログインで変えます。ロックが解け、セッションは終わります。"""
    return set_temporary_password(db=db, staff_id=staff_id, password=body.password, actor=current_staff)

@router.get("/{staff_id}/api-keys", response_model=list[ApiKeyResponse], responses={**COMMON_ERRORS, **AUTH_ERRORS})
@require_roles([RoleCode.SYSTEM_ADMIN])
def list_api_keys_endpoint(staff_id: int, db: Session = Depends(get_db)):
    """職員の API キー（取り消したものも。新しい順。キーそのものは返しません）"""
    return list_api_keys(db=db, staff_id=staff_id)

@router.post("/{staff_id}/api-keys", response_model=ApiKeyIssuedResponse, status_code=status.HTTP_201_CREATED, responses={**COMMON_ERRORS, **AUTH_ERRORS})
@require_roles([RoleCode.SYSTEM_ADMIN])
def issue_api_key_endpoint(
    staff_id: int, body: ApiKeyCreate, db: Session = Depends(get_db), current_staff: StaffUser = Depends(get_current_staff),
):
    """信号の送信（signal_sender）の職員に API キーを発行します。キーはこの応答でだけ見せます（保存されるのはハッシュ）。"""
    issued = issue_api_key(db=db, staff_id=staff_id, label=body.label, actor=current_staff)
    return ApiKeyIssuedResponse.model_validate({**ApiKeyResponse.model_validate(issued.api_key).model_dump(), "key": issued.key})

@router.post("/api-keys/{key_id}/revoke", response_model=ApiKeyResponse, responses={**COMMON_ERRORS, **AUTH_ERRORS})
@require_roles([RoleCode.SYSTEM_ADMIN])
def revoke_api_key_endpoint(key_id: int, db: Session = Depends(get_db), current_staff: StaffUser = Depends(get_current_staff)):
    """API キーを取り消します（すぐに使えなくなります）。"""
    return revoke_api_key(db=db, key_id=key_id, actor=current_staff)
