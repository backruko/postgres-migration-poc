from collections import defaultdict
from typing import Any
from dataclasses import dataclass
from datetime import date, datetime, timedelta, UTC
from zoneinfo import ZoneInfo

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from src.infrastructure.repositories import (
    biz_master_repo,
    business_calendar_repo,
    client_repo,
    plan_cache_repo,
    plan_instance_repo,
    plan_instance_void_repo,
    plan_slot_release_repo,
    plan_template_change_repo,
    plan_template_repo,
    reference_sheet_repo,
)
from src.domain_biz.models import BizMaster, ExecutionType
from src.domain_plan import engine, slot_events, template_events
from src.domain_plan.engine import DayUnit, HolidayPolicy
from src.domain_plan.models import (
    merge_ui_metadata,
    DeadlineMode,
    validate_timezone_name,
    BusinessCalendar,
    BusinessCalendarHoliday,
    InstanceOrigin,
    PlanExplicitDate,
    PlanInstance,
    PlanInstanceVoid,
    PlanOccurrenceCache,
    PlanTemplate,
    PlanTemplateChange,
    ReferenceSheet,
    ReferenceSheetEvent,
    ReferenceSheetMarker,
    TemplateChangeKind,
    TemplateChangeStatus,
    TemplateType,
    VoidReason,
)
from src.domain_plan.schemas import (
    MarkerInUseDetails,
    TemplateRef,
    DEADLINE_FIELDS,
    deadline_problem,
    MarkerRule,
    BusinessCalendarCreate,
    BusinessCalendarUpdate,
    CalendarChangeResult,
    ExplicitDatesTemplateCreate,
    HolidaysAdd,
    PlanInstanceCancel,
    PlanInstanceOverride,
    PlanInstanceResponse,
    PlanInstanceRevert,
    CancelledInstanceResponse,
    PlanOccurrenceResponse,
    PlanOneOffCreate,
    CalendarDay,
    CalendarTemplateInfo,
    ClientCalendarResponse,
    ClientOccurrenceResponse,
    PlanRevertResponse,
    PlanTemplateCreate,
    PlanTemplateUpdate,
    ReferenceSheetCreate,
    ReferenceSheetEventResponse,
    ReferenceSheetEventsCreate,
    ReferenceSheetEventUpdate,
    ReferenceSheetImport,
    ReferenceSheetImportResult,
    ReferenceSheetMarkerCreate,
    ReferenceSheetMarkerUpdate,
    ReferenceSheetUpdate,
    ReferenceSheetEventsBatch,
    ReferenceSheetResponse,
    ReferenceSheetTemplateCreate,
    TemplateExtensionResult,
    ui_metadata_to_dict,
)
from src.core.error_codes import ErrorCode
from src.core.exceptions import BadRequestError, DuplicatedRecordError, NotFoundError
from src.domain_client.services import require_active_client
from src.domain_staff.models import StaffUser
from src.core.clock import local_date_of, utc_now as _now

# ローリングホライズン: テンプレート作成時に「今から何日先まで」インスタンスを生成するか
MATERIALIZE_HORIZON = timedelta(days=90)
# 範囲検索の上限（巨大な範囲で大量生成されるのを防ぐ）
MAX_QUERY_SPAN = timedelta(days=366)
MAX_LOOKAHEAD = timedelta(days=730)
# 生成済みの範囲の上の境目をまたいで動いた枠を、どこまで追うか（時刻の変更で枠が動くのは 1 日より短い。_reconcile）
BOUNDARY_SHIFT = timedelta(days=1)
# 「無期限」の代わりに使う上限
FAR_FUTURE = datetime.max.replace(tzinfo=UTC)

RRULE_FIELDS = {"anchor_date", "rrule"}
# 変えても予定を作り直さない項目（締切の項目は DEADLINE_FIELDS）
NON_GENERATION_FIELDS = {"name", "start_prompt"}
SHEET_FIELDS = {"reference_sheet_id", "reference_event_marker", "offset_days", "offset_day_unit", "offset_minutes"}
# null を指定できる（＝解除できる）項目
NULLABLE_UPDATE_FIELDS = {"business_calendar_id", "deadline_offset_minutes", "deadline_time", "deadline_day_offset"}
# 決め方ごとに使う締切の項目（決め方を変えたら、使わない項目は空にする）
DEADLINE_MODE_FIELDS = {
    DeadlineMode.END: set(),
    DeadlineMode.OFFSET: {"deadline_offset_minutes"},
    DeadlineMode.FIXED_TIME: {"deadline_time", "deadline_day_offset"},
}


# シートの日付・休日は顧客の現地の日付。どちらにもタイムゾーンは無いため、日本の日付で「今日」を判定する


def _local_today() -> date:
    """
    シートの日付・休日の「今日」。これより前の日付は実際に行った業務の記録（実績）として、
    人の操作でも再生成でも変えない（過去の休日を変えると、過去の月の表示が実際と食い違うため）
    """
    return local_date_of(_now())


def _check_not_past(dates: list[date], action: str) -> None:
    today = _local_today()
    past = sorted(d for d in dates if d < today)
    if past:
        raise BadRequestError(
            f"Cannot {action} dates before today ({today}): {[d.isoformat() for d in past]}. Past dates are the operating record."
        )


# ==========================================
# 生成エンジン（内部処理）
# ==========================================
def _load_calendar(db: Session, template: PlanTemplate) -> engine.BusinessCalendar | None:
    """テンプレートの営業日カレンダー（週末 + 休日）をエンジン用に読み込みます。"""
    return _load_engine_calendar(db, template.business_calendar_id)


def _load_engine_calendar(db: Session, calendar_id: int | None) -> engine.BusinessCalendar | None:
    if calendar_id is None:
        return None
    calendar = business_calendar_repo.get(db, id=calendar_id)
    assert calendar is not None  # FK により存在は保証される
    return engine.BusinessCalendar(
        weekend_days=frozenset(calendar.weekend_days),
        holidays=frozenset(business_calendar_repo.get_holiday_dates(db, calendar_id=calendar.id)),
    )


def _expand(db: Session, template: PlanTemplate, lower: datetime, upper: datetime) -> list[engine.Occurrence]:
    """テンプレートの種類に応じて入力を集め、エンジンで [lower, upper) の発生日時を算出します。"""
    try:
        return _expand_with_engine(db, template, lower, upper)
    except engine.CalendarError as e:
        raise BadRequestError(f"PlanTemplate(id={template.id}): {e}") from e


def _expand_with_engine(db: Session, template: PlanTemplate, lower: datetime, upper: datetime) -> list[engine.Occurrence]:
    calendar = _load_calendar(db, template)
    policy = template.holiday_policy

    if template.template_type == TemplateType.RRULE:
        # CHECK制約により RRULE の場合は必ず値がある
        assert template.rrule is not None and template.anchor_date is not None
        return engine.expand_rrule(
            rrule=template.rrule,
            anchor_date=template.anchor_date,
            start_time=template.start_time,
            tz_name=template.timezone,
            duration_minutes=template.duration_minutes,
            window_start=lower,
            window_end=upper,
            calendar=calendar,
            holiday_policy=policy,
        )

    if template.template_type == TemplateType.EXPLICIT_DATES:
        return engine.expand_explicit_dates(
            dates=[d.local_date for d in template.explicit_dates],
            start_time=template.start_time,
            tz_name=template.timezone,
            duration_minutes=template.duration_minutes,
            window_start=lower,
            window_end=upper,
            calendar=calendar,
            holiday_policy=policy,
        )

    # REFERENCE_SHEET_BASED: (シート, マーカー) の具体的な日付を取得して算出
    assert template.reference_sheet_id is not None and template.reference_event_marker is not None
    assert template.offset_minutes is not None and template.offset_days is not None
    assert template.offset_day_unit is not None
    events = reference_sheet_repo.get_event_dates(
        db, sheet_id=template.reference_sheet_id, marker=template.reference_event_marker
    )
    return engine.expand_sheet_events(
        events=events,
        start_time=template.start_time,
        offset_minutes=template.offset_minutes,
        offset_days=template.offset_days,
        offset_day_unit=template.offset_day_unit,
        tz_name=template.timezone,
        duration_minutes=template.duration_minutes,
        window_start=lower,
        window_end=upper,
        calendar=calendar,
        holiday_policy=policy,
    )


def _slot(occurrence_date: date, source_sheet_event_id: int | None) -> tuple[str, date | int]:
    """
    発生枠の識別キー。シート由来はシート日付の行（日付が動いても同じ枠）、それ以外はルール上のローカル日付。
    """
    if source_sheet_event_id is not None:
        return ("sheet_event", source_sheet_event_id)
    return ("date", occurrence_date)


def _manual_slots(db: Session, template: PlanTemplate) -> set[tuple[str, date | int]]:
    """
    手動の判断が現在も有効な発生枠を返します。
    - 取消（MANUALで無効化され、置き換えなし）→ 有効
    - オーバーライド → 置き換えの連鎖の末端が「有効」または「手動取消」なら有効
      （末端がテンプレート削除などで無効化された場合は手動の判断も失効し、ルールから再生成される）
    - 解除（plan_slot_release）された起点 → 無効（ルールの管理下に戻っている）
    """
    history = plan_instance_repo.get_manual_history(db, template_id=template.id)
    released = plan_slot_release_repo.get_released_root_ids(db, template_id=template.id)
    void_of = {instance.id: void for instance, void in history}
    child_of = {instance.overrides_instance_id: instance for instance, _ in history if instance.overrides_instance_id}

    slots: set[tuple[str, date | int]] = set()
    for instance, void in history:
        # 起点 = ルールから生成され、手動で無効化され、まだ解除されていないインスタンス
        if (
            instance.origin != InstanceOrigin.GENERATED
            or void is None
            or void.reason != VoidReason.MANUAL
            or instance.id in released
        ):
            continue
        terminal = instance
        while terminal.id in child_of:
            terminal = child_of[terminal.id]
        terminal_void = void_of.get(terminal.id)
        if terminal_void is None or terminal_void.reason == VoidReason.MANUAL:
            slots.add(_slot(instance.occurrence_date, instance.source_sheet_event_id))
    return slots


def _started_slots(
    db: Session, template: PlanTemplate, lower: datetime, occurrences: list[engine.Occurrence]
) -> set[tuple[str, date | int]]:
    """occurrences の発生枠のうち、lower より前に始まった有効な予定がもうある枠"""
    started = plan_instance_repo.get_live_started(
        db,
        template_id=template.id,
        before=lower,
        occurrence_dates=sorted({o.occurrence_date for o in occurrences if o.source_sheet_event_id is None}),
        sheet_event_ids=sorted({o.source_sheet_event_id for o in occurrences if o.source_sheet_event_id is not None}),
    )
    return {_slot(i.occurrence_date, i.source_sheet_event_id) for i in started}


def _reconcile(db: Session, template: PlanTemplate, lower: datetime, upper: datetime, reason: VoidReason) -> None:
    """
    [lower, upper) の範囲で「あるべきインスタンス」と「現在有効なインスタンス」を突き合わせます。
    - 変わっていないもの: そのまま残す（不変）
    - 不要になったもの: 無効化(void)し、キャッシュから除外
    - 新しく必要なもの: 現在のリビジョンで生成し、キャッシュに追加
    次の発生枠は触らない: 人が変更・取消した枠、もう始まった枠（過去に有効な予定がある）、作業が始まった・終わった枠（確認役が答える）
    """
    if upper <= lower:
        return

    # 手動で変更・取消された発生枠はルールから再生成しない（手動オーバーライド自体も照合対象外）
    manual_slots = _manual_slots(db, template)
    # 今ある予定は、upper の先 1 日まで見る（下の「上の境目をまたいで動いた枠」で、upper より後に作った予定があるため）
    edge = upper + BOUNDARY_SHIFT
    existing = {
        (i.scheduled_start, i.scheduled_end, i.source_sheet_event_id): i
        for i in plan_instance_repo.get_live_in_window(
            db, template_id=template.id, lower=lower, upper=edge, generated_only=True
        )
    }
    # 上の境目（upper）をまたいで動いた枠: 今の予定がある枠は、新しい開始が upper 以降になっても、その枠の新しい予定を作る
    # （無効にして作らないと、範囲の最後の日の枠だけ「無くなった」になり、オーダーが取り消される。取り消されたオーダーは
    # 直前の上流（L.2）として下流を止め続ける。例: 境目が 9:00（ふつうのホライズンは UTC の 0 時 = 日本時間 9:00）のとき
    # 8:30 → 9:30）。時刻の変更で枠が動くのは 1 日より短いので、upper から BOUNDARY_SHIFT だけ先を見る。
    # materialized_until は変えない（後で範囲を延ばすと、その予定は範囲の中にあり、同じキーなのでそのまま残る）
    slots_with_instance = {_slot(i.occurrence_date, i.source_sheet_event_id) for i in existing.values()}
    expanded = [
        o for o in _expand(db, template, lower, upper) if _slot(o.occurrence_date, o.source_sheet_event_id) not in manual_slots
    ] + [
        o for o in _expand(db, template, upper, edge)
        if _slot(o.occurrence_date, o.source_sheet_event_id) in slots_with_instance - manual_slots
    ]
    # もう始まった発生枠（lower より前に始まった有効な予定がある枠）も作らない。
    # 例: 今日 9:00 の予定が過ぎた後に、テンプレートの時刻を 14:00 に変える。過去の予定は変えないので 9:00 の予定は残り、
    # ここで除かないと今日に 14:00 の予定がもう 1 件できる（オーダーは 9:00 の側に付いたままで、14:00 の側はリリースも取消もできない）
    started_slots = _started_slots(db, template, lower, expanded)
    # 作業がもう始まった・終わった発生枠（確認役 = coordination が答える。オーダーが完了・作業中）は、今の予定のまま残す:
    # 無効にしないし、新しい日時で作り直しもしない。動かすと、完了したオーダーが古い予定に残り、新しい予定にはオーダーが無くなる
    protected = slot_events.protected_slots(db, template.id, lower, edge, reason)
    kept_slots = {
        _slot(i.occurrence_date, i.source_sheet_event_id)
        for i in existing.values()
        if _slot(i.occurrence_date, i.source_sheet_event_id) in protected
    }
    skip = started_slots | kept_slots
    desired = {o.key: o for o in expanded if _slot(o.occurrence_date, o.source_sheet_event_id) not in skip}

    to_void = [
        i.id
        for key, i in existing.items()
        if key not in desired and _slot(i.occurrence_date, i.source_sheet_event_id) not in kept_slots
    ]
    to_add = [o for key, o in desired.items() if key not in existing]

    if to_void:
        plan_instance_void_repo.void_many(db, instance_ids=to_void, reason=reason)
        plan_cache_repo.purge(db, instance_ids=to_void)
    if to_add:
        new_instances = plan_instance_repo.add_many(db, template=template, occurrences=to_add)
        plan_cache_repo.add_for_instances(db, template=template, instances=new_instances)


