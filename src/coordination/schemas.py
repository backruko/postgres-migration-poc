from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

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


class DailyDashboardResponse(BaseModel):
    date: date
    timezone: str
    start: datetime = Field(description="start of the day in the given timezone")
    end: datetime = Field(description="start of the next day in the given timezone")
    total: int = Field(description="number of live occurrences on this day across all clients")
    calendars: list[DailyCalendarStatus] = Field(description="every business calendar, with the day's holiday status")
    clients: list[DailyClient] = Field(description="clients with something on this day, by client code")
