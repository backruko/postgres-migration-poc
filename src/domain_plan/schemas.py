import json
from datetime import date, datetime, time
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.domain_plan.engine import DayUnit, HolidayPolicy
from src.domain_plan.models import (
    InstanceOrigin, TemplateType, VoidReason, validate_rrule_text, validate_timezone_name,
)

HEX_COLOR_PATTERN = r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$"
UI_METADATA_MAX_BYTES = 2000


# ==========================================
# 表示用メタデータ（カレンダーUIのスタイル）
# ==========================================
class UIMetadata(BaseModel):
    """
    既知のキーは型・形式を検証します。それ以外のキーも（UI側の拡張用に）そのまま保存します。
    例: {"bg_color": "#FF0000", "text_color": "#FFFFFF", "icon": "money-bag", "is_bold": true}
    """
    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)

    bg_color: str | None = Field(default=None, pattern=HEX_COLOR_PATTERN, description="background color (#RGB / #RRGGBB / #RRGGBBAA)")
    text_color: str | None = Field(default=None, pattern=HEX_COLOR_PATTERN, description="text color")
    icon: str | None = Field(default=None, max_length=50, description="icon name (e.g. 'money-bag')")
    is_bold: bool | None = None

    @model_validator(mode="after")
    def check_size(self):
        if len(json.dumps(self.to_dict(), ensure_ascii=False).encode("utf-8")) > UI_METADATA_MAX_BYTES:
            raise ValueError(f"ui_metadata must not exceed {UI_METADATA_MAX_BYTES} bytes")
        return self

    def to_dict(self) -> dict[str, Any]:
        """保存用の辞書（未指定・null のキーは保存しない）"""
        return self.model_dump(exclude_none=True)


def ui_metadata_to_dict(value: UIMetadata | None) -> dict[str, Any] | None:
    return value.to_dict() if value is not None else None


# ==========================================
# BusinessCalendar（営業日カレンダー）
# ==========================================
def _check_weekend_days(days: list[int]) -> list[int]:
    if any(d < 1 or d > 7 for d in days):
        raise ValueError("weekend_days must be ISO weekdays (1=Mon ... 7=Sun)")
    if len(set(days)) != len(days):
        raise ValueError("weekend_days must not contain duplicates")
    if len(days) >= 7:
        raise ValueError("at least one weekday must be a business day")
    return sorted(days)


class BusinessCalendarCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    code: str = Field(..., min_length=1, max_length=50, description="e.g. JP_BANK")
    name: str = Field(..., min_length=1, max_length=100)
    weekend_days: list[int] = Field(default_factory=lambda: [6, 7], description="ISO weekdays treated as weekend (1=Mon ... 7=Sun)")

    @field_validator("weekend_days")
    @classmethod
    def check_weekend_days(cls, v: list[int]) -> list[int]:
        return _check_weekend_days(v)


class BusinessCalendarUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=100)
    weekend_days: list[int] | None = None

    @field_validator("weekend_days")
    @classmethod
    def check_weekend_days(cls, v: list[int] | None) -> list[int] | None:
        return _check_weekend_days(v) if v is not None else v


class BusinessCalendarResponse(BaseModel):
    id: int
    code: str
    name: str
    weekend_days: list[int]

    model_config = ConfigDict(from_attributes=True)


class HolidayIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    holiday_date: date
    name: str | None = Field(default=None, max_length=100)


class HolidaysAdd(BaseModel):
    holidays: list[HolidayIn] = Field(..., min_length=1, max_length=5000)

    @model_validator(mode="after")
    def check_duplicates(self):
        dates = [h.holiday_date for h in self.holidays]
        duplicated = sorted({d for d in dates if dates.count(d) > 1})
        if duplicated:
            raise ValueError(f"duplicated holiday dates: {[d.isoformat() for d in duplicated]}")
        return self


class HolidayResponse(BaseModel):
    holiday_date: date
    name: str | None

    model_config = ConfigDict(from_attributes=True)


class CalendarChangeResult(BaseModel):
    holidays_added: int = 0
    holidays_renamed: int = 0
    holidays_deleted: int = 0
    templates_regenerated: int = Field(default=0, description="templates using this calendar that were re-reconciled")


# ==========================================
# ReferenceSheet（年間計画シート）
# ==========================================
class ReferenceSheetCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    client_code: str = Field(..., min_length=4, max_length=4, description="client code must be 4 characters")
    name: str = Field(..., max_length=100, description="sheet name")
    period_start: date
    period_end: date

    @model_validator(mode="after")
    def check_period(self):
        if self.period_end <= self.period_start:
            raise ValueError("period_end must be after period_start")
        return self