def _ensure_materialized(db: Session, template: PlanTemplate, until: datetime) -> None:
    """materialized_until から until までの未生成区間を生成します（生成済みなら何もしません）。"""
    lower = template.materialized_until or _now()
    if until <= lower:
        return
    _reconcile(db, template, lower, until, VoidReason.TEMPLATE_REVISED)
    template.materialized_until = until


def _regenerate(db: Session, template: PlanTemplate, reason: VoidReason) -> None:
    """
    生成に影響する変更の後に呼びます。リビジョンを上げ、未来（今以降）の生成済み区間を再照合します。
    過去のインスタンスには一切触れません。
    """
    template.revision += 1
    if template.materialized_until is not None:
        _reconcile(db, template, _now(), template.materialized_until, reason)
    _ensure_next_occurrence(db, template)


def _ensure_next_occurrence(db: Session, template: PlanTemplate) -> bool:
    """
    生成済み区間を「今から MATERIALIZE_HORIZON 先」と「次の予定」の遅い方まで延ばします（上限 MAX_LOOKAHEAD）。
    年1回の業務のように次の予定がホライズンより先でも、次の予定が常に生成済み（ダッシュボード等から見える）になります。
    戻り値: 生成済み区間を延ばしたか
    """
    now = _now()
    cap = now + MAX_LOOKAHEAD
    before = template.materialized_until
    # 日単位（UTC の翌0時）に切り上げる。同じ日に何度実行しても区間が動かない（日次ジョブの「延ばした数」が意味を持つ）
    horizon = datetime.combine((now + MATERIALIZE_HORIZON).date() + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
    _ensure_materialized(db, template, horizon)
    while not plan_cache_repo.next_starts(db, template_ids=[template.id], after=now):
        lower = template.materialized_until or now
        if lower >= cap:
            break
        upcoming = _expand(db, template, lower, cap)
        if not upcoming:
            break
        # 次の発生日時を含むところまで生成する（手動で取り消された枠だった場合は、さらに次を探す）
        _ensure_materialized(db, template, min(min(o.start for o in upcoming) + timedelta(days=1), cap))
    return template.materialized_until != before


def _with_next_occurrence(db: Session, templates: list[PlanTemplate]) -> list[PlanTemplate]:
    """
    各テンプレートに next_occurrence_at（次の有効な予定の開始日時）と pending_change_id（承認待ちの下書き）を付けます
    （レスポンス用。DB には保存しない）。一覧でもそれぞれ 1 回の問い合わせ。
    """
    ids = [t.id for t in templates]
    next_starts = plan_cache_repo.next_starts(db, template_ids=ids, after=_now())
    drafts = plan_template_change_repo.get_draft_template_ids(db, template_ids=ids)
    for t in templates:
        t.next_occurrence_at = next_starts.get(t.id)  # type: ignore[attr-defined]
        t.pending_change_id = drafts.get(t.id)  # type: ignore[attr-defined]
    return templates


def extend_all_templates(db: Session) -> TemplateExtensionResult:
    """
    日次の生成ジョブ（scripts/extend_plans.py から1日1回実行）。全顧客の有効なテンプレートについて、
    生成済み区間を「今から MATERIALIZE_HORIZON 先」と「次の予定」の遅い方まで延ばします。何度実行しても同じ結果になります。
    テンプレートごとにコミットし、行ロックを短く保ちます。
    """
    template_ids = [t.id for t in plan_template_repo.get_all(db)]
    extended = 0
    without_upcoming = []
    for template_id in template_ids:
        template = plan_template_repo.get_locked(db, id=template_id)
        if template is None:
            continue  # 実行中に削除された
        if _ensure_next_occurrence(db, template):
            extended += 1
        if not plan_cache_repo.next_starts(db, template_ids=[template.id], after=_now()):
            without_upcoming.append(template.id)
        _commit(db, f"Occurrences of PlanTemplate(id={template_id}) could not be generated due to a conflicting record.")
    return TemplateExtensionResult(
        templates_checked=len(template_ids), templates_extended=extended, without_upcoming=without_upcoming
    )


def _commit(db: Session, message: str) -> None:
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise DuplicatedRecordError(message) from e


# ==========================================
# BusinessCalendar Service
# ==========================================
def create_calendar(db: Session, calendar_in: BusinessCalendarCreate) -> BusinessCalendar:
    if business_calendar_repo.get_by_code(db, code=calendar_in.code):
        raise DuplicatedRecordError(f"BusinessCalendar with code '{calendar_in.code}' already exists.")
    calendar = BusinessCalendar(**calendar_in.model_dump())
    db.add(calendar)
    _commit(db, f"BusinessCalendar with code '{calendar_in.code}' already exists.")
    db.refresh(calendar)
    return calendar


def get_calendar(db: Session, calendar_id: int) -> BusinessCalendar:
    calendar = business_calendar_repo.get(db, id=calendar_id)
    if not calendar:
        raise NotFoundError(f"BusinessCalendar(id={calendar_id}) not found.")
    return calendar


def get_calendars(db: Session) -> list[BusinessCalendar]:
    return business_calendar_repo.get_all(db)


def _propagate_calendar_change(db: Session, calendar_id: int) -> int:
    """
    カレンダーの変更を反映します（同一トランザクション内）。
    1. このカレンダーで生成しているシートのマーカー日付を再生成（手動の日付は維持）
    2. このカレンダーを使うテンプレート、および日付が変わったマーカーを参照するテンプレートの未来分を再照合
    """
    targets = {t.id: t for t in plan_template_repo.get_by_calendar(db, calendar_id=calendar_id)}
    for marker in reference_sheet_repo.get_generated_markers_by_calendar(db, calendar_id=calendar_id):
        if _generate_marker_events(db, marker):
            for t in plan_template_repo.get_by_sheet_marker(db, sheet_id=marker.sheet_id, marker=marker.marker):
                targets[t.id] = t
    for template in targets.values():
        _regenerate(db, template, VoidReason.CALENDAR_CHANGED)
    return len(targets)


def update_calendar(db: Session, calendar_id: int, calendar_in: BusinessCalendarUpdate) -> BusinessCalendar:
    calendar = get_calendar(db, calendar_id)
    if calendar_in.name is not None:
        calendar.name = calendar_in.name
    if calendar_in.weekend_days is not None and sorted(calendar.weekend_days) != calendar_in.weekend_days:
        calendar.weekend_days = calendar_in.weekend_days
        db.flush()
        _propagate_calendar_change(db, calendar.id)
    db.commit()
    db.refresh(calendar)
    return calendar


def get_holidays(db: Session, calendar_id: int, year: int | None = None) -> list[BusinessCalendarHoliday]:
    get_calendar(db, calendar_id)
    if year is None:
        return business_calendar_repo.get_holidays(db, calendar_id=calendar_id)
    return business_calendar_repo.get_holidays(
        db, calendar_id=calendar_id, start=date(year, 1, 1), end=date(year, 12, 31)
    )


def add_holidays(db: Session, calendar_id: int, holidays_in: HolidaysAdd) -> CalendarChangeResult:
    """
    休日を一括登録します（登録済みの日付は名前のみ更新）。日付が増えた場合は使用中のテンプレートへ反映します。
    今日より前の日付は追加も名前の変更もできません（実績のため）。
    """
    _check_not_past([h.holiday_date for h in holidays_in.holidays], "add or rename holidays on")
    calendar = get_calendar(db, calendar_id)
    existing = {h.holiday_date: h for h in calendar.holidays}

    result = CalendarChangeResult()
    for h in holidays_in.holidays:
        current = existing.get(h.holiday_date)
        if current is None:
            calendar.holidays.append(BusinessCalendarHoliday(holiday_date=h.holiday_date, name=h.name))
            result.holidays_added += 1
        elif current.name != h.name:
            current.name = h.name
            result.holidays_renamed += 1

    db.flush()
    if result.holidays_added:
        result.templates_regenerated = _propagate_calendar_change(db, calendar.id)
    _commit(db, "Some holidays already exist in this calendar.")
    return result


def delete_holiday(db: Session, calendar_id: int, holiday_date: date) -> CalendarChangeResult:
    """休日を削除します（その日は営業日に戻ります）。今日より前の休日は削除できません（実績のため）。"""
    calendar = get_calendar(db, calendar_id)
    _check_not_past([holiday_date], "delete holidays on")
    target = next((h for h in calendar.holidays if h.holiday_date == holiday_date), None)
    if target is None:
        raise NotFoundError(f"Holiday {holiday_date} not found in BusinessCalendar(id={calendar_id}).")

    calendar.holidays.remove(target)
    db.flush()
    result = CalendarChangeResult(holidays_deleted=1, templates_regenerated=_propagate_calendar_change(db, calendar.id))
    db.commit()
    return result


# ==========================================
# ReferenceSheet Service
# ==========================================
def create_sheet(db: Session, sheet_in: ReferenceSheetCreate) -> ReferenceSheet:
    require_active_client(db, sheet_in.client_code)  # 無い顧客は 404、無効な顧客は 400（M1.1）

    sheet = ReferenceSheet(**sheet_in.model_dump())
    db.add(sheet)
    _commit(db, f"Sheet '{sheet_in.name}' already exists for client '{sheet_in.client_code}'.")
    db.refresh(sheet)
    return sheet


def _get_sheet(db: Session, sheet_id: int) -> ReferenceSheet:
    sheet = reference_sheet_repo.get(db, id=sheet_id)
    if not sheet:
        raise NotFoundError(f"ReferenceSheet(id={sheet_id}) not found.")
    return sheet


def get_sheet(db: Session, sheet_id: int) -> ReferenceSheet:
    return _annotate_sheet(db, _get_sheet(db, sheet_id))


def get_sheets(db: Session, client_code: str) -> list[ReferenceSheet]:
    return [_annotate_sheet(db, s) for s in reference_sheet_repo.get_by_client(db, client_code=client_code)]


def add_marker(db: Session, sheet_id: int, marker_in: ReferenceSheetMarkerCreate) -> ReferenceSheet:
    sheet = _get_sheet(db, sheet_id)
    if reference_sheet_repo.get_marker(db, sheet_id=sheet_id, marker=marker_in.marker):
        raise DuplicatedRecordError(f"Marker '{marker_in.marker}' already exists on this sheet.")

    marker = ReferenceSheetMarker(marker=marker_in.marker, description=marker_in.description)
    sheet.markers.append(marker)
    if marker_in.rule is not None:
        _apply_marker_rule(db, marker, marker_in.rule)
        db.flush()
        _generate_marker_events(db, marker)
    _commit(db, f"Marker '{marker_in.marker}' already exists on this sheet.")
    db.refresh(sheet)
    return _annotate_sheet(db, sheet)


# ==========================================
# 生成マーカー（ルールから基準日を生成）
# ==========================================
def _apply_marker_rule(db: Session, marker: ReferenceSheetMarker, rule: MarkerRule | None) -> None:
    if rule is None:
        marker.rrule = marker.anchor_date = marker.business_calendar_id = None
        marker.holiday_policy = HolidayPolicy.AS_IS
        return
    _get_calendar_if_set(db, rule.business_calendar_id)
    marker.rrule = rule.rrule
    marker.anchor_date = rule.anchor_date
    marker.business_calendar_id = rule.business_calendar_id
    marker.holiday_policy = rule.holiday_policy


def _generate_marker_events(db: Session, marker: ReferenceSheetMarker) -> bool:
    """
    生成マーカーの日付をルールに合わせます。変更があれば True を返します（flush 済み）。
    人の判断が常に優先されます:
    - 人が編集・取消した日付（is_manual）は変更しない。その発生枠をルールで作り直すこともしない
    - 人が置いた日付と同じ日には、ルールの日付を置かない
    今日より前の日付は実績のため変えません（ルール・カレンダーを変えても、過去の日付の追加・移動・削除はしない）。
    ただし新しいマーカーの最初の生成では、シートの期間全体（過去も含む）を作ります。
    ルールの日付は rule_date（発生枠）で照合するため、日付が動いても同じ行（id）が更新され、
    参照しているテンプレート側の手動変更もそのまま維持されます。
    """
    if not marker.is_generated:
        return False
    desired = _expand_marker_rule(db, marker)
    first_run = not marker.events
    today = _local_today()
    by_rule_date = {e.rule_date: e for e in marker.events if e.rule_date is not None}

    targets = _marker_rule_targets(marker, desired, by_rule_date, first_run=first_run, today=today)
    removed = _remove_unwanted_rule_events(marker, targets, first_run=first_run, today=today)
    placed = _place_rule_events(marker, targets, by_rule_date)
    db.flush()
    return removed or placed


def _expand_marker_rule(db: Session, marker: ReferenceSheetMarker) -> list[engine.RuleDate]:
    """生成マーカーのルールが、シートの期間に作る日付（発生枠と、休日の扱いの後の実際の日付）。カレンダーの誤りは 400"""
    assert marker.rrule is not None and marker.anchor_date is not None
    try:
        return engine.expand_rule_dates(
            rrule=marker.rrule,
            anchor_date=marker.anchor_date,
            period_start=marker.sheet.period_start,
            period_end=marker.sheet.period_end,
            calendar=_load_engine_calendar(db, marker.business_calendar_id),
            holiday_policy=marker.holiday_policy,
        )
    except engine.CalendarError as e:
        raise BadRequestError(f"Marker '{marker.marker}': {e}") from e


def _marker_rule_targets(
    marker: ReferenceSheetMarker, desired: list[engine.RuleDate], by_rule_date: dict[date, ReferenceSheetEvent],
    *, first_run: bool, today: date,
) -> dict[date, date]:
    """
    ルールが置いてよい日付（発生枠 → 実際の日付）。人が変更・取消した枠、人の日付と同じ日、
    最初の生成でなければ過去（実績）にかかる枠は外す
    """
    human_dates = {e.event_date for e in marker.events if e.is_manual}
    targets: dict[date, date] = {}
    for rd in desired:
        current = by_rule_date.get(rd.rule_date)
        if current is not None and current.is_manual:
            continue
        if rd.actual_date in human_dates:
            continue
        if not first_run and (rd.actual_date < today or (current is not None and current.event_date < today)):
            continue  # 過去（実績）は作らない・動かさない
        targets[rd.rule_date] = rd.actual_date
    return targets


def _remove_unwanted_rule_events(
    marker: ReferenceSheetMarker, targets: dict[date, date], *, first_run: bool, today: date,
) -> bool:
    """ルールがもう置かない、ルール由来の日付を消す（人の日付と、最初の生成でなければ過去の日付は残す）。消したら True"""
    changed = False
    for event in list(marker.events):
        if not first_run and event.event_date < today:
            continue  # 過去（実績）は消さない
        if not event.is_manual and event.rule_date is not None and event.rule_date not in targets:
            marker.events.remove(event)  # delete-orphan により削除
            changed = True
    return changed


def _place_rule_events(
    marker: ReferenceSheetMarker, targets: dict[date, date], by_rule_date: dict[date, ReferenceSheetEvent],
) -> bool:
    """ルールの日付を置く: 新しい発生枠は行を足し、ある枠は同じ行の日付を動かす（id が変わらない）。変えたら True"""
    changed = False
    for rule_date, actual in targets.items():
        current = by_rule_date.get(rule_date)
        if current is None:
            marker.events.append(ReferenceSheetEvent(event_date=actual, rule_date=rule_date))
            changed = True
        elif current.event_date != actual:
            current.event_date = actual
            changed = True
    return changed


def _annotate_revertable(db: Session, marker: ReferenceSheetMarker) -> ReferenceSheetMarker:
    """
    各日付に revertable（「ルールに戻す」が通るか）を付けます（レスポンス用。DB には保存しない）。
    revert_sheet_event と同じ条件: 生成マーカーの、人が変更・取消したルール由来の日付で、
    今のルールがその発生枠を作り、その日が他の人の日付と重ならず、戻した後の日付も今日以降であること。
    """
    for e in marker.events:
        e.revertable = False  # type: ignore[attr-defined]
    if not marker.is_generated or not any(e.is_manual and e.rule_date is not None for e in marker.events):
        return marker
    assert marker.rrule is not None and marker.anchor_date is not None
    try:
        desired = {
            rd.rule_date: rd.actual_date
            for rd in engine.expand_rule_dates(
                rrule=marker.rrule,
                anchor_date=marker.anchor_date,
                period_start=marker.sheet.period_start,
                period_end=marker.sheet.period_end,
                calendar=_load_engine_calendar(db, marker.business_calendar_id),
                holiday_policy=marker.holiday_policy,
            )
        }
    except engine.CalendarError:
        return marker
    today = _local_today()
    for e in marker.events:
        if not e.is_manual or e.rule_date is None or e.event_date < today:
            continue
        actual = desired.get(e.rule_date)
        others = {x.event_date for x in marker.events if x.is_manual and x is not e}
        e.revertable = actual is not None and actual >= today and actual not in others  # type: ignore[attr-defined]
    return marker


def _annotate_sheet(db: Session, sheet: ReferenceSheet) -> ReferenceSheet:
    for m in sheet.markers:
        _annotate_revertable(db, m)
    return sheet


def _regenerate_sheet_markers(db: Session, sheet: ReferenceSheet) -> list[str]:
    """シートの全生成マーカーを再生成し、日付が変わったマーカー名を返します（シート期間の変更時など）"""
    return [m.marker for m in sheet.markers if _generate_marker_events(db, m)]


def update_marker(
    db: Session, sheet_id: int, marker: str, marker_in: ReferenceSheetMarkerUpdate, actor_id: int | None = None,
) -> ReferenceSheetMarker:
    _get_sheet(db, sheet_id)
    marker_obj = reference_sheet_repo.get_marker(db, sheet_id=sheet_id, marker=marker)
    if not marker_obj:
        raise NotFoundError(f"Marker '{marker}' not found on ReferenceSheet(id={sheet_id}).")

    if marker_in.name is not None and marker_in.name != marker:
        _rename_marker(db, marker_obj, marker_in.name, actor_id)
        marker = marker_in.name  # 以降（日付の反映など）は新しい名前で

    if "description" in marker_in.model_fields_set:
        marker_obj.description = marker_in.description

    changed = False
    if "rule" in marker_in.model_fields_set:
        if marker_in.rule is None:
            # 生成をやめる: 以後ルールで作り直さない。
            # 出どころ（rule_date）・人の判断（is_manual）・取消の記録は監査のため消さない
            _apply_marker_rule(db, marker_obj, None)
            if marker_in.cancel_future_dates:
                # 業務が終わった場合: 今日以降の日付をすべて取り消す（過去は実績のため残す）。1トランザクションで反映する
                today = _local_today()
                for event in [e for e in marker_obj.events if e.event_date >= today and not e.is_cancelled]:
                    _cancel_sheet_event(db, marker_obj, event)
                    changed = True
        else:
            _apply_marker_rule(db, marker_obj, marker_in.rule)
            db.flush()
            changed = _generate_marker_events(db, marker_obj)
    db.flush()
    if changed:
        _propagate_sheet_change(db, sheet_id, marker)
    _commit(db, "Marker dates conflict with each other.")
    db.refresh(marker_obj)
    return _annotate_revertable(db, marker_obj)


# ==========================================
# シート・マーカーの編集（E.1、2026-11-11）: 名前・期間の変更、マーカーの名前の変更・削除
# ==========================================
def _template_label(t: PlanTemplate) -> str:
    return f"{t.name} (#{t.id})"


def _drafts_naming_marker_reason(db: Session, sheet_id: int, marker: str) -> str | None:
    drafts = plan_template_change_repo.get_drafts_naming_marker(db, sheet_id=sheet_id, marker=marker)
    if not drafts:
        return None
    return _drafts_reason(drafts)


def _drafts_reason(drafts: list) -> str:
    return f"{len(drafts)} template draft(s) waiting for approval name this marker (change {', '.join(str(d.id) for d in drafts)})"


def _rename_marker(db: Session, marker_obj: ReferenceSheetMarker, new_name: str, actor_id: int | None) -> None:
    """
    マーカーの名前を変えます（コミットしない）。参照しているテンプレート（削除済みも）は DB の FK（ON UPDATE CASCADE）で
    同じ名前に変わる。日付は変わらないので、予定・オーダーは動かない。
    承認待ちの下書きがあるテンプレート・下書きの本文がこのマーカーを指す場合は断る（409 TEMPLATE_DRAFT_PENDING）。
    業務管理者の操作（actor_id あり）なら、参照している有効なテンプレートごとに履歴を 1 行残す（直接の変更）。
    """
    sheet_id, old_name = marker_obj.sheet_id, marker_obj.marker
    if reference_sheet_repo.get_marker(db, sheet_id=sheet_id, marker=new_name):
        raise DuplicatedRecordError(f"Marker '{new_name}' already exists on this sheet.")
    templates = plan_template_repo.get_by_sheet_marker(db, sheet_id=sheet_id, marker=old_name)  # 行ロック
    for template in templates:
        refuse_if_draft_pending(db, template.id)
    reason = _drafts_naming_marker_reason(db, sheet_id, old_name)
    if reason:
        raise DuplicatedRecordError(
            f"Marker '{old_name}' cannot be renamed: {reason}; approve, reject or withdraw them first.",
            error_code=ErrorCode.TEMPLATE_DRAFT_PENDING,
        )
    before = {t.id: template_settings(t) for t in templates}
    marker_obj.marker = new_name
    db.flush()  # FK の ON UPDATE CASCADE でテンプレートの名前も変わる
    for template in templates:
        db.refresh(template, ["reference_event_marker"])
        if actor_id is not None:
            _record_direct_change(
                db, template, TemplateChangeKind.UPDATE, actor_id, base_revision=template.revision,
                before=before[template.id], changes={"reference_event_marker": new_name},
            )


def update_sheet(db: Session, sheet_id: int, sheet_in: ReferenceSheetUpdate) -> ReferenceSheet:
    """
    シートの名前・期間を変えます（業務管理者）。期間を延ばすと、ルールで日付を作るマーカーが新しい期間まで作り、
    日付が変わったマーカーを参照するテンプレートの予定も直す。縮めるのは、新しい期間の外に残る日付が無いときだけ
    （取り込み・人の日付と、今日より前のルールの日付は残るため。取り消した日付も記録として数える）。
    """
    sheet = db.scalars(select(ReferenceSheet).where(ReferenceSheet.id == sheet_id).with_for_update()).first()
    if sheet is None:
        raise NotFoundError(f"ReferenceSheet(id={sheet_id}) not found.")
    if sheet_in.name is not None:
        sheet.name = sheet_in.name
    start = sheet_in.period_start or sheet.period_start
    end = sheet_in.period_end or sheet.period_end
    if end <= start:
        raise BadRequestError("period_end must be after period_start.")

    changed_markers: list[str] = []
    if (start, end) != (sheet.period_start, sheet.period_end):
        today = _local_today()
        outside = sorted(
            e.event_date
            for m in sheet.markers
            for e in m.events
            if not (start <= e.event_date <= end) and (not m.is_generated or e.is_manual or e.event_date < today)
        )
        if outside:
            raise BadRequestError(
                f"{len(outside)} date(s) fall outside the new period (e.g. {[d.isoformat() for d in outside[:10]]}); "
                "delete or move them first, or keep a longer period."
            )
        sheet.period_start, sheet.period_end = start, end
        db.flush()
        changed_markers = _regenerate_sheet_markers(db, sheet)
    for name in changed_markers:
        _propagate_sheet_change(db, sheet.id, name)
    _commit(db, f"Sheet '{sheet.name}' already exists for client '{sheet.client_code}'.")
    db.refresh(sheet)
    return _annotate_sheet(db, sheet)


def delete_marker(db: Session, sheet_id: int, marker: str) -> ReferenceSheet:
    """
    マーカーとその日付を削除します（業務管理者）。テンプレート（有効・削除済み）か承認待ちの下書きが参照していれば断る
    （409 DELETE_FAILED_RECORD_IN_USE。理由にテンプレートを挙げる）。削除済みのテンプレートも、復元できるため数える。
    オーダー・予定の履歴は日付を写しで持つので、消しても残る。
    """
    sheet = _get_sheet(db, sheet_id)
    marker_obj = reference_sheet_repo.get_marker(db, sheet_id=sheet_id, marker=marker)
    if not marker_obj:
        raise NotFoundError(f"Marker '{marker}' not found on ReferenceSheet(id={sheet_id}).")
    reasons: list[str] = []
    live = plan_template_repo.get_by_sheet_marker(db, sheet_id=sheet_id, marker=marker)
    if live:
        reasons.append(f"template(s) {', '.join(_template_label(t) for t in live)} use it")
    deleted = plan_template_repo.get_deleted_by_sheet_marker(db, sheet_id=sheet_id, marker=marker)
    if deleted:
        reasons.append(f"deleted template(s) {', '.join(_template_label(t) for t in deleted)} still refer to it (they can be restored)")
    drafts = plan_template_change_repo.get_drafts_naming_marker(db, sheet_id=sheet_id, marker=marker)
    if drafts:
        reasons.append(_drafts_reason(drafts))
    if reasons:
        # 画面は details で日本語の文を作る（message は英語のまま）
        details = MarkerInUseDetails(
            marker=marker,
            templates=[TemplateRef(id=t.id, name=t.name) for t in live],
            deleted_templates=[TemplateRef(id=t.id, name=t.name) for t in deleted],
            draft_change_ids=[d.id for d in drafts],
        )
        raise DuplicatedRecordError(
            f"Marker '{marker}' is in use: " + "; ".join(reasons) + ".", error_code=ErrorCode.DELETE_FAILED_RECORD_IN_USE,
            details=details.model_dump(),
        )
    sheet.markers.remove(marker_obj)  # 日付も delete-orphan で消える
    _commit(db, f"Marker '{marker}' could not be deleted.")
    db.refresh(sheet)
    return _annotate_sheet(db, sheet)


def import_sheet(db: Session, import_in: ReferenceSheetImport, replace_events: bool = False) -> ReferenceSheetImportResult:
    """
    年間計画シートを一括で取り込みます（1トランザクション。失敗時は何も反映されません）。
    - (client_code, sheet_name) のシートが無ければ新規作成、あれば更新（同じファイルの再取り込みは何も変えない）
    - マーカー: 無いものは追加、説明が変わったものは更新（ファイルに無いマーカーは、テンプレートが参照し得るため削除しない）
    - 日付: 無いものは追加。replace_events=True の場合はファイルに無い日付を削除し、シートをファイルと完全一致させる
    - 日付が変わったマーカーを参照しているテンプレートは、未来分を自動で再照合する
    """
    require_active_client(db, import_in.client_code)  # 無い顧客は 404、無効な顧客は 400（M1.1）
    sheet, created = _sheet_to_import_into(db, import_in, replace_events)
    markers, markers_created, markers_updated = _import_markers(sheet, import_in)
    wanted = _wanted_dates(import_in, markers)
    events_created, events_deleted, changed_markers = _import_dates(markers, wanted, replace_events)

    try:
        db.flush()
    except IntegrityError as e:
        db.rollback()
        raise DuplicatedRecordError("The sheet could not be imported due to a conflicting record.") from e

    # 新規シートは参照されていないため、作り直すものは無い
    templates_regenerated = 0 if created else _follow_sheet_changes(db, sheet, changed_markers)

    _commit(db, "The sheet could not be imported due to a conflicting record.")
    db.refresh(sheet)
    return ReferenceSheetImportResult(
        created=created,
        markers_created=markers_created,
        markers_updated=markers_updated,
        events_created=events_created,
        events_deleted=events_deleted,
        templates_regenerated=templates_regenerated,
        sheet=ReferenceSheetResponse.model_validate(_annotate_sheet(db, sheet)),
    )


def _sheet_to_import_into(db: Session, import_in: ReferenceSheetImport, replace_events: bool) -> tuple[ReferenceSheet, bool]:
    """(client_code, sheet_name) のシートと、新しく作ったか。あるシートは期間をファイルの期間にする"""
    sheet = reference_sheet_repo.get_by_client_and_name(db, client_code=import_in.client_code, name=import_in.sheet_name)
    if sheet is None:
        sheet = ReferenceSheet(
            client_code=import_in.client_code,
            name=import_in.sheet_name,
            period_start=import_in.period_start,
            period_end=import_in.period_end,
        )
        db.add(sheet)
        return sheet, True
    if not replace_events:
        _check_kept_dates_in_period(sheet, import_in.period_start, import_in.period_end)
    sheet.period_start = import_in.period_start
    sheet.period_end = import_in.period_end
    return sheet, False


def _check_kept_dates_in_period(sheet: ReferenceSheet, period_start: date, period_end: date) -> None:
    """
    期間を狭めるマージで、残る日付が新しい期間の外に出るなら断る（件数と最初の 10 件）。
    生成マーカーのルール由来の日付は、新しい期間で作り直されるため対象外（人が変えた日付は対象）
    """
    kept_outside = sorted(
        e.event_date
        for m in sheet.markers
        for e in m.events
        if not (period_start <= e.event_date <= period_end)
        and (not m.is_generated or e.is_manual)
    )
    if kept_outside:
        raise BadRequestError(
            f"{len(kept_outside)} existing event date(s) fall outside the new period "
            f"(e.g. {[d.isoformat() for d in kept_outside[:10]]}). Use replace_events=true to remove them."
        )


def _import_markers(sheet: ReferenceSheet, import_in: ReferenceSheetImport) -> tuple[dict[str, ReferenceSheetMarker], int, int]:
    """
    シートのマーカー（名前 → マーカー）と、足した数・説明を直した数。ファイルに無いマーカーは消さない
    （テンプレートが参照し得るため）
    """
    markers = {m.marker: m for m in sheet.markers}
    created = updated = 0
    for m in import_in.markers:
        existing = markers.get(m.marker)
        if existing is None:
            existing = ReferenceSheetMarker(marker=m.marker, description=m.description)
            sheet.markers.append(existing)
            markers[m.marker] = existing
            created += 1
        elif existing.description != m.description:
            existing.description = m.description
            updated += 1
    return markers, created, updated


def _wanted_dates(import_in: ReferenceSheetImport, markers: dict[str, ReferenceSheetMarker]) -> dict[str, set[date]]:
    """ファイルの日付（マーカー → 日付）。生成マーカーの日付はファイルでは渡せない（events API で直す）"""
    wanted: dict[str, set[date]] = defaultdict(set)
    for e in import_in.events:
        wanted[e.marker].add(e.event_date)
    generated_in_file = sorted(name for name in wanted if markers[name].is_generated)
    if generated_in_file:
        raise BadRequestError(
            f"Markers {generated_in_file} are generated from a rule; edit their dates with the events API instead of importing."
        )
    return dict(wanted)


def _import_dates(
    markers: dict[str, ReferenceSheetMarker], wanted: dict[str, set[date]], replace_events: bool,
) -> tuple[int, int, list[str]]:
    """
    各マーカーの日付をファイルに合わせる。足した数・消した数・日付が変わったマーカー。
    生成マーカーはルール（と人の編集）が管理するので触らない。マージでは、ファイルに日付の無いマーカーも触らない。
    replace_events では、ファイルに無いマーカーの取り込んだ日付も消える（そのマーカーの「欲しい日付」は空）
    """
    created = deleted = 0
    changed: list[str] = []
    for name, marker in markers.items():
        if marker.is_generated:
            continue
        if name not in wanted and not replace_events:
            continue
        to_add, to_delete = _date_changes(marker, wanted.get(name, set()), replace_events)
        for d in to_add:
            marker.events.append(ReferenceSheetEvent(event_date=d))
        for event in to_delete:
            marker.events.remove(event)  # delete-orphan により削除
        if to_add or to_delete:
            changed.append(name)
        created += len(to_add)
        deleted += len(to_delete)
    return created, deleted, changed


def _date_changes(
    marker: ReferenceSheetMarker, wanted: set[date], replace_events: bool,
) -> tuple[list[date], list[ReferenceSheetEvent]]:
    """
    1 つのマーカーで足す日付と消す日付（人の判断が優先）:
    - 人が動かした・取り消した日付（元の日付）は、ファイルにあっても足し直さない
    - replace_events でも、人が変更・追加・取消した日付は消さない（監査の記録）
    """
    current = {e.event_date: e for e in marker.events}
    decided = {e.original_date for e in marker.events if e.is_manual and e.original_date is not None}
    to_add = sorted(wanted - current.keys() - decided)
    to_delete = [e for d, e in current.items() if d not in wanted and not e.is_manual] if replace_events else []
    return to_add, to_delete


def _follow_sheet_changes(db: Session, sheet: ReferenceSheet, changed_markers: list[str]) -> int:
    """
    あるシートを取り込んだ後: 期間が変わったかもしれないので生成マーカーを作り直し、日付が変わったマーカーを
    参照しているテンプレートの未来分を再照合する。作り直したテンプレートの数（マーカーごとに数える）
    """
    changed = changed_markers + _regenerate_sheet_markers(db, sheet)
    regenerated = 0
    for name in changed:
        for template in plan_template_repo.get_by_sheet_marker(db, sheet_id=sheet.id, marker=name):
            _regenerate(db, template, VoidReason.SOURCE_DATE_CHANGED)
            regenerated += 1
    return regenerated


def _check_in_sheet_period(sheet: ReferenceSheet, dates: list[date]) -> None:
    outside = [d for d in dates if not (sheet.period_start <= d <= sheet.period_end)]
    if outside:
        raise BadRequestError(
            f"Dates {[d.isoformat() for d in outside]} are outside the sheet period "
            f"({sheet.period_start} - {sheet.period_end})."
        )


def _propagate_sheet_change(db: Session, sheet_id: int, marker: str) -> int:
    """シート日付の変更を、そのマーカーを参照している全テンプレートへ反映します（同一トランザクション内）。"""
    templates = plan_template_repo.get_by_sheet_marker(db, sheet_id=sheet_id, marker=marker)
    for template in templates:
        _regenerate(db, template, VoidReason.SOURCE_DATE_CHANGED)
    return len(templates)


def add_sheet_events(
    db: Session, sheet_id: int, marker: str, events_in: ReferenceSheetEventsCreate
) -> ReferenceSheetMarker:
    sheet = _get_sheet(db, sheet_id)
    marker_obj = reference_sheet_repo.get_marker(db, sheet_id=sheet_id, marker=marker)
    if not marker_obj:
        raise NotFoundError(f"Marker '{marker}' not found on ReferenceSheet(id={sheet_id}).")
    if _add_events(db, sheet, marker_obj, events_in.dates):
        _propagate_sheet_change(db, sheet_id, marker)
        _commit(db, "Some dates already exist for this marker.")
        db.refresh(marker_obj)
    return _annotate_revertable(db, marker_obj)


def _add_events(db: Session, sheet: ReferenceSheet, marker_obj: ReferenceSheetMarker, dates: list[date]) -> bool:
    """
    マーカーに日付を足します（flush 済み・コミットしない）。何か変われば True。期間の外・今日より前は 400。
    登録済みの日付は無視し、取り消した日付を足し直すと取消を解除する。
    """
    _check_in_sheet_period(sheet, dates)
    _check_not_past(dates, "add")
    existing = {e.event_date: e for e in marker_obj.events}
    new_dates = sorted(set(dates) - existing.keys())
    # 取り消した日付を再度追加した場合は、取消を解除する
    reactivated = [existing[d] for d in set(dates) if d in existing and existing[d].is_cancelled]
    if not new_dates and not reactivated:
        return False
    for d in new_dates:
        # 人が追加した日付（出どころの日付は無い。再生成・再取り込みで消さない）
        marker_obj.events.append(ReferenceSheetEvent(event_date=d, is_manual=True))
    for event in reactivated:
        event.is_cancelled = False
        if event.rule_date is None and event.original_date == event.event_date:
            # 取り込んだ日付の取消を、同じ日付のまま元に戻した: 取り込んだままの日付に戻る
            event.is_manual = False
            event.original_date = None
    db.flush()
    return True


def change_sheet_events(
    db: Session, sheet_id: int, marker: str, batch_in: ReferenceSheetEventsBatch,
) -> ReferenceSheetMarker:
    """
    マーカーの日付をまとめて足す・消します（E.2、2026-11-11。年間の日付の画面の「保存」）。1 トランザクションで、
    参照しているテンプレートの予定の作り直しは最後に 1 回だけ（1 件ずつの API だと、日付ごとに作り直す）。
    規則は 1 件ずつの追加（add_sheet_events）・削除（delete_sheet_event）と同じ: 期間の外・今日より前は不可、
    消す日付は取り込み・ルール由来なら「取消」の記録を残し、人が足した日付なら消える。
    同じ日付を足しつつ消す・別のマーカーの日付・取り消し済みの日付を消す、は 400。
    """
    sheet = _get_sheet(db, sheet_id)
    marker_obj = reference_sheet_repo.get_marker(db, sheet_id=sheet_id, marker=marker)
    if not marker_obj:
        raise NotFoundError(f"Marker '{marker}' not found on ReferenceSheet(id={sheet_id}).")
    events = {e.id: e for e in marker_obj.events}
    unknown = sorted(set(batch_in.remove) - events.keys())
    if unknown:
        raise BadRequestError(f"Dates {unknown} are not dates of marker '{marker}'.")
    to_remove = [events[i] for i in sorted(set(batch_in.remove))]
    cancelled = [e.id for e in to_remove if e.is_cancelled]
    if cancelled:
        raise BadRequestError(f"Dates {cancelled} are already cancelled.")
    both = sorted({e.event_date for e in to_remove} & set(batch_in.add))
    if both:
        raise BadRequestError(f"Dates {[d.isoformat() for d in both]} are both added and removed.")
    _check_not_past([e.event_date for e in to_remove], "delete")

    changed = _add_events(db, sheet, marker_obj, batch_in.add) if batch_in.add else False
    for event in to_remove:
        _cancel_sheet_event(db, marker_obj, event)
        changed = True
    if changed:
        _propagate_sheet_change(db, sheet_id, marker)  # 予定の作り直しは 1 回だけ
        _commit(db, "Some dates already exist for this marker.")
        db.refresh(marker_obj)
    return _annotate_revertable(db, marker_obj)


def update_sheet_event(db: Session, event_id: int, event_in: ReferenceSheetEventUpdate) -> ReferenceSheetEvent:
    event = reference_sheet_repo.get_event(db, event_id=event_id)
    if not event:
        raise NotFoundError(f"ReferenceSheetEvent(id={event_id}) not found.")
    marker_obj = event.marker_def
    if event.is_cancelled:
        raise BadRequestError(f"ReferenceSheetEvent(id={event_id}) has been cancelled. Revert it or add the date again.")
    _check_in_sheet_period(marker_obj.sheet, [event_in.event_date])
    _check_not_past([event.event_date, event_in.event_date], "move")

    if event.event_date != event_in.event_date:
        if event.original_date is None and (event.rule_date is not None or not event.is_manual):
            event.original_date = event.event_date  # 取り込み・ルール由来の日付を初めて動かす: 元の日付を残す
        event.event_date = event_in.event_date
        event.is_manual = True  # 人の編集は再生成・再取り込みで上書きしない
        try:
            db.flush()
        except IntegrityError as e:
            db.rollback()
            raise DuplicatedRecordError(f"Date {event_in.event_date} already exists for this marker.") from e
        _propagate_sheet_change(db, marker_obj.sheet_id, marker_obj.marker)
        _commit(db, f"Date {event_in.event_date} already exists for this marker.")
        db.refresh(event)
    _annotate_revertable(db, marker_obj)
    return event


def _cancel_sheet_event(db: Session, marker_obj: ReferenceSheetMarker, event: ReferenceSheetEvent) -> bool:
    """
    日付を取り消します（flush 済み）。物理削除した場合は True を返します。
    - ルール・取り込み由来の日付: 出どころを残して「取消」として記録する（再生成・再取り込みでも復活しない）
    - 人が追加した日付: 出どころが無いため削除する（記録を残すと、その日にルールの日付を置けなくなる）
    """
    if event.rule_date is None and event.original_date is None and event.is_manual:
        marker_obj.events.remove(event)  # delete-orphan により削除
        db.flush()
        return True
    if event.original_date is None:
        event.original_date = event.event_date
    event.is_cancelled = True
    event.is_manual = True
    db.flush()
    return False


def delete_sheet_event(db: Session, event_id: int) -> ReferenceSheetEventResponse:
    event = reference_sheet_repo.get_event(db, event_id=event_id)
    if not event:
        raise NotFoundError(f"ReferenceSheetEvent(id={event_id}) not found.")
    marker_obj = event.marker_def
    sheet_id, marker = marker_obj.sheet_id, marker_obj.marker
    if event.is_cancelled:
        raise BadRequestError(f"ReferenceSheetEvent(id={event_id}) is already cancelled.")
    _check_not_past([event.event_date], "delete")

    before = ReferenceSheetEventResponse.model_validate(event)
    removed = _cancel_sheet_event(db, marker_obj, event)
    if not removed:
        _annotate_revertable(db, marker_obj)
    result = before if removed else ReferenceSheetEventResponse.model_validate(event)
    _propagate_sheet_change(db, sheet_id, marker)
    db.commit()
    return result


def revert_sheet_event(db: Session, event_id: int) -> ReferenceSheetEvent:
    """生成マーカーの日付に対する人の編集・取消を取り消し、ルールの日付に戻します。"""
    event = reference_sheet_repo.get_event(db, event_id=event_id)
    if not event:
        raise NotFoundError(f"ReferenceSheetEvent(id={event_id}) not found.")
    marker_obj = event.marker_def
    if not marker_obj.is_generated or event.rule_date is None:
        raise BadRequestError("Only dates generated from a marker rule can be reverted. Delete manually added dates instead.")
    if not event.is_manual:
        raise BadRequestError(f"ReferenceSheetEvent(id={event_id}) has not been edited or cancelled.")
    _check_not_past([event.event_date], "revert")

    event.is_manual = False
    event.is_cancelled = False
    event.original_date = None
    db.flush()
    _generate_marker_events(db, marker_obj)
    if event not in marker_obj.events or event.event_date < _local_today():
        # ルールがこの発生枠をもう作らない、またはその日に人が置いた日付があるため、戻すと日付が消えてしまう
        db.rollback()
        raise BadRequestError(
            f"The rule no longer produces a free date for ReferenceSheetEvent(id={event_id}). Edit or delete it instead."
        )
    _propagate_sheet_change(db, marker_obj.sheet_id, marker_obj.marker)
    _commit(db, "Marker dates conflict with each other.")
    db.refresh(event)
    _annotate_revertable(db, marker_obj)
    return event


# ==========================================
# PlanTemplate Service
# ==========================================
def _get_biz(db: Session, biz_id: int) -> BizMaster:
    biz = biz_master_repo.get(db, id=biz_id)
    if not biz:
        raise NotFoundError(f"BizMaster(id={biz_id}) not found.")
    return biz


def _validate_sheet_reference(db: Session, biz: BizMaster, sheet_id: int, marker: str) -> None:
    """参照するシートが存在し、同じ顧客のものであり、マーカーがシート上に実在することを確認します。"""
    sheet = reference_sheet_repo.get(db, id=sheet_id)
    if not sheet:
        raise NotFoundError(f"ReferenceSheet(id={sheet_id}) not found.")
    if sheet.client_code != biz.client_code:
        raise BadRequestError(
            f"ReferenceSheet(id={sheet_id}) belongs to client '{sheet.client_code}', "
            f"but BizMaster(id={biz.id}) belongs to client '{biz.client_code}'."
        )
    if not reference_sheet_repo.get_marker(db, sheet_id=sheet_id, marker=marker):
        raise NotFoundError(f"Marker '{marker}' not found on ReferenceSheet(id={sheet_id}).")


def _get_calendar_if_set(db: Session, calendar_id: int | None) -> BusinessCalendar | None:
    if calendar_id is None:
        return None
    calendar = business_calendar_repo.get(db, id=calendar_id)
    if not calendar:
        raise NotFoundError(f"BusinessCalendar(id={calendar_id}) not found.")
    return calendar


def _check_calendar_settings(template: PlanTemplate) -> None:
    """振替・スキップ・営業日オフセットにはカレンダーが必要（DBのCHECK制約と同じ条件を、分かりやすいエラーで返す）"""
    if template.business_calendar_id is not None:
        return
    if template.holiday_policy != HolidayPolicy.AS_IS:
        raise BadRequestError(f"holiday_policy '{template.holiday_policy.value}' requires business_calendar_id.")
    if template.offset_day_unit == DayUnit.BUSINESS:
        raise BadRequestError("offset_day_unit 'business' requires business_calendar_id.")


def _get_locked_template(db: Session, template_id: int) -> PlanTemplate:
    template = plan_template_repo.get_locked(db, id=template_id)
    if not template:
        raise NotFoundError(f"PlanTemplate(id={template_id}) not found.")
    return template


def refuse_if_draft_pending(db: Session, template_id: int, except_change_id: int | None = None) -> None:
    """
    承認待ちの下書きがあるテンプレートは、ほかの変更（直接の保存・削除・指定日・別の下書き）を受け付けない（409）。
    テンプレートの行ロックを取ってから呼ぶ（下書きの作成も同じ行ロックを取るので、すれ違わない）。
    except_change_id: 自分の下書きを直すときは、その下書きを数えない。
    """
    draft = plan_template_change_repo.get_draft_for_template(db, template_id=template_id)
    if draft is None or draft.id == except_change_id:
        return
    drafter = db.get(StaffUser, draft.drafted_by_id)
    raise DuplicatedRecordError(
        f"PlanTemplate(id={template_id}) has a pending draft (change {draft.id} by "
        f"{drafter.name if drafter else draft.drafted_by_id}); approve, reject or withdraw it first.",
        error_code=ErrorCode.TEMPLATE_DRAFT_PENDING,
    )


def _get_locked_template_without_draft(db: Session, template_id: int) -> PlanTemplate:
    """業務管理者の直接の変更用: 行ロックを取り、承認待ちの下書きがあれば断る"""
    template = _get_locked_template(db, template_id)
    refuse_if_draft_pending(db, template_id)
    return template


# 変更の前後を比べる・履歴に残すテンプレートの設定（生成・表示・締切に効く項目すべて。revision などの管理用は含めない）
TEMPLATE_SETTING_FIELDS = (
    "biz_master_id", "template_type", *PlanTemplateUpdate.model_fields.keys(),
)


def _record_direct_change(
    db: Session, template: PlanTemplate, kind: TemplateChangeKind, actor_id: int, *,
    base_revision: int | None, before: dict[str, Any] | None, changes: dict[str, Any] | None,
    add_dates: list[date] | None = None, remove_dates: list[date] | None = None,
) -> None:
    """
    業務管理者の直接の保存を、変更の履歴に「下書き即承認」（is_direct）として 1 行残します（コミットしない）。
    API からの保存だけが actor_id を渡す（テスト・サービスの直接の呼び出しでは残さない）。
    """
    now = _now()
    plan_template_change_repo.add(db, change=PlanTemplateChange(
        biz_master_id=template.biz_master_id, template_id=template.id, kind=kind,
        status=TemplateChangeStatus.APPROVED, is_direct=True, base_revision=base_revision,
        changes=changes, add_dates=add_dates or [], remove_dates=remove_dates or [],
        before_values=before, applied_revision=template.revision,
        drafted_by_id=actor_id, drafted_at=now, decided_by_id=actor_id, decided_at=now,
    ))


def template_settings(template: PlanTemplate) -> dict[str, Any]:
    """テンプレートの設定の写し（JSON にできる値。指定日は日付の一覧）。下書きの「何も変わらない」の判定と変更前の値に使う"""
    values: dict[str, Any] = {field: getattr(template, field) for field in TEMPLATE_SETTING_FIELDS}
    if template.template_type == TemplateType.EXPLICIT_DATES:
        values["dates"] = sorted(d.local_date for d in template.explicit_dates)
    return jsonable_encoder(values)


def create_template(db: Session, template_in: PlanTemplateCreate, actor_id: int | None = None) -> PlanTemplate:
    template = _apply_template_create(db, template_in)
    if actor_id is not None:
        _record_direct_change(
            db, template, TemplateChangeKind.CREATE, actor_id,
            base_revision=None, before=None, changes=template_in.model_dump(mode="json"),
        )
    _commit(db, "PlanTemplate could not be created due to a conflicting record.")
    db.refresh(template)
    return _with_next_occurrence(db, [template])[0]


def _apply_template_create(db: Session, template_in: PlanTemplateCreate) -> PlanTemplate:
    """
    テンプレートを作り、最初の予定を生成します（コミットしない）。
    本物の保存（create_template）と、保存の前の確認（dry_run_create）の両方がこれを使います。
    """
    biz = _get_biz(db, template_in.biz_master_id)
    if isinstance(template_in, ReferenceSheetTemplateCreate):
        _validate_sheet_reference(db, biz, template_in.reference_sheet_id, template_in.reference_event_marker)
    _get_calendar_if_set(db, template_in.business_calendar_id)

    template = PlanTemplate(**template_in.model_dump(exclude={"dates", "ui_metadata", "start_prompt"}))
    template.ui_metadata = ui_metadata_to_dict(template_in.ui_metadata)
    # 開始時刻に知らせる: 指定がなければ、顧客側の出来事（外からの合図を待つ）の業務はオフ、ほかはオン（並行稼働 P.1）
    template.start_prompt = (
        template_in.start_prompt if template_in.start_prompt is not None else biz.execution_type != ExecutionType.LIGHTWEIGHT
    )
    _check_deadline(template)
    if isinstance(template_in, ExplicitDatesTemplateCreate):
        template.explicit_dates = [PlanExplicitDate(local_date=d) for d in sorted(set(template_in.dates))]
    db.add(template)
    db.flush()

    _ensure_next_occurrence(db, template)
    return template


def get_template(db: Session, template_id: int) -> PlanTemplate:
    template = plan_template_repo.get(db, id=template_id)
    if not template:
        raise NotFoundError(f"PlanTemplate(id={template_id}) not found.")
    return _with_next_occurrence(db, [template])[0]


def get_templates(db: Session, biz_id: int) -> list[PlanTemplate]:
    _get_biz(db, biz_id)
    return _with_next_occurrence(db, plan_template_repo.get_by_biz(db, biz_master_id=biz_id))


def _normalize_deadline_update(template: PlanTemplate, update_data: dict) -> None:
    """締切の決め方を変えたら、新しい決め方で使わない項目を空にする（送られた値は残して、組み合わせの確認で誤りにする）"""
    mode = update_data.get("deadline_mode")
    if mode is None:
        return
    for field in set().union(*DEADLINE_MODE_FIELDS.values()) - DEADLINE_MODE_FIELDS[mode]:
        update_data.setdefault(field, None)
    if mode == DeadlineMode.FIXED_TIME and update_data.get("deadline_day_offset", template.deadline_day_offset) is None:
        update_data["deadline_day_offset"] = 0


def _check_deadline(template: PlanTemplate) -> None:
    """締切の項目の組み合わせと、固定の時刻が同じ日の開始より後か（前なら翌日以降を指定してもらう）"""
    problem = deadline_problem(
        template.deadline_mode, template.deadline_offset_minutes, template.deadline_time, template.deadline_day_offset,
    )
    if problem:
        raise BadRequestError(problem)
    if template.deadline_mode == DeadlineMode.FIXED_TIME and template.deadline_day_offset == 0:
        assert template.deadline_time is not None
        start = template.start_time.hour * 60 + template.start_time.minute + (template.offset_minutes or 0)
        deadline = template.deadline_time.hour * 60 + template.deadline_time.minute
        if deadline <= start:
            raise BadRequestError(
                "deadline_time must be after the start on the scheduled day; use deadline_day_offset for a later day."
            )


def update_template(
    db: Session, template_id: int, template_in: PlanTemplateUpdate, actor_id: int | None = None
) -> PlanTemplate:
    locked = _get_locked_template_without_draft(db, template_id)
    base_revision, before = locked.revision, template_settings(locked)
    template, _ = _apply_template_update(db, template_id, template_in)
    # 何も変わらない保存（今と同じ値）は履歴に残さない
    if actor_id is not None and template_settings(template) != before:
        _record_direct_change(
            db, template, TemplateChangeKind.UPDATE, actor_id, base_revision=base_revision, before=before,
            changes=template_in.model_dump(mode="json", exclude_unset=True),
        )
    _commit(db, "PlanTemplate could not be updated due to a conflicting record.")
    db.refresh(template)
    return _with_next_occurrence(db, [template])[0]


def _apply_template_update(db: Session, template_id: int, template_in: PlanTemplateUpdate) -> tuple[PlanTemplate, bool]:
    """
    テンプレートの変更を当てはめます（コミットしない）。戻り値の 2 つ目は、生成に影響する変更（日付の作り直し）があったか。
    本物の保存（update_template）と、保存の前の確認（dry_run_update）の両方がこれを使うので、2 つの結果は同じになります。
    """
    template = _get_locked_template(db, template_id)
    update_data = template_in.model_dump(exclude_unset=True, exclude={"ui_metadata"})
    ui_changed = "ui_metadata" in template_in.model_fields_set

    _check_update_fields(db, template, update_data)
    changes = _classify_update(template, update_data)  # 締切の項目は、ここで決め方に合わせて整える
    if "business_calendar_id" in update_data:
        _get_calendar_if_set(db, update_data["business_calendar_id"])

    for field, value in update_data.items():
        setattr(template, field, value)
    _check_calendar_settings(template)
    _check_deadline(template)
    if ui_changed:
        # 表示のみの変更のためリビジョンは上げず、キャッシュの実効値だけ更新する
        template.ui_metadata = ui_metadata_to_dict(template_in.ui_metadata)

    _follow_template_update(db, template, changes, ui_changed=ui_changed)
    # セッションは autoflush しない。名前・表示だけの変更は何も書き込まないまま残るため、ここで書き出す
    # （このあと行ロックを取り直す処理（populate_existing）が、書き出していない変更を DB の値で上書きして消すため）
    db.flush()
    return template, changes.generation


@dataclass(frozen=True)
class _TemplateUpdateChanges:
    """テンプレートの変更が何に効くか（当てはめる前の値と比べて決める）"""
    name: bool        # 名前が変わった（予定の表示名を写し直す）
    generation: bool  # 生成に影響する（改訂を上げて日付を作り直す）
    deadline: bool    # 締切の設定・タイムゾーンが変わった（リリース前のオーダーの締切を写し直す）


def _check_update_fields(db: Session, template: PlanTemplate, update_data: dict) -> None:
    """型に合わない項目・必須項目への null・参照するシートとマーカーを、この順で確かめる（何も変えない）"""
    not_applicable = set()
    if template.template_type != TemplateType.RRULE:
        not_applicable |= update_data.keys() & RRULE_FIELDS
    if template.template_type != TemplateType.REFERENCE_SHEET_BASED:
        not_applicable |= update_data.keys() & SHEET_FIELDS
    if not_applicable:
        raise BadRequestError(
            f"Fields {sorted(not_applicable)} are not applicable to template type '{template.template_type.value}'."
        )
    null_fields = sorted(k for k, v in update_data.items() if v is None and k not in NULLABLE_UPDATE_FIELDS)
    if null_fields:
        raise BadRequestError(f"Fields {null_fields} must not be null.")

    if update_data.keys() & {"reference_sheet_id", "reference_event_marker"}:
        _validate_sheet_reference(
            db,
            _get_biz(db, template.biz_master_id),
            update_data.get("reference_sheet_id", template.reference_sheet_id),
            update_data.get("reference_event_marker", template.reference_event_marker),
        )


def _classify_update(template: PlanTemplate, update_data: dict) -> _TemplateUpdateChanges:
    """
    変更が何に効くかを決める（当てはめる前に呼ぶ）。締切の項目は決め方に合わせて update_data の中で整える
    （使わなくなった項目を空にする）。締切が変わったかは、整えた後の値で比べる
    """
    name_changed = "name" in update_data and update_data["name"] != template.name
    # 名前・締切・開始時刻に知らせるかは生成に影響しない（改訂・再生成しない）
    generation_changed = any(
        getattr(template, k) != v for k, v in update_data.items() if k not in NON_GENERATION_FIELDS and k not in DEADLINE_FIELDS
    )
    _normalize_deadline_update(template, update_data)
    # 締切の設定・タイムゾーンが変わったら、リリース前のオーダーの締切を写し直してもらう（受け手は coordination）
    deadline_changed = any(
        getattr(template, k) != v for k, v in update_data.items() if k in DEADLINE_FIELDS or k == "timezone"
    )
    return _TemplateUpdateChanges(name=name_changed, generation=generation_changed, deadline=deadline_changed)


def _follow_template_update(db: Session, template: PlanTemplate, changes: _TemplateUpdateChanges, *, ui_changed: bool) -> None:
    """当てはめた変更に追従する: 日付の作り直し・予定の表示名と表示の写し直し・締切の変更の知らせ"""
    if changes.generation:
        _regenerate(db, template, VoidReason.TEMPLATE_REVISED)
    if changes.name:
        plan_cache_repo.rename_template(db, template_id=template.id, title=template.name)
    if ui_changed:
        plan_cache_repo.refresh_ui_metadata(db, template=template)
    if changes.deadline:
        db.flush()
        template_events.notify_deadline_changed(db, template)


# ==========================================
# 保存の前の確認（試し実行）。コミットしない: 呼び出し側が infrastructure.dry_run の中で呼び、最後にロールバックする
# ==========================================
@dataclass(frozen=True)
class DryRunOccurrence:
    """試し実行の時点で有効な予定 1 件（ORM のオブジェクトではなく値。ロールバックの後も読める）"""
    slot: tuple[str, str]      # 発生枠（slot_events.SlotKey の kind と ref）。変更の前後で同じ枠を突き合わせる
    occurrence_date: date
    scheduled_start: datetime
    scheduled_end: datetime
    manual: bool               # 人が変更した・臨時に追加した予定（ルールを変えても動かない）


def _dry_run_occurrences(db: Session, template: PlanTemplate, since: datetime, until: datetime) -> list[DryRunOccurrence]:
    db.flush()
    slot_events.publish_pending(db)  # 受け手（オーダーの追従）にも、ここまでの変更を反映してもらう
    db.flush()
    result = []
    for i in plan_instance_repo.get_live_in_window(db, template_id=template.id, lower=since, upper=until):
        key = slot_events.slot_key_of(db, i)
        result.append(DryRunOccurrence(
            slot=(key.kind, str(key.ref)), occurrence_date=i.occurrence_date, scheduled_start=i.scheduled_start,
            scheduled_end=i.scheduled_end, manual=i.origin != InstanceOrigin.GENERATED,
        ))
    return sorted(result, key=lambda o: (o.scheduled_start, o.slot))


def dry_run_materialize(
    db: Session, template_id: int, since: datetime, until: datetime
) -> tuple[list[DryRunOccurrence], datetime]:
    """
    【コミットしない】今の設定のまま until まで予定を生成し、[since, 範囲の終わり) の有効な予定（変更の前の姿）と、
    範囲の終わりを返します。変更の前と後を同じ範囲で比べるためのものです。
    範囲の終わりは until と生成済みの終わりの遅いほう（変更は生成済みの範囲すべてを作り直すので、それより短くは見せない）。
    """
    template = _get_locked_template(db, template_id)
    if template.materialized_until is not None and template.materialized_until > until:
        until = template.materialized_until
    _ensure_materialized(db, template, until)
    return _dry_run_occurrences(db, template, since, until), until


def dry_run_create(
    db: Session, template_in: PlanTemplateCreate, since: datetime, until: datetime
) -> tuple[int, list[DryRunOccurrence], datetime]:
    """
    【コミットしない】テンプレートを作って until まで予定を生成し、（仮の）テンプレート ID、[since, 範囲の終わり) の予定、
    範囲の終わりを返します。作り方は create_template と同じ（入力の誤りも同じ例外）。
    範囲の終わりは until と、作成時に生成される範囲（90 日先、または次の予定まで）の遅いほう。
    """
    template = _apply_template_create(db, template_in)
    if template.materialized_until is not None and template.materialized_until > until:
        until = template.materialized_until
    _ensure_materialized(db, template, until)
    return template.id, _dry_run_occurrences(db, template, since, until), until


def dry_run_update(
    db: Session, template_id: int, template_in: PlanTemplateUpdate, since: datetime, until: datetime,
    add_dates: list[date] | None = None, remove_dates: list[date] | None = None,
) -> tuple[list[DryRunOccurrence], bool]:
    """
    【コミットしない】変更を当てはめて、[since, until) の有効な予定（変更の後の姿）と、日付の作り直しがあったかを返します。
    当てはめ方は update_template と同じ（人が変更・取消した日はそのまま残る）。入力の誤りは update_template と同じ例外になります。
    add_dates / remove_dates: 指定日の追加・削除も当てはめる（下書きの確認用。直接の変更の /dates と同じ処理、項目の変更の後）。
    """
    generation_changed = False
    if template_in.model_fields_set:
        _, generation_changed = _apply_template_update(db, template_id, template_in)
    if _apply_explicit_dates(db, template_id, add=add_dates or [], remove=remove_dates or []):
        generation_changed = True
    template = _get_locked_template(db, template_id)
    return _dry_run_occurrences(db, template, since, until), generation_changed


def dry_run_delete(db: Session, template_id: int, since: datetime, until: datetime) -> list[DryRunOccurrence]:
    """
    【コミットしない】テンプレートを削除して、[since, until) に残る有効な予定を返します（未来の予定は無効化されるため、
    ふつうは空）。削除の仕方は delete_template と同じ。
    """
    template = _apply_template_delete(db, template_id)
    return _dry_run_occurrences(db, template, since, until)


def delete_template(db: Session, template_id: int, actor_id: int | None = None) -> PlanTemplate:
    """論理削除。未来のインスタンスは無効化し、過去のインスタンスは履歴として残します。"""
    locked = _get_locked_template_without_draft(db, template_id)
    base_revision, before = locked.revision, template_settings(locked)
    template = _apply_template_delete(db, template_id)
    if actor_id is not None:
        _record_direct_change(
            db, template, TemplateChangeKind.DELETE, actor_id, base_revision=base_revision, before=before, changes=None,
        )
    db.commit()

    # 全局scopeにより削除済みは通常の取得で見えないため、解除して読み直す
    deleted = plan_template_repo.get_including_deleted(db, id=template_id)
    assert deleted is not None
    return deleted


def _apply_template_delete(db: Session, template_id: int) -> PlanTemplate:
    """
    テンプレートを論理削除します（コミットしない）。未来のインスタンスは無効化し、過去は履歴として残します。
    直接の削除（delete_template）と、削除の下書きの確認・承認が使います。
    """
    template = _get_locked_template(db, template_id)
    future = plan_instance_repo.get_live_in_window(db, template_id=template.id, lower=_now(), upper=FAR_FUTURE)
    future_ids = [i.id for i in future]
    if future_ids:
        plan_instance_void_repo.void_many(db, instance_ids=future_ids, reason=VoidReason.TEMPLATE_DELETED)
        plan_cache_repo.purge(db, instance_ids=future_ids)
    template.soft_delete()
    db.flush()
    return template


def restore_template(db: Session, template_id: int) -> PlanTemplate:
    template = plan_template_repo.get_locked(db, id=template_id, include_deleted=True)
    if not template or not template.is_deleted:
        raise NotFoundError(f"Deleted PlanTemplate(id={template_id}) not found.")
    refuse_if_draft_pending(db, template_id)

    template.restore()
    # 削除期間中は生成していないため、今からホライズンまでを新しいリビジョンで生成し直す
    template.revision += 1
    template.materialized_until = None
    _ensure_next_occurrence(db, template)

    _commit(db, "PlanTemplate could not be restored due to a conflicting record.")
    db.refresh(template)
    return _with_next_occurrence(db, [template])[0]


def _apply_explicit_dates(
    db: Session, template_id: int, add: list[date] | tuple[()] = (), remove: list[date] | tuple[()] = ()
) -> bool:
    """
    指定日を足す・外して、変わったら作り直します（コミットしない）。戻り値は変わったか。
    足す日付が登録済みなら無視、外す日付が無ければ 404。足すのは指定日テンプレートだけ（400）。
    直接の変更（add_explicit_dates / remove_explicit_date）と、下書きの確認・承認が使います。
    """
    if not add and not remove:
        return False
    template = _get_locked_template(db, template_id)
    if add and template.template_type != TemplateType.EXPLICIT_DATES:
        raise BadRequestError("Explicit dates can only be added to EXPLICIT_DATES templates.")

    changed = False
    for local_date in remove:
        if not plan_template_repo.remove_explicit_date(db, template=template, local_date=local_date):
            raise NotFoundError(f"Date {local_date} not found on PlanTemplate(id={template_id}).")
        changed = True
    if add and plan_template_repo.add_explicit_dates(db, template=template, dates=add):
        changed = True
    if changed:
        _regenerate(db, template, VoidReason.SOURCE_DATE_CHANGED)
    return changed


def add_explicit_dates(db: Session, template_id: int, dates: list[date], actor_id: int | None = None) -> PlanTemplate:
    template = _get_locked_template_without_draft(db, template_id)
    base_revision, before = template.revision, template_settings(template)
    new_dates = sorted(set(dates) - {d.local_date for d in template.explicit_dates})
    if _apply_explicit_dates(db, template_id, add=dates):
        if actor_id is not None:
            _record_direct_change(
                db, template, TemplateChangeKind.UPDATE, actor_id, base_revision=base_revision, before=before,
                changes={}, add_dates=new_dates,
            )
        _commit(db, "Some dates already exist for this template.")
        db.refresh(template)
    return _with_next_occurrence(db, [template])[0]


def remove_explicit_date(db: Session, template_id: int, local_date: date, actor_id: int | None = None) -> PlanTemplate:
    template = _get_locked_template_without_draft(db, template_id)
    base_revision, before = template.revision, template_settings(template)
    _apply_explicit_dates(db, template_id, remove=[local_date])
    if actor_id is not None:
        _record_direct_change(
            db, template, TemplateChangeKind.UPDATE, actor_id, base_revision=base_revision, before=before,
            changes={}, remove_dates=[local_date],
        )
    db.commit()
    db.refresh(template)
    return _with_next_occurrence(db, [template])[0]


# ==========================================
# PlanInstance / Occurrence Service
# ==========================================
def get_instances(db: Session, template_id: int, skip: int = 0, limit: int = 100) -> list[PlanInstanceResponse]:
    """無効化済みも含めた全インスタンス（監査用）。削除済みテンプレートの履歴も参照できます。"""
    if not plan_template_repo.get_including_deleted(db, id=template_id):
        raise NotFoundError(f"PlanTemplate(id={template_id}) not found.")

    return [
        _to_instance_response(instance, void)
        for instance, void in plan_instance_repo.get_with_void_status(
            db, template_id=template_id, skip=skip, limit=limit
        )
    ]


def _to_instance_response(instance: PlanInstance, void: PlanInstanceVoid | None) -> PlanInstanceResponse:
    return PlanInstanceResponse(
        id=instance.id,
        template_id=instance.template_id,
        template_revision=instance.template_revision,
        scheduled_start=instance.scheduled_start,
        scheduled_end=instance.scheduled_end,
        source_sheet_event_id=instance.source_sheet_event_id,
        occurrence_date=instance.occurrence_date,
        origin=instance.origin,
        overrides_instance_id=instance.overrides_instance_id,
        ui_metadata=instance.ui_metadata,
        generated_at=instance.generated_at,
        voided=void is not None,
        void_reason=void.reason if void else None,
        void_note=void.note if void else None,
        voided_at=void.voided_at if void else None,
    )


def _get_live_instance_for_manual_change(db: Session, instance_id: int) -> tuple[PlanInstance, PlanTemplate]:
    """
    手動変更の対象インスタンスを取得します。
    テンプレートを行ロックしてから無効化状態を確認するため、再生成や他の手動変更と競合しません。
    """
    found = plan_instance_repo.get_with_void(db, id=instance_id)
    if not found:
        raise NotFoundError(f"PlanInstance(id={instance_id}) not found.")
    instance, _ = found

    template = plan_template_repo.get_locked(db, id=instance.template_id)
    if not template:
        raise BadRequestError(f"PlanTemplate(id={instance.template_id}) of this instance has been deleted.")

    # ロック取得後に改めて確認（ロック待ちの間に無効化されている可能性があるため）
    found = plan_instance_repo.get_with_void(db, id=instance_id)
    assert found is not None
    if found[1] is not None:
        raise DuplicatedRecordError(
            f"PlanInstance(id={instance_id}) is already voided ({found[1].reason.value}).",
            error_code=ErrorCode.INSTANCE_ALREADY_VOIDED,
        )
    return instance, template


def _reject_past_start(start: datetime) -> None:
    """予定は未来に向けて立てるもののため、変更後・追加する予定の開始が過去の場合は受け付けない"""
    if start < _now():
        raise BadRequestError(
            f"Cannot schedule in the past ({start.isoformat()}). Choose a start time later than now."
        )


def _chain_root(db: Session, instance: PlanInstance) -> PlanInstance:
    """オーバーライドの連鎖を遡り、起点（ルール生成または臨時追加）のインスタンスを返します"""
    root = instance
    while root.overrides_instance_id is not None:
        parent = plan_instance_repo.get(db, id=root.overrides_instance_id)
        assert parent is not None
        root = parent
    return root


def _is_revertable(db: Session, instance: PlanInstance, now: datetime) -> bool:
    """revert_instance が受け付ける条件（ルール生成の予定の手動変更で、元の日時がまだ未来）"""
    if instance.origin != InstanceOrigin.OVERRIDE:
        return False
    root = _chain_root(db, instance)
    return root.origin == InstanceOrigin.GENERATED and root.scheduled_start >= now


def override_instance(
    db: Session, instance_id: int, override_in: PlanInstanceOverride, actor_staff_id: int | None = None
) -> PlanInstanceResponse:
    """
    特定のインスタンスだけ時刻を変更します（テンプレートのルールは変えません）。
    内部では、元インスタンスを MANUAL で無効化し、同じ発生枠を引き継いだ新しいインスタンスを追記します。
    """
    original, template = _get_live_instance_for_manual_change(db, instance_id)

    start = override_in.scheduled_start
    end = override_in.scheduled_end or start + (original.scheduled_end - original.scheduled_start)
    if start == original.scheduled_start and end == original.scheduled_end:
        raise BadRequestError("The new time is the same as the current one.")
    _reject_past_start(start)

    # ui_metadata: 省略時は元インスタンスの値を引き継ぐ / null ならクリア
    ui_metadata = (
        ui_metadata_to_dict(override_in.ui_metadata)
        if "ui_metadata" in override_in.model_fields_set
        else original.ui_metadata
    )

    slot_events.mark_person_change(db, actor_staff_id, override_in.note)
    plan_instance_void_repo.void_many(db, instance_ids=[original.id], reason=VoidReason.MANUAL, note=override_in.note)
    plan_cache_repo.purge(db, instance_ids=[original.id])
    replacement = plan_instance_repo.add_override(
        db, template=template, original=original, start=start, end=end, ui_metadata=ui_metadata
    )
    plan_cache_repo.add_for_instances(db, template=template, instances=[replacement])

    _commit(db, f"PlanInstance(id={instance_id}) was changed concurrently. Please retry.")
    db.refresh(replacement)
    return _to_instance_response(replacement, None)


def cancel_instance(
    db: Session, instance_id: int, cancel_in: PlanInstanceCancel, actor_staff_id: int | None = None
) -> PlanInstanceResponse:
    """特定のインスタンスだけを取り消します。ルールからの再生成でも復活しません。"""
    instance, _ = _get_live_instance_for_manual_change(db, instance_id)

    slot_events.mark_person_change(db, actor_staff_id, cancel_in.note)
    plan_instance_void_repo.void_many(db, instance_ids=[instance.id], reason=VoidReason.MANUAL, note=cancel_in.note)
    plan_cache_repo.purge(db, instance_ids=[instance.id])
    _commit(db, f"PlanInstance(id={instance_id}) was changed concurrently. Please retry.")

    found = plan_instance_repo.get_with_void(db, id=instance_id)
    assert found is not None
    return _to_instance_response(*found)


def revert_instance(
    db: Session, instance_id: int, revert_in: PlanInstanceRevert, actor_staff_id: int | None = None
) -> PlanRevertResponse:
    """
    手動変更（オーバーライド・取消）を取り消し、その発生枠をテンプレートのルールの管理下に戻します。
    置き換えの連鎖のどのインスタンスを指定しても、連鎖全体（=1つの発生枠）が対象になります。
    内部では、有効なオーバーライドを REVERTED で無効化し、解除記録を追記したうえで、ルールから再生成します。
    """
    found = plan_instance_repo.get_with_void(db, id=instance_id)
    if not found:
        raise NotFoundError(f"PlanInstance(id={instance_id}) not found.")
    template = plan_template_repo.get_locked(db, id=found[0].template_id)
    if not template:
        raise BadRequestError(f"PlanTemplate(id={found[0].template_id}) of this instance has been deleted.")

    root = _revertable_root(db, found[0], instance_id)
    slot_events.mark_person_change(db, actor_staff_id, revert_in.note)
    voided = _void_chain_end(db, root, revert_in.note)
    plan_slot_release_repo.add(db, root_instance_id=root.id, template_id=template.id, note=revert_in.note)
    # 解除された発生枠をルールから再生成（他の発生枠は変わらないため、そのまま維持される）
    _regenerate(db, template, VoidReason.TEMPLATE_REVISED)
    _commit(db, f"PlanInstance(id={instance_id}) was changed concurrently. Please retry.")

    restored = _restored_instance(db, template, root)
    return PlanRevertResponse(
        voided_instance=_instance_response_or_none(db, voided), restored_instance=_instance_response_or_none(db, restored),
    )


def _revertable_root(db: Session, instance: PlanInstance, instance_id: int) -> PlanInstance:
    """
    連鎖を遡った起点（ルールから生成されたインスタンス）。戻せない理由があれば、この順で断る:
    臨時の追加・手動の変更や取消がない・解除済み・元の発生枠が過去
    """
    root = _chain_root(db, instance)
    if root.origin == InstanceOrigin.ONE_OFF:
        raise BadRequestError("One-off instances are not tied to the rule, so they cannot be reverted. Cancel it instead.")

    root_found = plan_instance_repo.get_with_void(db, id=root.id)
    assert root_found is not None
    root_void = root_found[1]
    if root_void is None or root_void.reason != VoidReason.MANUAL:
        raise BadRequestError(f"PlanInstance(id={instance_id}) has not been manually changed or cancelled.")
    if plan_slot_release_repo.exists(db, root_instance_id=root.id):
        raise DuplicatedRecordError(
            f"The manual change of PlanInstance(id={instance_id}) has already been reverted.",
            error_code=ErrorCode.INSTANCE_ALREADY_VOIDED,
        )
    # 過去の発生枠は再生成されない（過去は不変）ため、戻すとカレンダーから消えてしまう
    if root.scheduled_start < _now():
        raise BadRequestError(
            "The original occurrence is in the past and cannot be regenerated. Cancel or override it instead."
        )
    return root


def _void_chain_end(db: Session, root: PlanInstance, note: str | None) -> PlanInstance | None:
    """連鎖の末端（現在のオーバーライド）が有効なら REVERTED で無効にし、キャッシュから外す。無効にしたものを返す"""
    terminal = root
    while (child := plan_instance_repo.get_override_child(db, instance_id=terminal.id)) is not None:
        terminal = child
    terminal_found = plan_instance_repo.get_with_void(db, id=terminal.id)
    assert terminal_found is not None
    if terminal is root or terminal_found[1] is not None:
        return None
    plan_instance_void_repo.void_many(db, instance_ids=[terminal.id], reason=VoidReason.REVERTED, note=note)
    plan_cache_repo.purge(db, instance_ids=[terminal.id])
    return terminal


def _restored_instance(db: Session, template: PlanTemplate, root: PlanInstance) -> PlanInstance | None:
    """作り直した後の、起点と同じ発生枠の有効なルール生成のインスタンス（今より後のものから探す）"""
    root_slot = _slot(root.occurrence_date, root.source_sheet_event_id)
    return next(
        (
            i
            for i in plan_instance_repo.get_live_in_window(
                db, template_id=template.id, lower=_now(), upper=FAR_FUTURE, generated_only=True
            )
            if _slot(i.occurrence_date, i.source_sheet_event_id) == root_slot
        ),
        None,
    )


def _instance_response_or_none(db: Session, instance: PlanInstance | None) -> PlanInstanceResponse | None:
    if instance is None:
        return None
    row = plan_instance_repo.get_with_void(db, id=instance.id)
    assert row is not None
    return _to_instance_response(*row)


def add_one_off_instance(
    db: Session, template_id: int, one_off_in: PlanOneOffCreate, actor_staff_id: int | None = None
) -> PlanInstanceResponse:
    """
    発生枠に紐づかない単発のインスタンスを追加します（例: 臨時の作業）。
    ルールからの再生成の対象外のため、テンプレートを変更しても維持されます（テンプレート削除時は無効化）。
    """
    template = _get_locked_template(db, template_id)

    start = one_off_in.scheduled_start
    end = one_off_in.scheduled_end or start + timedelta(minutes=template.duration_minutes)
    _reject_past_start(start)
    slot_events.mark_person_change(db, actor_staff_id)
    instance = plan_instance_repo.add_one_off(
        db,
        template=template,
        start=start,
        end=end,
        occurrence_date=start.astimezone(ZoneInfo(template.timezone)).date(),
        ui_metadata=ui_metadata_to_dict(one_off_in.ui_metadata),
    )
    plan_cache_repo.add_for_instances(db, template=template, instances=[instance])

    _commit(db, "One-off instance could not be created due to a conflicting record.")
    db.refresh(instance)
    return _to_instance_response(instance, None)


def _cancelled_restore_block(db: Session, instance: PlanInstance, now: datetime) -> str | None:
    """取り消された予定を元に戻せない理由（戻せるなら None）"""
    if instance.scheduled_start < now:
        return "past"
    root = _chain_root(db, instance)
    if root.origin == InstanceOrigin.GENERATED and plan_slot_release_repo.exists(db, root_instance_id=root.id):
        return "reverted"  # ルールの管理下に戻され、ルールの予定が既にある
    return None


def get_cancelled_instances(
    db: Session, client_code: str, start: datetime, end: datetime
) -> list[CancelledInstanceResponse]:
    """顧客に属する全業務の、取り消された予定（範囲内、開始時刻順）。元に戻せるかどうかも返します。"""
    _validate_range(start, end)
    code = _get_client_code(db, client_code)
    now = _now()

    results = []
    for instance, void, template, biz_code, biz_name in plan_instance_repo.get_cancelled_for_client(
        db, client_code=code, start=start, end=end
    ):
        block = _cancelled_restore_block(db, instance, now)
        if block == "reverted":
            continue  # 取消はすでに取り消されている（ルールの予定として復活済み）
        results.append(CancelledInstanceResponse(
            instance_id=instance.id,
            template_id=template.id,
            title=template.name,
            biz_master_id=template.biz_master_id,
            biz_code=biz_code,
            biz_name=biz_name,
            origin=instance.origin,
            scheduled_start=instance.scheduled_start,
            scheduled_end=instance.scheduled_end,
            ui_metadata=merge_ui_metadata(template.ui_metadata, instance.ui_metadata),
            note=void.note,
            cancelled_at=void.voided_at,
            restorable=block is None,
            not_restorable_reason=block,
        ))
    return results


def restore_cancelled_instance(db: Session, instance_id: int, actor_staff_id: int | None = None) -> PlanInstanceResponse:
    """
    取り消した予定を元に戻します（取消の直前と同じ日時・表示で復元）。
    インスタンスは不変のため、取り消された予定を置き換える新しいインスタンスを追記して復元します
    （取消の記録は履歴として残り、復元した予定は「手動変更」として扱われます）。
    """
    found = plan_instance_repo.get_with_void(db, id=instance_id)
    if not found:
        raise NotFoundError(f"PlanInstance(id={instance_id}) not found.")
    instance = found[0]
    template = plan_template_repo.get_locked(db, id=instance.template_id)
    if not template:
        raise BadRequestError(f"PlanTemplate(id={instance.template_id}) of this instance has been deleted.")

    # ロック取得後に状態を確認する（ロック待ちの間に復元・変更されている可能性があるため）
    found = plan_instance_repo.get_with_void(db, id=instance_id)
    assert found is not None
    void = found[1]
    if void is None or void.reason != VoidReason.MANUAL:
        raise BadRequestError(f"PlanInstance(id={instance_id}) has not been cancelled.")
    if plan_instance_repo.get_override_child(db, instance_id=instance.id) is not None:
        raise DuplicatedRecordError(
            f"PlanInstance(id={instance_id}) has already been rescheduled or restored.",
            error_code=ErrorCode.INSTANCE_ALREADY_VOIDED,
        )
    block = _cancelled_restore_block(db, instance, _now())
    if block == "past":
        raise BadRequestError("Cannot restore a cancelled schedule whose time has already passed.")
    if block == "reverted":
        raise DuplicatedRecordError(
            "This cancellation has already been undone (the slot is back under the rule).",
            error_code=ErrorCode.INSTANCE_ALREADY_VOIDED,
        )

    slot_events.mark_person_change(db, actor_staff_id)
    restored = plan_instance_repo.add_override(
        db, template=template, original=instance, start=instance.scheduled_start, end=instance.scheduled_end,
        ui_metadata=instance.ui_metadata,
    )
    plan_cache_repo.add_for_instances(db, template=template, instances=[restored])
    _commit(db, f"PlanInstance(id={instance_id}) was changed concurrently. Please retry.")
    db.refresh(restored)
    return _to_instance_response(restored, None)


def get_occurrences(db: Session, biz_id: int, start: datetime, end: datetime) -> list[PlanOccurrenceCache]:
    """
    範囲内の予定をシャドウテーブルから取得します。
    範囲が未生成区間にかかる場合のみ、先にその区間を生成します（生成済みなら読み取りのみ）。
    """
    _validate_range(start, end)
    _get_biz(db, biz_id)
    _materialize_templates(db, plan_template_repo.get_by_biz(db, biz_master_id=biz_id), end)
    return plan_cache_repo.query_range(db, biz_master_id=biz_id, start=start, end=end)


def _validate_range(start: datetime, end: datetime) -> None:
    if start.tzinfo is None or end.tzinfo is None:
        raise BadRequestError("start and end must include a timezone offset (e.g. 2026-11-01T00:00:00+09:00).")
    if end <= start:
        raise BadRequestError("end must be after start.")
    if end - start > MAX_QUERY_SPAN:
        raise BadRequestError(f"Range must not exceed {MAX_QUERY_SPAN.days} days.")


def _materialize_templates(db: Session, templates: list[PlanTemplate], end: datetime) -> None:
    """範囲が未生成区間にかかるテンプレートだけ、先にその区間を生成します（生成済みなら何もしません）。"""
    now = _now()
    until = min(end, now + MAX_LOOKAHEAD)
    materialized = False
    for template in templates:
        if (template.materialized_until or now) < until:
            locked = _get_locked_template(db, template.id)
            _ensure_materialized(db, locked, until)
            materialized = True
    if materialized:
        _commit(db, "Occurrences could not be generated due to a conflicting record.")


def materialize_all_templates(db: Session, end: datetime) -> None:
    """全顧客の有効なテンプレートについて、end までの未生成区間を生成します（日次ダッシュボードなど顧客横断の表示用）。"""
    _materialize_templates(db, plan_template_repo.get_all(db), end)


def _get_client_code(db: Session, client_code: str) -> str:
    code = client_code.strip().upper()
    if not client_repo.get_by_code(db, code=code):
        raise NotFoundError(f"Client with code '{code}' does not exist.")
    return code


def get_client_occurrences(db: Session, client_code: str, start: datetime, end: datetime) -> list[ClientOccurrenceResponse]:
    """顧客に属する全業務の予定を、範囲内で開始時刻順に取得します。"""
    _validate_range(start, end)
    code = _get_client_code(db, client_code)
    _materialize_templates(db, plan_template_repo.get_by_client(db, client_code=code), end)

    now = _now()
    results = []
    for cache, biz_code, biz_name in plan_cache_repo.query_range_for_client(db, client_code=code, start=start, end=end):
        revertable = False
        if cache.origin == InstanceOrigin.OVERRIDE:
            instance = plan_instance_repo.get(db, id=cache.instance_id)
            revertable = instance is not None and _is_revertable(db, instance, now)
        results.append(ClientOccurrenceResponse(
            **PlanOccurrenceResponse.model_validate(cache).model_dump(),
            biz_code=biz_code, biz_name=biz_name, revertable=revertable,
        ))
    return results


def get_client_calendar(
    db: Session, client_code: str, month: str, tz_name: str, holiday_calendar: str | None = None
) -> ClientCalendarResponse:
    """
    顧客に属する全業務の予定を、月カレンダーの形（その月の全日 + 日ごとの予定）で返します。
    日付は指定タイムゾーンでの開始日で振り分けます（前月から続く予定は月初の日に表示）。
    holiday_calendar（営業日カレンダーのコード）を指定すると、各日に休日名と営業日かどうかを付けます。
    """
    calendar = None
    if holiday_calendar:
        calendar = business_calendar_repo.get_by_code(db, code=holiday_calendar)
        if not calendar:
            raise NotFoundError(f"BusinessCalendar with code '{holiday_calendar}' not found.")
    try:
        tz = ZoneInfo(validate_timezone_name(tz_name))
        year, mon = (int(x) for x in month.split("-"))
        first_day = date(year, mon, 1)
    except ValueError as e:
        raise BadRequestError(str(e)) from e
    next_first = date(year + mon // 12, mon % 12 + 1, 1)
    start = datetime.combine(first_day, datetime.min.time(), tzinfo=tz)
    end = datetime.combine(next_first, datetime.min.time(), tzinfo=tz)

    occurrences = get_client_occurrences(db, client_code, start, end)

    by_day: dict[date, list[ClientOccurrenceResponse]] = defaultdict(list)
    for occ in occurrences:
        by_day[max(occ.starts_at.astimezone(tz).date(), first_day)].append(occ)

    holidays: dict[date, str | None] = {}
    if calendar is not None:
        holidays = {
            h.holiday_date: h.name
            for h in business_calendar_repo.get_holidays(
                db, calendar_id=calendar.id, start=first_day, end=next_first - timedelta(days=1)
            )
        }

    days = []
    d = first_day
    while d < next_first:
        days.append(CalendarDay(
            date=d,
            weekday=d.isoweekday(),
            holiday_name=(holidays.get(d) or "休日") if d in holidays else None,
            is_business_day=(
                None if calendar is None
                else d.isoweekday() not in calendar.weekend_days and d not in holidays
            ),
            occurrences=by_day.get(d, []),
        ))
        d += timedelta(days=1)

    code = client_code.strip().upper()
    templates = [
        CalendarTemplateInfo(
            template_id=t.id, name=t.name, biz_master_id=t.biz_master_id, biz_code=biz_code, biz_name=biz_name,
            template_type=t.template_type, timezone=t.timezone, start_time=t.start_time,
            duration_minutes=t.duration_minutes, rrule=t.rrule, reference_event_marker=t.reference_event_marker,
            offset_days=t.offset_days, offset_day_unit=t.offset_day_unit, holiday_policy=t.holiday_policy,
            ui_metadata=t.ui_metadata,
        )
        for t, biz_code, biz_name in plan_template_repo.get_by_client_with_biz(db, client_code=code)
    ]

    return ClientCalendarResponse(
        client_code=code, month=f"{year:04d}-{mon:02d}", timezone=tz_name,
        holiday_calendar=calendar.code if calendar is not None else None,
        start=start, end=end, total=len(occurrences), templates=templates, days=days,
    )


def rebuild_cache(db: Session, template_id: int) -> int:
    """シャドウテーブルを plan_instance - plan_instance_void から作り直します。再構築した件数を返します。"""
    template = get_template(db, template_id)
    live = plan_instance_repo.get_live_by_template(db, template_id=template.id)

    plan_cache_repo.purge_template(db, template_id=template.id)
    plan_cache_repo.add_for_instances(db, template=template, instances=live)
    db.commit()
    return len(live)
