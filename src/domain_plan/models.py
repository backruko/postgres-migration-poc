import enum
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.rrule import rrulestr
from sqlalchemy import (
    CHAR,
    DDL,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    event,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSTZRANGE, Range
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates
from sqlalchemy.sql import func

from src.infrastructure.database import Base
from src.core.mixin import SoftDeleteMixin
from src.domain_plan.engine import DayUnit, HolidayPolicy


class TemplateType(str, enum.Enum):
    RRULE = "rrule"                                  # 通常の暦ベース繰り返し
    EXPLICIT_DATES = "explicit_dates"                # 顧客指定日（月単位で受領）
    REFERENCE_SHEET_BASED = "reference_sheet_based"  # 顧客の年間計画シート上のイベントから算出


class VoidReason(str, enum.Enum):
    TEMPLATE_REVISED = "template_revised"
    TEMPLATE_DELETED = "template_deleted"
    SOURCE_DATE_CHANGED = "source_date_changed"      # 参照元の日付（指定日・シート日付）が変更された
    MANUAL = "manual"
    REVERTED = "reverted"                            # 手動変更を取り消し、ルールの管理下に戻した
    CALENDAR_CHANGED = "calendar_changed"            # 営業日カレンダー（休日・週末）が変更された


class InstanceOrigin(str, enum.Enum):
    GENERATED = "generated"  # テンプレートのルールから生成
    OVERRIDE = "override"    # 既存インスタンスの手動変更（置き換え）
    ONE_OFF = "one_off"      # 発生枠に紐づかない単発の手動追加


def merge_ui_metadata(template_meta: dict[str, Any] | None, instance_meta: dict[str, Any] | None) -> dict[str, Any] | None:
    """表示用メタデータの実効値。テンプレートの値をベースに、インスタンス側のキーで上書きします。"""
    if template_meta is None and instance_meta is None:
        return None
    return {**(template_meta or {}), **(instance_meta or {})}


def validate_rrule_text(value: str) -> str:
    """RRULE文字列の検証（DTSTART は anchor_date から組み立てるため、文字列内での指定は禁止）"""
    if "DTSTART" in value.upper():
        raise ValueError("RRULE must not contain DTSTART. Use anchor_date and start_time instead.")
    try:
        rrulestr(value, dtstart=datetime(2000, 1, 1))
    except (ValueError, TypeError) as e:
        raise ValueError(f"Invalid RRULE: '{value}' ({e})")
    return value


def validate_timezone_name(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"Unknown timezone: '{value}'")
    return value


# ==========================================
# 0. BusinessCalendar（営業日カレンダー: 週末 + 休日。例: 日本の銀行休業日）
# ==========================================
class BusinessCalendar(Base):
    __tablename__ = "business_calendar"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, comment="カレンダーコード（例: JP_BANK）")
    name: Mapped[str] = mapped_column(String(100), comment="カレンダー名")
    weekend_days: Mapped[list[int]] = mapped_column(
        ARRAY(Integer), server_default=text("'{6,7}'"), comment="週末とする ISO 曜日（1=月 ... 7=日）"
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    holidays: Mapped[list["BusinessCalendarHoliday"]] = relationship(
        back_populates="calendar", cascade="all, delete-orphan", passive_deletes=True,
        order_by="BusinessCalendarHoliday.holiday_date",
    )

    __table_args__ = (
        CheckConstraint(
            "weekend_days <@ ARRAY[1,2,3,4,5,6,7] AND cardinality(weekend_days) < 7",
            name="ck_business_calendar_weekend_days",
        ),
    )


class BusinessCalendarHoliday(Base):
    __tablename__ = "business_calendar_holiday"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    calendar_id: Mapped[int] = mapped_column(ForeignKey("business_calendar.id", ondelete="CASCADE"), index=True)
    holiday_date: Mapped[date] = mapped_column(Date, nullable=False)
    name: Mapped[str | None] = mapped_column(String(100), comment="休日名（例: 元日）")

    calendar: Mapped["BusinessCalendar"] = relationship(back_populates="holidays")

    __table_args__ = (UniqueConstraint("calendar_id", "holiday_date", name="uq_business_calendar_holiday"),)


# ==========================================
# 1. ReferenceSheet（顧客の年間計画シート）
# ==========================================
class ReferenceSheet(Base):
    __tablename__ = "reference_sheet"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    client_code: Mapped[str] = mapped_column(
        CHAR(4), ForeignKey("client_master.code"), index=True, comment="顧客コード(ClientMaster外部キー)"
    )
    name: Mapped[str] = mapped_column(String(100), comment="シート名（例: 2026年度 物流計画）")
    period_start: Mapped[date] = mapped_column(Date, comment="シート対象期間の開始（12ヶ月）")
    period_end: Mapped[date] = mapped_column(Date, comment="シート対象期間の終了")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    markers: Mapped[list["ReferenceSheetMarker"]] = relationship(
        back_populates="sheet", cascade="all, delete-orphan", passive_deletes=True,
        order_by="ReferenceSheetMarker.marker",
    )

    __table_args__ = (
        UniqueConstraint("client_code", "name", name="uq_reference_sheet_client_name"),
        CheckConstraint("period_end > period_start", name="ck_reference_sheet_period"),
    )


class ReferenceSheetMarker(Base):
    """
    シート上のイベント種別（A〜G, 'Delivery' など）。
    日付は「取り込み（顧客のファイル）」か「ルールから生成（例: 毎月27日、休日なら翌営業日）」のどちらか。
    生成マーカーでも、人が編集・追加・取消した日付は常に優先され、再生成で上書きされません。
    """
    __tablename__ = "reference_sheet_marker"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    sheet_id: Mapped[int] = mapped_column(ForeignKey("reference_sheet.id", ondelete="CASCADE"), index=True)
    marker: Mapped[str] = mapped_column(String(50), comment="イベント記号")
    description: Mapped[str | None] = mapped_column(String(200), comment="イベントの説明（例: 決済代行会社への送付）")

    # --- 生成ルール（設定されている場合、日付はルールから生成される） ---
    rrule: Mapped[str | None] = mapped_column(Text, comment="日付生成ルール（例: FREQ=MONTHLY;BYMONTHDAY=27）")
    anchor_date: Mapped[date | None] = mapped_column(Date, comment="ルールの起点日（DTSTART）")
    business_calendar_id: Mapped[int | None] = mapped_column(
        ForeignKey("business_calendar.id", ondelete="RESTRICT", name="fk_reference_sheet_marker_business_calendar_id"),
        index=True, comment="生成時に使う営業日カレンダー",
    )
    holiday_policy: Mapped[HolidayPolicy] = mapped_column(
        Enum(HolidayPolicy, name="plan_holiday_policy_enum"),
        default=HolidayPolicy.AS_IS, server_default=HolidayPolicy.AS_IS.name, nullable=False,
        comment="生成日が非営業日の場合の扱い",
    )

    sheet: Mapped["ReferenceSheet"] = relationship(back_populates="markers")
    events: Mapped[list["ReferenceSheetEvent"]] = relationship(
        back_populates="marker_def", cascade="all, delete-orphan", passive_deletes=True,
        order_by="ReferenceSheetEvent.event_date",
    )

    __table_args__ = (
        # テンプレート側の複合FKの参照先
        UniqueConstraint("sheet_id", "marker", name="uq_reference_sheet_marker"),
        CheckConstraint("(rrule IS NULL) = (anchor_date IS NULL)", name="ck_reference_sheet_marker_rule_fields"),
        CheckConstraint(
            "holiday_policy = 'AS_IS' OR business_calendar_id IS NOT NULL",
            name="ck_reference_sheet_marker_calendar_required",
        ),
    )

    @property
    def is_generated(self) -> bool:
        return self.rrule is not None

    @validates("rrule")
    def validate_rrule(self, key, value):
        return validate_rrule_text(value) if value is not None else value

    @validates("marker")
    def validate_marker(self, key, value):
        if value is not None:
            cleaned_value = value.strip()
            if not cleaned_value:
                raise ValueError("Marker must not be empty.")
            return cleaned_value
        return value


class ReferenceSheetEvent(Base):
    """マーカーが付いた具体的な日付"""
    __tablename__ = "reference_sheet_event"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    marker_id: Mapped[int] = mapped_column(
        ForeignKey("reference_sheet_marker.id", ondelete="CASCADE"), index=True
    )
    event_date: Mapped[date] = mapped_column(Date, nullable=False)

    # --- 出どころと人の判断（監査のため、生成をやめた後も消さない） ---
    # 出どころ: rule_date あり = ルール、なし かつ original_date あり / is_manual なし = 取り込み、どちらもなし かつ is_manual = 人が追加
    rule_date: Mapped[date | None] = mapped_column(Date, comment="生成元のルール上の日付（発生枠の識別子）")
    original_date: Mapped[date | None] = mapped_column(
        Date, comment="人が変更・取消する前の日付（取り込み・ルール由来の日付のみ。人が追加した日付は NULL）"
    )
    is_manual: Mapped[bool] = mapped_column(
        default=False, server_default=text("false"), comment="人が編集・追加・取消した日付（再生成・再取り込みで上書きしない）"
    )
    is_cancelled: Mapped[bool] = mapped_column(
        default=False, server_default=text("false"), comment="人が取り消した日付（再生成・再取り込みで復活させないための記録）"
    )

    marker_def: Mapped["ReferenceSheetMarker"] = relationship(back_populates="events")

    __table_args__ = (
        # 再生成で日付を入れ替える際に一時的な重複が起きるため、コミット時に検査する
        UniqueConstraint(
            "marker_id", "event_date", name="uq_reference_sheet_event", deferrable=True, initially="DEFERRED"
        ),
        Index(
            "uq_reference_sheet_event_rule_date", "marker_id", "rule_date",
            unique=True, postgresql_where=text("rule_date IS NOT NULL"),
        ),
        CheckConstraint("NOT is_cancelled OR is_manual", name="ck_reference_sheet_event_cancel"),
    )


# ==========================================
# 2. PlanTemplate（生成テンプレート）
# ==========================================
class PlanTemplate(Base, SoftDeleteMixin):
    __tablename__ = "plan_template"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    biz_master_id: Mapped[int] = mapped_column(
        ForeignKey("biz_master.id", ondelete="CASCADE"), index=True, comment="所属業務"
    )
    name: Mapped[str] = mapped_column(String(100), comment="予定名")
    template_type: Mapped[TemplateType] = mapped_column(
        Enum(TemplateType, name="plan_template_type_enum"), nullable=False
    )

    # --- 時刻の解釈（DST対応のためローカル時刻 + IANAタイムゾーン） ---
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Tokyo")
    start_time: Mapped[time] = mapped_column(Time, nullable=False, comment="ローカル開始時刻（全タイプ共通）")
    duration_minutes: Mapped[int] = mapped_column(comment="所要時間（分）")

    # --- RRULE 用 ---
    anchor_date: Mapped[date | None] = mapped_column(Date, comment="DTSTART の日付部分")
    rrule: Mapped[str | None] = mapped_column(Text, comment="例: FREQ=WEEKLY;BYDAY=MO,WE")

    # --- REFERENCE_SHEET_BASED 用 ---
    reference_sheet_id: Mapped[int | None] = mapped_column(comment="参照する年間計画シート")
    reference_event_marker: Mapped[str | None] = mapped_column(String(50), comment="参照するイベント記号")
    offset_days: Mapped[int | None] = mapped_column(comment="基準日のずれ（日数。単位は offset_day_unit）")
    offset_day_unit: Mapped[DayUnit | None] = mapped_column(
        Enum(DayUnit, name="plan_offset_day_unit_enum"), comment="offset_days の単位（暦日 / 営業日）"
    )
    offset_minutes: Mapped[int | None] = mapped_column(comment="(基準日 + start_time) からの時刻のずれ")

    # --- 休日の扱い（全タイプ共通） ---
    business_calendar_id: Mapped[int | None] = mapped_column(
        ForeignKey("business_calendar.id", ondelete="RESTRICT", name="fk_plan_template_business_calendar_id"),
        index=True, comment="営業日カレンダー",
    )
    holiday_policy: Mapped[HolidayPolicy] = mapped_column(
        Enum(HolidayPolicy, name="plan_holiday_policy_enum"),
        default=HolidayPolicy.AS_IS, server_default=HolidayPolicy.AS_IS.name, nullable=False,
        comment="非営業日に当たった場合の扱い",
    )

    # --- 表示用メタデータ（例: {"bg_color": "#FF0000", "icon": "money-bag"}）。生成ルールには影響しない ---
    ui_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB, comment="カレンダー表示用スタイル（インスタンス側で上書き可）")

    # --- 改訂・マテリアライズ管理 ---
    revision: Mapped[int] = mapped_column(default=1, comment="生成に影響する変更ごとに+1。インスタンスに刻印される")
    materialized_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="ここまでインスタンス生成済み（ローリングホライズン）"
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    explicit_dates: Mapped[list["PlanExplicitDate"]] = relationship(
        back_populates="template", cascade="all, delete-orphan", passive_deletes=True,
        order_by="PlanExplicitDate.local_date",
    )

    # ※ SQLAlchemy の Enum はメンバー名を保存するため、CHECK では 'RRULE' 等の名前で比較する
    __table_args__ = (
        # (sheet_id, marker) がシート上に実在することをDBで保証（どちらかがNULLなら検査対象外）
        ForeignKeyConstraint(
            ["reference_sheet_id", "reference_event_marker"],
            ["reference_sheet_marker.sheet_id", "reference_sheet_marker.marker"],
            ondelete="RESTRICT", name="fk_plan_template_sheet_marker",
        ),
        CheckConstraint(
            "template_type <> 'RRULE' OR (rrule IS NOT NULL AND anchor_date IS NOT NULL)",
            name="ck_plan_template_rrule_fields",
        ),
        CheckConstraint(
            "template_type <> 'REFERENCE_SHEET_BASED' OR "
            "(reference_sheet_id IS NOT NULL AND reference_event_marker IS NOT NULL AND offset_minutes IS NOT NULL "
            "AND offset_days IS NOT NULL AND offset_day_unit IS NOT NULL)",
            name="ck_plan_template_sheet_fields",
        ),
        # 振替・スキップ・営業日オフセットにはカレンダーが必要
        CheckConstraint(
            "(holiday_policy = 'AS_IS' AND offset_day_unit IS DISTINCT FROM 'BUSINESS') "
            "OR business_calendar_id IS NOT NULL",
            name="ck_plan_template_calendar_required",
        ),
        CheckConstraint("duration_minutes > 0", name="ck_plan_template_duration_positive"),
        Index("ix_plan_template_sheet_marker", "reference_sheet_id", "reference_event_marker"),
    )

    @validates("rrule")
    def validate_rrule(self, key, value):
        return validate_rrule_text(value) if value is not None else value

    @validates("timezone")
    def validate_timezone(self, key, value):
        return validate_timezone_name(value)


