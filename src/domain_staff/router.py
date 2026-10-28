from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from src.core.schemas.error import AUTH_ERRORS, COMMON_ERRORS
from src.infrastructure.database import get_db
from src.domain_staff.auth import get_current_staff, require_active, require_roles
from src.domain_staff.models import RoleCode, StaffUser
from src.domain_staff.schemas import (
    RoleResponse,
    StaffCreate,
    StaffResponse,
    StaffRolesUpdate,
    StaffStatusUpdate,
    StaffUpdate,
)
from src.domain_staff.services import (
    create_staff, get_roles, get_staff, get_staff_list, set_staff_roles, set_staff_status, update_staff,
)

router = APIRouter(prefix="/staff", tags=["staff"])

# 職員アカウントの管理はシステム管理者のみ。一覧の参照は業務管理者・監査担当も可
STAFF_READERS = [RoleCode.SYSTEM_ADMIN, RoleCode.ADMIN, RoleCode.AUDITOR]

# ==========================================
# 操作している職員
# ==========================================

@router.get("/me", response_model=StaffResponse, responses={**COMMON_ERRORS, **AUTH_ERRORS})
def get_me_endpoint(current_staff: StaffUser = Depends(get_current_staff)):
    """X-Staff-Id で指定した職員（利用開始前・利用停止中でも返します。画面で状態を表示するため）"""
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
    """有効化・利用停止・再開（ロールはそのまま残ります。自分の状態は変えられません）。"""
    return set_staff_status(db=db, staff_id=staff_id, status=status_in.status, actor=current_staff)
