from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.core.schemas.error import AUTH_ERRORS, COMMON_ERRORS
from src.infrastructure.database import get_db
from src.domain_ope.schemas import ConflictResolveIn, OrderCommentIn, OrderDetailResponse, OrderReasonIn, PinSopVersionIn
from src.domain_ope.services import (
    approve_dependency_override, approve_early_start, approve_step, cancel_order, finish_order, get_order, hold_order, pin_sop_version, reject_step,
    release_order, resolve_conflict, resume_order, start_order, submit_step, unpin_sop_version,
)
from src.domain_staff.auth import get_current_staff, require_active, require_roles
from src.domain_staff.models import RoleCode, StaffUser

router = APIRouter(prefix="/ope", tags=["ope"])

# 誰が操作できるか（2026-10-28 / 10-30 の決定）
OPERATOR = [RoleCode.OPERATOR]
HOLDERS = [RoleCode.OPERATOR, RoleCode.RELEASER, RoleCode.ADMIN]
PRIVILEGED = [RoleCode.RELEASER, RoleCode.ADMIN]  # リリース・SOP バージョンの指定・取消・矛盾の解決

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

@router.post("/orders/{order_id}/pin", response_model=OrderDetailResponse, responses=RESPONSES)
@require_roles(PRIVILEGED)
def pin_sop_version_endpoint(order_id: int, body: PinSopVersionIn, db: Session = Depends(get_db), current_staff: StaffUser = STAFF):
    """リリース前のオーダーに使う SOP バージョンを指定します（同じ業務の承認済みバージョン。古い・新しいどちらでも可）。"""
    return pin_sop_version(db=db, order_id=order_id, actor_staff_id=current_staff.id, sop_version_id=body.sop_version_id, reason=body.reason)

@router.post("/orders/{order_id}/unpin", response_model=OrderDetailResponse, responses=RESPONSES)
@require_roles(PRIVILEGED)
def unpin_sop_version_endpoint(
    order_id: int, body: OrderCommentIn | None = None, db: Session = Depends(get_db), current_staff: StaffUser = STAFF,
):
    """指定を外し、予定日時に有効なバージョンに戻します（リリース前のみ）。"""
    return unpin_sop_version(db=db, order_id=order_id, actor_staff_id=current_staff.id, comment=(body or OrderCommentIn()).comment)

@router.post("/orders/{order_id}/approve-early-start", response_model=OrderDetailResponse, responses=RESPONSES)
@require_roles([RoleCode.RELEASER])
def approve_early_start_endpoint(
    order_id: int, body: OrderCommentIn | None = None, db: Session = Depends(get_db), current_staff: StaffUser = STAFF,
):
    """予定日より前の開始を承認します（承認後は予定日前でも開始できます）。"""
    return approve_early_start(db=db, order_id=order_id, actor_staff_id=current_staff.id, comment=(body or OrderCommentIn()).comment)

@router.post("/orders/{order_id}/approve-dependency-override", response_model=OrderDetailResponse, responses=RESPONSES)
@require_roles(PRIVILEGED)
def approve_dependency_override_endpoint(order_id: int, body: OrderReasonIn, db: Session = Depends(get_db), current_staff: StaffUser = STAFF):
    """満たされていない hard の依存関係のまま開始することを承認します（理由必須。開始は作業者が行います）。"""
    return approve_dependency_override(db=db, order_id=order_id, actor_staff_id=current_staff.id, reason=body.reason)

@router.post("/orders/{order_id}/start", response_model=OrderDetailResponse, responses=RESPONSES)
@require_roles(OPERATOR)
def start_order_endpoint(
    order_id: int, body: OrderCommentIn | None = None, db: Session = Depends(get_db), current_staff: StaffUser = STAFF,
):
    """ready → in_progress。予定日（日本時間）以降のみ（それより前はリリース担当の承認が必要）。矛盾があると開始できません。
    hard の依存関係は上流のオーダーの完了が必要です（満たされていなければ、リリース担当・業務管理者の承認が必要）。"""
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
    """in_progress → finished（ステップの無いオーダーのみ。ステップのあるオーダーは最後の承認で自動で完了します）"""
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


# ==========================================
# Step（ステップ）エンドポイント（作業中のオーダーのみ。返り値はオーダーの詳細）
# ==========================================

@router.post("/steps/{step_id}/submit", response_model=OrderDetailResponse, responses=RESPONSES)
@require_roles(OPERATOR)
def submit_step_endpoint(
    step_id: int, body: OrderCommentIn | None = None, db: Session = Depends(get_db), current_staff: StaffUser = STAFF,
):
    """todo → submitted。is_blocking でなければ次のステップが開きます。"""
    return submit_step(db=db, step_id=step_id, actor_staff_id=current_staff.id, comment=(body or OrderCommentIn()).comment)

@router.post("/steps/{step_id}/approve", response_model=OrderDetailResponse, responses=RESPONSES)
@require_roles([RoleCode.CHECKER])
def approve_step_endpoint(
    step_id: int, body: OrderCommentIn | None = None, db: Session = Depends(get_db), current_staff: StaffUser = STAFF,
):
    """submitted → approved（提出者以外）。is_blocking なら次のステップが開き、最後の承認でオーダーが完了します。"""
    return approve_step(db=db, step_id=step_id, actor_staff_id=current_staff.id, comment=(body or OrderCommentIn()).comment)

@router.post("/steps/{step_id}/reject", response_model=OrderDetailResponse, responses=RESPONSES)
@require_roles([RoleCode.CHECKER])
def reject_step_endpoint(step_id: int, body: OrderReasonIn, db: Session = Depends(get_db), current_staff: StaffUser = STAFF):
    """submitted → todo（提出者以外・理由必須）。そのステップだけが戻り、後のステップはそのままです。"""
    return reject_step(db=db, step_id=step_id, actor_staff_id=current_staff.id, reason=body.reason)