# ==========================================
# 3. PlanExplicitDate（顧客指定日）
# ==========================================
class PlanExplicitDate(Base):
    __tablename__ = "plan_explicit_date"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    template_id: Mapped[int] = mapped_column(ForeignKey("plan_template.id", ondelete="CASCADE"), index=True)
    local_date: Mapped[date] = mapped_column(Date, nullable=False)

    template: Mapped["PlanTemplate"] = relationship(back_populates="explicit_dates")

    __table_args__ = (UniqueConstraint("template_id", "local_date", name="uq_plan_explicit_date"),)


# ==========================================
# 4. PlanInstance（不変インスタンス）
# ==========================================
class PlanInstance(Base):
    __tablename__ = "plan_instance"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    # RESTRICT: 履歴を守るため物理削除させない（テンプレートは論理削除のみ）
    template_id: Mapped[int] = mapped_column(ForeignKey("plan_template.id", ondelete="RESTRICT"), index=True)
    template_revision: Mapped[int] = mapped_column(nullable=False)

    scheduled_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scheduled_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # トレーサビリティ: どのシート日付から算出されたか（REFERENCE_SHEET_BASED のみ）
    # ※ シート日付は後から削除され得るため、FKではなくスナップショットとして保持する
    source_sheet_event_id: Mapped[int | None] = mapped_column(comment="算出元 reference_sheet_event.id")

    # 発生枠の識別子（iCalendar の RECURRENCE-ID に相当）。時刻が変わっても同じ枠を指す。
    # RRULE / EXPLICIT_DATES はルール上のローカル日付、REFERENCE_SHEET_BASED はシートの日付。
    occurrence_date: Mapped[date] = mapped_column(Date, nullable=False, comment="発生枠のローカル日付")

    # 生成元。GENERATED 以外（手動変更・単発追加）はルールからの再生成（reconcile）の対象外
    origin: Mapped[InstanceOrigin] = mapped_column(
        Enum(InstanceOrigin, name="plan_instance_origin_enum"), nullable=False, default=InstanceOrigin.GENERATED
    )

    # 手動変更（オーバーライド）で作られたインスタンスの場合、置き換え元のインスタンス
    # ※ ルールからの再生成（reconcile）の対象外となり、テンプレート変更後も維持される
    overrides_instance_id: Mapped[int | None] = mapped_column(
        ForeignKey("plan_instance.id", ondelete="RESTRICT", name="fk_plan_instance_overrides_instance_id"),
        index=True, comment="置き換え元インスタンス(手動変更時)",
    )

    # 表示用メタデータ（このインスタンスだけの上書き分。実効値はテンプレートとマージしてキャッシュに保持）
    ui_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB, comment="表示用スタイルの上書き")

    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # updated_at は意図的に持たない（不変）

    # ※ SQLAlchemy の Enum はメンバー名を保存するため、条件式では 'GENERATED' 等の名前で比較する
    __table_args__ = (
        # 同一リビジョンでの二重生成をDBで防ぐ（手動変更・単発追加は任意の時刻に置けるため対象外）
        Index(
            "uq_plan_instance_occurrence", "template_id", "template_revision", "scheduled_start",
            unique=True, postgresql_where=text("origin = 'GENERATED'"),
        ),
        CheckConstraint(
            "(origin = 'OVERRIDE') = (overrides_instance_id IS NOT NULL)", name="ck_plan_instance_override_link"
        ),
        CheckConstraint("scheduled_end > scheduled_start", name="ck_plan_instance_period"),
        Index("ix_plan_instance_template_start", "template_id", "scheduled_start"),
    )


