import enum
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.domain_biz.models import DependencyKind, DependencyMatchRule
from src.domain_ope.models import EventSource, OrderEventType, OrderStatus, StepEventType, StepState


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


class PinSopVersionIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    sop_version_id: int = Field(..., description="an approved (non-draft) version of the order's business")
    reason: str | None = Field(default=None, max_length=500)


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


class StepEventResponse(BaseModel):
    id: int
    event_type: StepEventType
    from_state: StepState | None
    to_state: StepState | None
    source: EventSource
    actor_staff_id: int | None
    reason: str | None
    detail: dict[str, Any] | None
    occurred_at: datetime

    model_config = ConfigDict(from_attributes=True)


class OrderStepResponse(BaseModel):
    """オーダーのステップ（リリース前はプレビュー: id と state は null）"""
    id: int | None = Field(description="null in a preview (pending order: steps are created at release)")
    step_template_id: int
    step_num: int = Field(description="the step template's number (gaps where a step's condition did not match)")
    title: str
    content: str | None
    is_blocking: bool
    state: StepState | None = Field(description="null in a preview")
    events: list[StepEventResponse] = Field(description="the step's events, oldest first (empty in a preview)")


class DependencyBiz(BaseModel):
    id: int
    business_code: str
    name: str


class OrderDependencyResponse(BaseModel):
    """このオーダーが待つ上流のオーダー（つながり1件）"""
    id: int
    kind: DependencyKind
    match_rule: DependencyMatchRule
    upstream_biz: DependencyBiz
    upstream_order_id: int | None = Field(description="null: no upstream order found (not met)")
    upstream_status: OrderStatus | None
    upstream_scheduled_start: datetime | None
    met: bool = Field(description="the upstream order has finished")
    overridden: bool = Field(description="a releaser / admin approved starting without it")


class OrderSopVersion(BaseModel):
    """オーダーが使う SOP バージョン（リリース後は固定したもの、リリース前は指定または予定日時に有効なもの）"""
    id: int
    version_tag: str
    source: Literal["frozen", "pinned", "effective"]


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
    sop_version: OrderSopVersion | None = Field(
        default=None, description="the SOP version this order runs (null: none approved / none was frozen at release)"
    )
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AutoReleaseResult(BaseModel):
    """自動リリースのジョブ（scripts/release_orders.py）の結果"""
    checked: int = Field(description="pending orders of AUTOMATIC businesses looked at")
    released: list[int] = Field(description="ids of the orders released by this run")


class OrderDetailResponse(OrderResponse):
    steps: list[OrderStepResponse] = Field(
        description="released: the order's steps; not released: a live preview from the SOP version it would run",
    )
    steps_preview: bool = Field(description="true while the steps are only a preview (not released yet)")
    dependencies: list[OrderDependencyResponse] = Field(description="upstream orders this order waits for (active links)")
    events: list[OrderEventResponse] = Field(description="the order's events, oldest first")