class MarkerRule(BaseModel):
    """生成マーカーのルール。例: 毎月27日、休日なら翌営業日"""
    model_config = ConfigDict(str_strip_whitespace=True)

    rrule: str = Field(..., description="e.g. FREQ=MONTHLY;BYMONTHDAY=27")
    anchor_date: date = Field(..., description="date the rule starts from (DTSTART)")
    business_calendar_id: int | None = None
    holiday_policy: HolidayPolicy = HolidayPolicy.AS_IS

    @field_validator("rrule")
    @classmethod
    def check_rrule(cls, v: str) -> str:
        return validate_rrule_text(v)

    @model_validator(mode="after")
    def check_calendar_required(self):
        if self.holiday_policy != HolidayPolicy.AS_IS and self.business_calendar_id is None:
            raise ValueError(f"holiday_policy '{self.holiday_policy.value}' requires business_calendar_id")
        return self


class ReferenceSheetMarkerCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    marker: str = Field(..., min_length=1, max_length=50, description="event marker (e.g. 'A', 'Delivery')")
    description: str | None = Field(default=None, max_length=200, description="what the marker means")
    rule: MarkerRule | None = Field(default=None, description="generate the dates from a rule instead of importing them")


class ReferenceSheetMarkerUpdate(BaseModel):
    """rule: 指定するとルールを置き換えて再生成（手動の日付は維持）。null で生成をやめ、現在の日付を通常の日付として残す"""
    model_config = ConfigDict(str_strip_whitespace=True)

    description: str | None = Field(default=None, max_length=200)
    rule: MarkerRule | None = None


class ReferenceSheetEventsCreate(BaseModel):
    dates: list[date] = Field(..., min_length=1, description="dates marked with this marker")


class ReferenceSheetEventUpdate(BaseModel):
    event_date: date


class ReferenceSheetEventResponse(BaseModel):
    id: int
    event_date: date
    rule_date: date | None = Field(default=None, description="generated markers: the rule date this date came from")
    is_manual: bool = Field(default=False, description="edited, added or cancelled by a person (kept on regeneration)")
    is_cancelled: bool = Field(default=False, description="a generated date cancelled by a person")

    model_config = ConfigDict(from_attributes=True)


class ReferenceSheetMarkerResponse(BaseModel):
    id: int
    marker: str
    description: str | None
    is_generated: bool
    rrule: str | None
    anchor_date: date | None
    business_calendar_id: int | None
    holiday_policy: HolidayPolicy
    events: list[ReferenceSheetEventResponse]

    model_config = ConfigDict(from_attributes=True)


class ReferenceSheetResponse(BaseModel):
    id: int
    client_code: str
    name: str
    period_start: date
    period_end: date
    markers: list[ReferenceSheetMarkerResponse]

    model_config = ConfigDict(from_attributes=True)


# ==========================================
# ReferenceSheet 一括インポート（顧客の年間計画シートJSONをそのまま受け付ける形式）
# ==========================================
IMPORT_MAX_MARKERS = 100
IMPORT_MAX_EVENTS = 20000