class PlanInstanceVoid(Base):
    """インスタンスの無効化記録（追記のみ）"""
    __tablename__ = "plan_instance_void"

    instance_id: Mapped[int] = mapped_column(
        ForeignKey("plan_instance.id", ondelete="RESTRICT"), primary_key=True
    )
    reason: Mapped[VoidReason] = mapped_column(Enum(VoidReason, name="plan_void_reason_enum"), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, comment="手動変更・取消の理由")
    voided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PlanSlotRelease(Base):
    """
    手動の判断（変更・取消）の解除記録（追記のみ）。
    ルールから生成され MANUAL で無効化されたインスタンス（=手動判断の起点）ごとに最大1件。
    これがある発生枠は再びルールの管理下に戻り、再生成の対象になります。
    """
    __tablename__ = "plan_slot_release"

    root_instance_id: Mapped[int] = mapped_column(
        ForeignKey("plan_instance.id", ondelete="RESTRICT"), primary_key=True, comment="手動判断の起点インスタンス"
    )
    template_id: Mapped[int] = mapped_column(ForeignKey("plan_template.id", ondelete="RESTRICT"), index=True)
    note: Mapped[str | None] = mapped_column(Text, comment="解除の理由")
    released_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ==========================================
# 5. PlanOccurrenceCache（シャドウテーブル）
# 有効なインスタンスのみを非正規化して保持。plan_instance - plan_instance_void から再構築可能。
# ==========================================
class PlanOccurrenceCache(Base):
    __tablename__ = "plan_occurrence_cache"

    instance_id: Mapped[int] = mapped_column(
        ForeignKey("plan_instance.id", ondelete="CASCADE"), primary_key=True
    )
    template_id: Mapped[int] = mapped_column(index=True)
    biz_master_id: Mapped[int] = mapped_column()
    title: Mapped[str] = mapped_column(String(100))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_override: Mapped[bool] = mapped_column(default=False, server_default=text("false"), comment="手動変更された予定か")
    origin: Mapped[InstanceOrigin] = mapped_column(
        Enum(InstanceOrigin, name="plan_instance_origin_enum"), nullable=False, default=InstanceOrigin.GENERATED
    )
    ui_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB, comment="表示用スタイルの実効値（テンプレート+インスタンス）")
    period: Mapped[Range[datetime]] = mapped_column(
        TSTZRANGE, Computed("tstzrange(starts_at, ends_at, '[)')", persisted=True)
    )

    __table_args__ = (
        Index("ix_plan_cache_period", "period", postgresql_using="gist"),
        Index("ix_plan_cache_biz_start", "biz_master_id", "starts_at"),
    )


# ==========================================
# 不変性の強制（DBトリガー）
# マイグレーション側にも同じSQLがあります。ここは create_all（テスト等）用です。
# ==========================================
PLAN_REJECT_MUTATION_FUNCTION = """
CREATE OR REPLACE FUNCTION plan_reject_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION USING MESSAGE = TG_TABLE_NAME || ' is immutable';
END;
$$ LANGUAGE plpgsql
"""

for _model in (PlanInstance, PlanInstanceVoid, PlanSlotRelease):
    _name = _model.__tablename__
    event.listen(_model.__table__, "after_create", DDL(PLAN_REJECT_MUTATION_FUNCTION).execute_if(dialect="postgresql"))
    event.listen(
        _model.__table__,
        "after_create",
        DDL(
            f"CREATE TRIGGER trg_{_name}_immutable BEFORE UPDATE OR DELETE ON {_name} "
            "FOR EACH ROW EXECUTE FUNCTION plan_reject_mutation()"
        ).execute_if(dialect="postgresql"),
    )
