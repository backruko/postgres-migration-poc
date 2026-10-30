import enum
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.domain_ope.models import EventSource, OrderEventType, OrderStatus


# ==========================================
# 入力（状態の操作）
# ==========================================
class OrderReasonIn(BaseModel):
    """理由が必須の操作（保留・取消）"""
    model_config = ConfigDict(str_strip_whitespace=True)

    reason: str = Field(..., min_length=1, max_length=500)


class OrderCommentIn(BaseModel):
    """理由・メモが任意の操作（リリース・開始・再開・完了・早期開始の承認）"""
    model_config = ConfigDict(str_strip_whitespace=True)

    comment: str | None = Field(default=None, max_length=500)


class ConflictAction(str, enum.Enum):
    """矛盾の解決方法"""
    ACCEPT_PLAN = "accept_plan"  # plan の新しい日時を採用（枠が消えていた場合は取消）
    KEEP = "keep"                # オーダーをそのまま続ける（フラグだけ外す）
    CANCEL = "cancel"            # オーダーを取り消す（plan の予定も取り消す）


class ConflictResolveIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    action: ConflictAction
    reason: str | None = Field(default=None, max_length=500, description="required when action is cancel")


# ==========================================
# Response
# ==========================================
class OrderEventResponse(BaseModel):
    id: int
    event_type: OrderEventType
    from_status: OrderStatus | None
    to_status: OrderStatus | None
    source: EventSource
    actor_staff_id: int | None
    reason: str | None
    detail: dict[str, Any] | None
    occurred_at: datetime

    model_config = ConfigDict(from_attributes=True)


class OrderResponse(BaseModel):
    id: int
    template_id: int
    occurrence_date: date
    source_sheet_event_id: int | None
    one_off_instance_id: int | None
    current_instance_id: int
    biz_master_id: int
    client_code: str
    scheduled_start: datetime
    scheduled_end: datetime
    status: OrderStatus
    status_before_hold: OrderStatus | None
    pinned_sop_version_id: int | None
    frozen_sop_version_id: int | None
    released_at: datetime | None
    conflict: bool
    conflict_reason: str | None
    early_start_approved: bool = Field(default=False, description="a releaser approved starting before the scheduled day")
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class OrderDetailResponse(OrderResponse):
    events: list[OrderEventResponse] = Field(description="the order's events, oldest first")
