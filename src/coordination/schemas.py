from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from src.domain_ope.models import OrderStatus
from src.domain_plan.models import InstanceOrigin, VoidReason


# ==========================================
# 日次ダッシュボード（全顧客横断・参照のみ）
# 第1版: plan の予定 + 年間計画シートの日付 + 各営業日カレンダーの休日のみ（ope の状態は後のフェーズで追加）
# ==========================================
class DailyCalendarStatus(BaseModel):
    """その日が各営業日カレンダーで営業日かどうか"""
    calendar_id: int
    code: str
    name: str
    is_business_day: bool
    holiday_name: str | None = Field(default=None, description="holiday name when the day is a holiday in this calendar")


class DailyOrderSummary(BaseModel):
    """予定のオーダーの要約（詳細と操作は GET /ope/orders/{id}）"""
    id: int
    status: OrderStatus
    conflict: bool
    conflict_reason: str | None = None
    steps_total: int = Field(default=0, description="the order's steps (0: not released yet, or no steps)")
    steps_approved: int = Field(default=0, description="steps approved so far")
    blocked: bool = Field(default=False, description="hard dependencies not met (and not overridden): start is refused")
    late: bool = Field(default=False, description="past its deadline and not finished / canceled")
    due_soon: bool = Field(default=False, description="within the warning time before its deadline")
    deadline_at: datetime = Field(description="the order's stored deadline")


class DailyOccurrence(BaseModel):
    """その日に開始する有効な予定"""
    instance_id: int
    template_id: int
    title: str
    starts_at: datetime
    ends_at: datetime
    occurrence_date: date = Field(description="slot date by the rule (differs from the start date when a holiday moved it)")
    origin: InstanceOrigin
    is_override: bool
    source_sheet_event_id: int | None = Field(default=None, description="sheet date this occurrence was computed from")
    ui_metadata: dict[str, Any] | None = Field(description="effective styling (template merged with instance)")
    order: DailyOrderSummary | None = Field(
        default=None, description="the order of this occurrence (null: none, e.g. past occurrences before go-live)"
    )


class DailyVoidedItem(BaseModel):
    """その日に予定されていたが、無効化・取消された予定（理由付き）"""
    instance_id: int
    template_id: int
    title: str
    starts_at: datetime
    ends_at: datetime
    origin: InstanceOrigin
    void_reason: VoidReason
    void_note: str | None = Field(default=None, description="reason a person gave when changing / cancelling it")
    voided_at: datetime


class DailyBusiness(BaseModel):
    biz_master_id: int
    biz_code: str
    biz_name: str
    occurrences: list[DailyOccurrence] = Field(description="live occurrences starting this day, by start time")
    voided: list[DailyVoidedItem] = Field(description="occurrences voided or cancelled for this day, with reasons")


class DailyMilestone(BaseModel):
    """年間計画シート上のその日の日付（振込日・本締 など）"""
    event_id: int
    sheet_id: int
    sheet_name: str
    marker: str
    marker_description: str | None
    is_generated: bool = Field(description="the marker is rule-generated (otherwise imported)")
    rule_date: date | None = Field(default=None, description="the rule date this date came from")
    original_date: date | None = Field(default=None, description="the imported / rule date before a person moved or cancelled it")
    is_manual: bool = Field(description="edited, added or cancelled by a person")
    is_cancelled: bool = Field(description="cancelled by a person (kept as a record)")


class DailyClient(BaseModel):
    client_code: str
    client_name: str
    milestones: list[DailyMilestone] = Field(description="sheet dates of this client on this day")
    businesses: list[DailyBusiness] = Field(description="businesses with something on this day, by business code")


class AttentionItem(BaseModel):
    """遅れ・まもなくのオーダー1件（日次ダッシュボードの上の一覧用。詳細と操作は GET /ope/orders/{id}）"""
    order_id: int
    client_code: str
    biz_master_id: int
    biz_code: str
    biz_name: str
    template_id: int
    title: str = Field(description="the template's name")
    status: OrderStatus
    scheduled_start: datetime
    deadline_at: datetime
    warn_at: datetime | None
    late: bool = Field(description="past its deadline")
    due_soon: bool = Field(description="within the warning time before its deadline")


