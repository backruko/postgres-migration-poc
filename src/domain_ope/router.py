from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.core.schemas.error import AUTH_ERRORS, COMMON_ERRORS
from src.infrastructure.database import get_db
from src.domain_ope.schemas import ConflictResolveIn, OrderCommentIn, OrderDetailResponse, OrderReasonIn
from src.domain_ope.services import (
    approve_early_start, cancel_order, finish_order, get_order, hold_order, release_order, resolve_conflict,
    resume_order, start_order,
)
from src.domain_staff.auth import get_current_staff, require_active, require_roles
from src.domain_staff.models import RoleCode, StaffUser

router = APIRouter(prefix="/ope", tags=["ope"])

# 誰が操作できるか（2026-10-28 / 10-30 の決定）
OPERATOR = [RoleCode.OPERATOR]
HOLDERS = [RoleCode.OPERATOR, RoleCode.RELEASER, RoleCode.ADMIN]
PRIVILEGED = [RoleCode.RELEASER, RoleCode.ADMIN]  # リリース・取消・矛盾の解決

RESPONSES: dict[int | str, dict[str, Any]] = {**COMMON_ERRORS, **AUTH_ERRORS}
STAFF = Depends(get_current_staff)

# ==========================================
# Order（オーダー）エンドポイント
# ==========================================

@router.get("/orders/{order_id}", response_model=OrderDetailResponse, responses=RESPONSES)
@require_active()
def get_order_endpoint(order_id: int, db: Session = Depends(get_db)):
    """オーダーと、その記録（古い順）"""
    return get_order(db=db, order_id=order_id)

@router.post("/orders/{order_id}/release", response_model=OrderDetailResponse, responses=RESPONSES)
@require_roles(PRIVILEGED)
def release_order_endpoint(
    order_id: int, body: OrderCommentIn | None = None, db: Session = Depends(get_db), current_staff: StaffUser = STAFF,
):
    """pending → ready。自動リリースの業務でも、予定より早く手動でリリースできます。"""
    return release_order(db=db, order_id=order_id, actor_staff_id=current_staff.id, comment=(body or OrderCommentIn()).comment)

@router.post("/orders/{order_id}/approve-early-start", response_model=OrderDetailResponse, responses=RESPONSES)
@require_roles([RoleCode.RELEASER])
def approve_early_start_endpoint(
    order_id: int, body: OrderCommentIn | None = None, db: Session = Depends(get_db), current_staff: StaffUser = STAFF,
):
    """予定日より前の開始を承認します（承認後は予定日前でも開始できます）。"""
    return approve_early_start(db=db, order_id=order_id, actor_staff_id=current_staff.id, comment=(body or OrderCommentIn()).comment)

@router.post("/orders/{order_id}/start", response_model=OrderDetailResponse, responses=RESPONSES)
@require_roles(OPERATOR)
def start_order_endpoint(
    order_id: int, body: OrderCommentIn | None = None, db: Session = Depends(get_db), current_staff: StaffUser = STAFF,
):
    """ready → in_progress。予定日（日本時間）以降のみ。それより前はリリース担当の承認が必要です。矛盾があると開始できません。"""
    return start_order(db=db, order_id=order_id, actor_staff_id=current_staff.id, comment=(body or OrderCommentIn()).comment)

@router.post("/orders/{order_id}/hold", response_model=OrderDetailResponse, responses=RESPONSES)
@require_roles(HOLDERS)
def hold_order_endpoint(order_id: int, body: OrderReasonIn, db: Session = Depends(get_db), current_staff: StaffUser = STAFF):
    """pending / ready / in_progress → on_hold（理由必須。再開すると保留前の状態に戻ります）"""
    return hold_order(db=db, order_id=order_id, actor_staff_id=current_staff.id, reason=body.reason)

@router.post("/orders/{order_id}/resume", response_model=OrderDetailResponse, responses=RESPONSES)
@require_roles(HOLDERS)
def resume_order_endpoint(
    order_id: int, body: OrderCommentIn | None = None, db: Session = Depends(get_db), current_staff: StaffUser = STAFF,
):
    """on_hold → 保留前の状態"""
    return resume_order(db=db, order_id=order_id, actor_staff_id=current_staff.id, comment=(body or OrderCommentIn()).comment)

@router.post("/orders/{order_id}/finish", response_model=OrderDetailResponse, responses=RESPONSES)
@require_roles(OPERATOR)
def finish_order_endpoint(
    order_id: int, body: OrderCommentIn | None = None, db: Session = Depends(get_db), current_staff: StaffUser = STAFF,
):
    """in_progress → finished（ステップができるまでの暫定の完了操作）"""
    return finish_order(db=db, order_id=order_id, actor_staff_id=current_staff.id, comment=(body or OrderCommentIn()).comment)

@router.post("/orders/{order_id}/cancel", response_model=OrderDetailResponse, responses=RESPONSES)
@require_roles(PRIVILEGED)
def cancel_order_endpoint(order_id: int, body: OrderReasonIn, db: Session = Depends(get_db), current_staff: StaffUser = STAFF):
    """finished / canceled 以外 → canceled（理由必須）。plan の予定も取り消し、カレンダーからも消えます。"""
    return cancel_order(db=db, order_id=order_id, actor_staff_id=current_staff.id, reason=body.reason)

@router.post("/orders/{order_id}/resolve-conflict", response_model=OrderDetailResponse, responses=RESPONSES)
@require_roles(PRIVILEGED)
def resolve_conflict_endpoint(
    order_id: int, body: ConflictResolveIn, db: Session = Depends(get_db), current_staff: StaffUser = STAFF,
):
    """矛盾の解決: accept_plan（plan の新しい日時を採用）・keep（そのまま続ける）・cancel（取消。理由必須）"""
    return resolve_conflict(db=db, order_id=order_id, actor_staff_id=current_staff.id, resolve_in=body)