class ReferenceSheetImportMarker(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    marker: str = Field(..., min_length=1, max_length=50)
    description: str | None = Field(default=None, max_length=200)


class ReferenceSheetImportEvent(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    event_date: date
    marker: str = Field(..., min_length=1, max_length=50)


class ReferenceSheetImport(BaseModel):
    """
    例: mock_data/desensitized_mock_data.json
    {"client_code": "MOCK", "sheet_name": "...", "period_start": "...", "period_end": "...",
     "markers": [{"marker": "本締", "description": "..."}], "events": [{"event_date": "...", "marker": "本締"}]}
    """
    model_config = ConfigDict(str_strip_whitespace=True)

    client_code: str = Field(..., min_length=4, max_length=4, description="client code must be 4 characters")
    sheet_name: str = Field(..., min_length=1, max_length=100)
    period_start: date
    period_end: date
    markers: list[ReferenceSheetImportMarker] = Field(..., min_length=1, max_length=IMPORT_MAX_MARKERS)
    events: list[ReferenceSheetImportEvent] = Field(default_factory=list, max_length=IMPORT_MAX_EVENTS)

    @model_validator(mode="after")
    def check_consistency(self):
        """ファイル内の整合性をまとめて検証し、問題をすべて列挙して返します（部分的な取り込みはしません）"""
        problems: list[str] = []
        if self.period_end <= self.period_start:
            problems.append("period_end must be after period_start")

        names = [m.marker for m in self.markers]
        duplicated = sorted({n for n in names if names.count(n) > 1})
        if duplicated:
            problems.append(f"duplicated markers: {duplicated}")

        undeclared = sorted({e.marker for e in self.events} - set(names))
        if undeclared:
            problems.append(f"events use undeclared markers: {undeclared}")

        outside = sorted({e.event_date for e in self.events if not (self.period_start <= e.event_date <= self.period_end)})
        if outside:
            sample = [d.isoformat() for d in outside[:10]]
            problems.append(f"{len(outside)} event date(s) outside the sheet period, e.g. {sample}")

        if problems:
            raise ValueError("; ".join(problems))
        return self


class ReferenceSheetImportResult(BaseModel):
    created: bool = Field(description="true if a new sheet was created, false if an existing one was updated")
    markers_created: int
    markers_updated: int = Field(description="markers whose description changed")
    events_created: int
    events_deleted: int = Field(description="only with replace_events=true")
    templates_regenerated: int = Field(description="templates referencing a changed marker that were re-reconciled")
    sheet: ReferenceSheetResponse


# ==========================================
# PlanTemplate
# 1. Base（全タイプ共通の必須項目）
# ==========================================
class PlanTemplateBase(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    biz_master_id: int
    name: str = Field(..., max_length=100, description="plan name")
    timezone: str = Field(default="Asia/Tokyo", description="IANA timezone name")
    start_time: time = Field(..., description="local start time")
    duration_minutes: int = Field(..., gt=0, description="duration in minutes")
    business_calendar_id: int | None = Field(default=None, description="business calendar (weekends + holidays)")
    holiday_policy: HolidayPolicy = Field(
        default=HolidayPolicy.AS_IS,
        description="what to do when an occurrence falls on a non-business day "
                    "(as_is / skip / following / preceding / modified_following / modified_preceding)",
    )
    ui_metadata: UIMetadata | None = Field(default=None, description="calendar styling (inherited by instances)")

    @field_validator("timezone")
    @classmethod
    def check_timezone(cls, v: str) -> str:
        return validate_timezone_name(v)

    @model_validator(mode="after")
    def check_calendar_required(self):
        if self.holiday_policy != HolidayPolicy.AS_IS and self.business_calendar_id is None:
            raise ValueError(f"holiday_policy '{self.holiday_policy.value}' requires business_calendar_id")
        return self


# ==========================================
# 2. Create（タイプごとのスキーマを template_type で判別）
# ==========================================
class RRuleTemplateCreate(PlanTemplateBase):
    template_type: Literal[TemplateType.RRULE]
    anchor_date: date = Field(..., description="date part of DTSTART")
    rrule: str = Field(..., description="e.g. FREQ=WEEKLY;BYDAY=MO,WE")

    @field_validator("rrule")
    @classmethod
    def check_rrule(cls, v: str) -> str:
        return validate_rrule_text(v)


class ExplicitDatesTemplateCreate(PlanTemplateBase):
    template_type: Literal[TemplateType.EXPLICIT_DATES]
    dates: list[date] = Field(default_factory=list, description="client-specified dates")


class ReferenceSheetTemplateCreate(PlanTemplateBase):
    template_type: Literal[TemplateType.REFERENCE_SHEET_BASED]
    reference_sheet_id: int
    reference_event_marker: str = Field(..., min_length=1, max_length=50)
    offset_days: int = Field(default=0, ge=-366, le=366, description="shift of the base date (e.g. -3 = 3 days before)")
    offset_day_unit: DayUnit = Field(default=DayUnit.CALENDAR, description="calendar days or business days")
    offset_minutes: int = Field(default=0, description="time offset from (base date + start_time)")

    @model_validator(mode="after")
    def check_business_offset(self):
        if self.offset_day_unit == DayUnit.BUSINESS and self.business_calendar_id is None:
            raise ValueError("offset_day_unit 'business' requires business_calendar_id")
        return self


PlanTemplateCreate = Annotated[
    Union[RRuleTemplateCreate, ExplicitDatesTemplateCreate, ReferenceSheetTemplateCreate],
    Field(discriminator="template_type"),
]


# ==========================================
# 3. Update（部分更新。template_type と biz_master_id は変更不可）
# ==========================================
class PlanTemplateUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str | None = Field(default=None, max_length=100)
    timezone: str | None = None
    start_time: time | None = None
    duration_minutes: int | None = Field(default=None, gt=0)

    # RRULE 用
    anchor_date: date | None = None
    rrule: str | None = None

    # REFERENCE_SHEET_BASED 用
    reference_sheet_id: int | None = None
    reference_event_marker: str | None = Field(default=None, min_length=1, max_length=50)
    offset_days: int | None = Field(default=None, ge=-366, le=366)
    offset_day_unit: DayUnit | None = None
    offset_minutes: int | None = None

    # 休日の扱い（business_calendar_id は null でカレンダー解除。as_is 以外のポリシーや営業日オフセットでは不可）
    business_calendar_id: int | None = None
    holiday_policy: HolidayPolicy | None = None

    # 表示用（null でクリア。部分マージではなく全体を置き換えます）
    ui_metadata: UIMetadata | None = None

    @field_validator("timezone")
    @classmethod
    def check_timezone(cls, v: str | None) -> str | None:
        return validate_timezone_name(v) if v is not None else v

    @field_validator("rrule")
    @classmethod
    def check_rrule(cls, v: str | None) -> str | None:
        return validate_rrule_text(v) if v is not None else v


class ExplicitDatesAdd(BaseModel):
    dates: list[date] = Field(..., min_length=1)


# ==========================================
# 手動変更（オーバーライド / 取消 / 解除 / 単発追加）
# ==========================================
class _ManualTimeBase(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    scheduled_start: datetime = Field(..., description="start (with timezone offset)")
    scheduled_end: datetime | None = Field(default=None, description="end; defaults to the original / template duration")
    ui_metadata: UIMetadata | None = Field(default=None, description="calendar styling for this instance only")

    @model_validator(mode="after")
    def check_times(self):
        if self.scheduled_start.tzinfo is None or (self.scheduled_end and self.scheduled_end.tzinfo is None):
            raise ValueError("scheduled_start / scheduled_end must include a timezone offset")
        if self.scheduled_end is not None and self.scheduled_end <= self.scheduled_start:
            raise ValueError("scheduled_end must be after scheduled_start")
        return self


class PlanInstanceOverride(_ManualTimeBase):
    """ui_metadata を省略すると元インスタンスの値を引き継ぎ、null を指定するとクリアします"""
    note: str | None = Field(default=None, max_length=500, description="reason for the change")


class PlanOneOffCreate(_ManualTimeBase):
    pass


class PlanInstanceRevert(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    note: str | None = Field(default=None, max_length=500, description="reason for putting it back under the rule")


class PlanInstanceCancel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    note: str | None = Field(default=None, max_length=500, description="reason for the cancellation")


# ==========================================
# 4. Response
# ==========================================
class PlanExplicitDateResponse(BaseModel):
    local_date: date

    model_config = ConfigDict(from_attributes=True)


class PlanTemplateResponse(BaseModel):
    id: int
    biz_master_id: int
    name: str
    template_type: TemplateType
    timezone: str
    start_time: time
    duration_minutes: int
    anchor_date: date | None
    rrule: str | None
    reference_sheet_id: int | None
    reference_event_marker: str | None
    offset_days: int | None
    offset_day_unit: DayUnit | None
    offset_minutes: int | None
    business_calendar_id: int | None
    holiday_policy: HolidayPolicy
    ui_metadata: dict[str, Any] | None
    revision: int
    materialized_until: datetime | None
    explicit_dates: list[PlanExplicitDateResponse]

    model_config = ConfigDict(from_attributes=True)


class PlanInstanceResponse(BaseModel):
    id: int
    template_id: int
    template_revision: int
    scheduled_start: datetime
    scheduled_end: datetime
    source_sheet_event_id: int | None
    occurrence_date: date
    origin: InstanceOrigin
    overrides_instance_id: int | None
    ui_metadata: dict[str, Any] | None
    generated_at: datetime
    voided: bool
    void_reason: VoidReason | None
    void_note: str | None
    voided_at: datetime | None


class PlanRevertResponse(BaseModel):
    voided_instance: PlanInstanceResponse | None = Field(description="the manual override that was voided (none if the slot was cancelled)")
    restored_instance: PlanInstanceResponse | None = Field(description="the instance regenerated from the rule (none if the rule no longer produces it within the materialized range)")


class CacheRebuildResponse(BaseModel):
    template_id: int
    occurrences: int


class PlanOccurrenceResponse(BaseModel):
    instance_id: int
    template_id: int
    biz_master_id: int
    title: str
    starts_at: datetime
    ends_at: datetime
    is_override: bool
    origin: InstanceOrigin
    ui_metadata: dict[str, Any] | None = Field(description="effective styling (template merged with instance)")

    model_config = ConfigDict(from_attributes=True)