class AttentionResponse(BaseModel):
    """完了・取消以外で、遅れ・まもなくのオーダー（日付によらず、締切の早い順）"""
    now: datetime = Field(description="the time the list was computed for")
    items: list[AttentionItem]
    truncated: bool = Field(description="true if more orders matched than the limit")


class SlaRow(BaseModel):
    """顧客 × 月（締切の月、日本時間）の SLA。取消したオーダーは数えない"""
    month: str = Field(description="YYYY-MM (month of the deadline, JST)")
    client_code: str
    client_name: str
    finished: int = Field(description="finished (received) orders")
    late: int = Field(description="finished after their deadline")
    on_time_rate: float | None = Field(description="(finished - late) / finished * 100, 1 decimal (null: nothing finished)")
    avg_minutes_late: float | None = Field(description="average minutes after the deadline, late orders only (null: none late)")
    open_late: int = Field(description="not finished yet and already past their deadline (not in the other figures)")


class SlaResponse(BaseModel):
    """GET /ops/sla: 顧客 × 締切の月（月の昇順、同じ月は顧客コード順）"""
    from_month: str
    to_month: str
    now: datetime = Field(description="the time open orders were judged against")
    rows: list[SlaRow]


class DailyDashboardResponse(BaseModel):
    date: date
    timezone: str
    start: datetime = Field(description="start of the day in the given timezone")
    end: datetime = Field(description="start of the next day in the given timezone")
    total: int = Field(description="number of live occurrences on this day across all clients")
    calendars: list[DailyCalendarStatus] = Field(description="every business calendar, with the day's holiday status")
    clients: list[DailyClient] = Field(description="clients with something on this day, by client code")


# ==========================================
# テンプレートの変更の、保存の前の確認（POST /ops/templates/{id}/preview）
# ==========================================
class PreviewOccurrence(BaseModel):
    occurrence_date: date = Field(description="the rule date of the slot (local)")
    scheduled_start: datetime
    scheduled_end: datetime
    manual: bool = Field(description="changed or added by a person; a template change does not move it")


class PreviewMove(BaseModel):
    """同じ発生枠で、日時が変わる予定"""
    occurrence_date: date
    before_start: datetime
    before_end: datetime
    after_start: datetime
    after_end: datetime


class PreviewOrderEffect(BaseModel):
    order_id: int
    effect: Literal["rescheduled", "canceled", "conflict"] = Field(
        description="rescheduled: follows the plan; canceled: its slot disappears; conflict: left as is and flagged for a person"
    )
    status: OrderStatus = Field(description="status now (before the change)")
    scheduled_start: datetime = Field(description="scheduled start now (before the change)")
    new_scheduled_start: datetime | None = Field(description="rescheduled only")
    reason: str | None = Field(description="conflict only: the reason that would be recorded")


class TemplateChangePreview(BaseModel):
    """テンプレートをこの内容で変えたら何が起きるか（何も保存されていない）"""
    template_id: int
    range_start: datetime = Field(description="now")
    range_end: datetime = Field(description="end of the previewed range (the requested months, or the generated range if longer)")
    generation_changed: bool = Field(description="false: the change does not touch dates (name, deadline, display only)")
    occurrences: list[PreviewOccurrence] = Field(description="live occurrences in the range after the change")
    added: list[PreviewOccurrence]
    removed: list[PreviewOccurrence]
    moved: list[PreviewMove]
    manual_unchanged: int = Field(description="occurrences in the range that a person changed or added (kept as they are)")
    orders_total: int = Field(description="orders of this template that are not finished or canceled, before the change")
    orders_created: int = Field(description="new orders the change would create")
    order_effects: list[PreviewOrderEffect] = Field(description="existing orders the change would touch")
