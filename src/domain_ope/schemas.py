import enum
import json
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.domain_biz.models import CompletionMode, DependencyKind, DependencyMatchRule, ExecutionType
from src.domain_ope.models import EventSource, OrderEventType, OrderStatus, SignalResult, StepEventType, StepState


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


class OrderDependentResponse(BaseModel):
    """このオーダーを上流として待っている下流のオーダー（つながり1件）"""
    id: int
    kind: DependencyKind
    downstream_biz: DependencyBiz
    downstream_order_id: int
    downstream_status: OrderStatus
    downstream_scheduled_start: datetime


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
    late: bool = Field(default=False, description="past its scheduled end and not finished / canceled (computed)")
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
    execution_type: ExecutionType = Field(description="the business's execution type (LIGHTWEIGHT: client event, no steps)")
    completion_mode: CompletionMode = Field(description="the business's completion mode (AUTOMATIC: finished by a signal)")
    steps: list[OrderStepResponse] = Field(
        description="released: the order's steps; not released: a live preview from the SOP version it would run",
    )
    steps_preview: bool = Field(description="true while the steps are only a preview (not released yet)")
    dependencies: list[OrderDependencyResponse] = Field(description="upstream orders this order waits for (active links)")
    dependents: list[OrderDependentResponse] = Field(description="downstream orders waiting for this order (active links)")
    events: list[OrderEventResponse] = Field(description="the order's events, oldest first")


# ==========================================
# 信号（Phase 7）
# ==========================================
PAYLOAD_MAX_BYTES = 10_000


class SignalIn(BaseModel):
    """顧客側の出来事の信号（POST /ope/signals）。照合できなくても捨てずに残す"""
    model_config = ConfigDict(str_strip_whitespace=True)

    type: str = Field(..., min_length=1, max_length=50, description="signal type (the business's signal_type)")
    client_code: str = Field(..., min_length=1, max_length=10)
    key: str = Field(..., min_length=1, max_length=100, description="the order's rule date, YYYY-MM-DD")
    received_at: datetime = Field(..., description="when the sender received it (with a timezone offset)")
    payload: dict[str, Any] | None = Field(default=None, description="extra data for the record (never used for matching)")

    @field_validator("received_at")
    @classmethod
    def require_timezone(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("received_at must include a timezone offset")
        return v

    @field_validator("payload")
    @classmethod
    def limit_payload(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        if v is not None and len(json.dumps(v, ensure_ascii=False).encode("utf-8")) > PAYLOAD_MAX_BYTES:
            raise ValueError(f"payload must be at most {PAYLOAD_MAX_BYTES} bytes as JSON")
        return v


class SignalHandleIn(BaseModel):
    """照合できなかった信号への人の対応（order_id があれば、そのオーダーを完了にしてから対応済みにする）"""
    model_config = ConfigDict(str_strip_whitespace=True)

    note: str = Field(..., min_length=1, max_length=500)
    order_id: int | None = Field(default=None, description="a ready / in-progress order of the signal's client to finish")


class SignalResponse(BaseModel):
    id: int
    signal_type: str
    client_code: str
    key: str
    received_at: datetime
    recorded_at: datetime
    sender_staff_id: int
    payload: dict[str, Any] | None
    result: SignalResult
    matched_order_id: int | None
    result_note: str | None
    handled_at: datetime | None
    handled_by_staff_id: int | None
    handled_note: str | None

    model_config = ConfigDict(from_attributes=True)
