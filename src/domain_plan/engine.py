# src/domain_plan/engine.py
"""
繰り返し展開エンジン（純粋関数のみ。DB / Session には一切触れません）

すべての計算は「ローカル壁時計時刻 + IANAタイムゾーン」で行い、最後にUTCへ変換します。
これにより、DST（夏時間）をまたいでも「毎週月曜 9:00」が 9:00 のまま維持されます。

休日の扱い（holiday policy）:
- AS_IS              : 休日・週末を無視し、暦日どおりに実行（24/7/365 運用）
- SKIP               : 非営業日に当たる回は実行しない（後から振り替えもしない）
- FOLLOWING          : 翌営業日へ繰り下げ（Roll Forward / Postpone）
- PRECEDING          : 前営業日へ繰り上げ（Roll Backward / Advance）
- MODIFIED_FOLLOWING : 翌営業日へ。ただし月をまたぐ場合は前営業日へ（月末処理の標準的な慣行）
- MODIFIED_PRECEDING : 前営業日へ。ただし月をまたぐ場合は翌営業日へ
"""
import enum
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable
from zoneinfo import ZoneInfo

from dateutil.rrule import rrulestr

# 営業日の探索上限（全日が休日の設定ミス等で無限ループしないため）
MAX_BUSINESS_DAY_SEARCH = 366
# 振替で窓の外から入ってくる回を取りこぼさないよう、ルール展開時に前後へ広げる日数
ROLL_EXPANSION_MARGIN_DAYS = 31


class HolidayPolicy(str, enum.Enum):
    AS_IS = "as_is"
    SKIP = "skip"
    FOLLOWING = "following"
    PRECEDING = "preceding"
    MODIFIED_FOLLOWING = "modified_following"
    MODIFIED_PRECEDING = "modified_preceding"


class DayUnit(str, enum.Enum):
    CALENDAR = "calendar"  # 暦日
    BUSINESS = "business"  # 営業日（週末・休日を数えない）


class CalendarError(ValueError):
    """営業日カレンダーの設定では計算できない場合（例: 営業日が1日も無い）"""


@dataclass(frozen=True)
class BusinessCalendar:
    weekend_days: frozenset[int]  # ISO曜日（1=月 ... 7=日）
    holidays: frozenset[date]

    def is_business_day(self, d: date) -> bool:
        return d.isoweekday() not in self.weekend_days and d not in self.holidays

    def _step_to_business_day(self, d: date, step: int) -> date:
        """d から step(+1/-1) 方向に進み、最初の営業日を返します（d 自身は含まない）"""
        for _ in range(MAX_BUSINESS_DAY_SEARCH):
            d += timedelta(days=step)
            if self.is_business_day(d):
                return d
        raise CalendarError(f"No business day found within {MAX_BUSINESS_DAY_SEARCH} days of {d}.")

    def add_business_days(self, d: date, n: int) -> date:
        """d から n 営業日後（負なら前）の日付。d 自身は数えません（例: n=-3 → 3営業日前）。"""
        step = 1 if n > 0 else -1
        for _ in range(abs(n)):
            d = self._step_to_business_day(d, step)
        return d

    def roll(self, d: date, policy: HolidayPolicy) -> date | None:
        """休日ポリシーを適用した実行日。SKIP で非営業日の場合は None。"""
        if policy == HolidayPolicy.AS_IS or self.is_business_day(d):
            return d
        if policy == HolidayPolicy.SKIP:
            return None
        if policy == HolidayPolicy.FOLLOWING:
            return self._step_to_business_day(d, 1)
        if policy == HolidayPolicy.PRECEDING:
            return self._step_to_business_day(d, -1)
        if policy == HolidayPolicy.MODIFIED_FOLLOWING:
            rolled = self._step_to_business_day(d, 1)
            return rolled if rolled.month == d.month else self._step_to_business_day(d, -1)
        # MODIFIED_PRECEDING
        rolled = self._step_to_business_day(d, -1)
        return rolled if rolled.month == d.month else self._step_to_business_day(d, 1)


@dataclass(frozen=True)
class Occurrence:
    start: datetime  # UTC（aware）
    end: datetime    # UTC（aware）
    occurrence_date: date  # 発生枠の識別子（休日の振替や時刻変更があっても同じ枠を指すローカル日付）
    source_sheet_event_id: int | None = None
    rolled: bool = False   # 休日ポリシーで実行日が動いたか

    @property
    def key(self) -> tuple[datetime, datetime, int | None]:
        """差分照合（reconcile）用のキー"""
        return (self.start, self.end, self.source_sheet_event_id)


@dataclass(frozen=True)
class _Context:
    tz: ZoneInfo
    duration_minutes: int
    window_start: datetime
    window_end: datetime
    calendar: BusinessCalendar | None
    holiday_policy: HolidayPolicy

    @property
    def shifts_dates(self) -> bool:
        return self.calendar is not None and self.holiday_policy != HolidayPolicy.AS_IS


def _context(
    tz_name: str,
    duration_minutes: int,
    window_start: datetime,
    window_end: datetime,
    calendar: BusinessCalendar | None,
    holiday_policy: HolidayPolicy,
) -> _Context:
    if holiday_policy != HolidayPolicy.AS_IS and calendar is None:
        raise CalendarError(f"Holiday policy '{holiday_policy.value}' requires a business calendar.")
    return _Context(ZoneInfo(tz_name), duration_minutes, window_start, window_end, calendar, holiday_policy)


def _build(
    ctx: _Context,
    local_start: datetime,
    occurrence_date: date | None = None,
    source_sheet_event_id: int | None = None,
) -> Occurrence | None:
    """ローカル開始時刻（naive）に休日ポリシーを適用して Occurrence を作り、SKIP・窓外なら None を返します。"""
    slot_date = occurrence_date or local_start.date()
    rolled = False
    if ctx.shifts_dates:
        assert ctx.calendar is not None
        # 休日判定は「実際に作業するローカル日付」で行う
        actual = ctx.calendar.roll(local_start.date(), ctx.holiday_policy)
        if actual is None:
            return None
        rolled = actual != local_start.date()
        local_start = datetime.combine(actual, local_start.time())

    start = local_start.replace(tzinfo=ctx.tz).astimezone(timezone.utc)
    if not (ctx.window_start <= start < ctx.window_end):
        return None
    end = (local_start + timedelta(minutes=ctx.duration_minutes)).replace(tzinfo=ctx.tz).astimezone(timezone.utc)
    return Occurrence(
        start=start, end=end, occurrence_date=slot_date, source_sheet_event_id=source_sheet_event_id, rolled=rolled
    )


def _collapse(occurrences: Iterable[Occurrence | None]) -> list[Occurrence]:
    """
    振替で同じ開始時刻に重なった回を1つにまとめます（例: 毎日の予定 + FOLLOWING で土日月 → 月曜1回）。
    振替されていない回（その日本来の回）を優先し、次に発生枠の日付が早いものを残します。
    """
    best: dict[datetime, Occurrence] = {}
    for occ in occurrences:
        if occ is None:
            continue
        current = best.get(occ.start)
        if current is None or (occ.rolled, occ.occurrence_date) < (current.rolled, current.occurrence_date):
            best[occ.start] = occ
    return sorted(best.values(), key=lambda o: (o.start, o.occurrence_date))


@dataclass(frozen=True)
class RuleDate:
    rule_date: date    # ルール上の日付（発生枠の識別子）
    actual_date: date  # 休日ポリシー適用後の日付
    rolled: bool


def expand_rule_dates(
    *,
    rrule: str,
    anchor_date: date,
    period_start: date,
    period_end: date,
    calendar: BusinessCalendar | None = None,
    holiday_policy: HolidayPolicy = HolidayPolicy.AS_IS,
) -> list[RuleDate]:
    """
    ルール（例: 毎月27日、休日なら翌営業日）から基準日の一覧を生成します（シートの生成マーカー用）。
    休日ポリシー適用後の日付が [period_start, period_end] に入るものだけを返し、
    振替で同じ日に重なった場合は、振替されていない日（次に元の日付が早いもの）を残します。
    """
    if holiday_policy != HolidayPolicy.AS_IS and calendar is None:
        raise CalendarError(f"Holiday policy '{holiday_policy.value}' requires a business calendar.")
    shifts = calendar is not None and holiday_policy != HolidayPolicy.AS_IS
    margin = timedelta(days=ROLL_EXPANSION_MARGIN_DAYS if shifts else 0)
    rule = rrulestr(rrule, dtstart=datetime.combine(anchor_date, time(0, 0)))

    best: dict[date, RuleDate] = {}
    seen_rule_dates: set[date] = set()
    lower = datetime.combine(period_start - margin, time(0, 0))
    upper = datetime.combine(period_end + margin, time(23, 59, 59))
    for dt in rule.between(lower, upper, inc=True):  # type: ignore[union-attr]
        rule_date = dt.date()
        if rule_date in seen_rule_dates:  # 1日に複数回のルール（BYHOUR等）は1つの基準日として扱う
            continue
        seen_rule_dates.add(rule_date)

        actual = calendar.roll(rule_date, holiday_policy) if shifts and calendar is not None else rule_date
        if actual is None or not (period_start <= actual <= period_end):
            continue
        candidate = RuleDate(rule_date=rule_date, actual_date=actual, rolled=actual != rule_date)
        current = best.get(actual)
        if current is None or (candidate.rolled, candidate.rule_date) < (current.rolled, current.rule_date):
            best[actual] = candidate
    return sorted(best.values(), key=lambda r: r.actual_date)


def expand_rrule(
    *,
    rrule: str,
    anchor_date: date,
    start_time: time,
    tz_name: str,
    duration_minutes: int,
    window_start: datetime,
    window_end: datetime,
    calendar: BusinessCalendar | None = None,
    holiday_policy: HolidayPolicy = HolidayPolicy.AS_IS,
) -> list[Occurrence]:
    ctx = _context(tz_name, duration_minutes, window_start, window_end, calendar, holiday_policy)
    rule = rrulestr(rrule, dtstart=datetime.combine(anchor_date, start_time))

    # UTCの窓をローカルnaiveに変換（DSTの端、および振替で窓に出入りする回を取りこぼさないよう余裕を持たせる）
    margin = timedelta(days=1 + (ROLL_EXPANSION_MARGIN_DAYS if ctx.shifts_dates else 0))
    lower = window_start.astimezone(ctx.tz).replace(tzinfo=None) - margin
    upper = window_end.astimezone(ctx.tz).replace(tzinfo=None) + margin

    return _collapse(_build(ctx, local_start) for local_start in rule.between(lower, upper, inc=True))  # type: ignore[union-attr]


def expand_explicit_dates(
    *,
    dates: Iterable[date],
    start_time: time,
    tz_name: str,
    duration_minutes: int,
    window_start: datetime,
    window_end: datetime,
    calendar: BusinessCalendar | None = None,
    holiday_policy: HolidayPolicy = HolidayPolicy.AS_IS,
) -> list[Occurrence]:
    ctx = _context(tz_name, duration_minutes, window_start, window_end, calendar, holiday_policy)
    return _collapse(_build(ctx, datetime.combine(d, start_time)) for d in sorted(set(dates)))


def expand_sheet_events(
    *,
    events: Iterable[tuple[int, date]],
    start_time: time,
    offset_minutes: int,
    tz_name: str,
    duration_minutes: int,
    window_start: datetime,
    window_end: datetime,
    offset_days: int = 0,
    offset_day_unit: DayUnit = DayUnit.CALENDAR,
    calendar: BusinessCalendar | None = None,
    holiday_policy: HolidayPolicy = HolidayPolicy.AS_IS,
) -> list[Occurrence]:
    """
    シート日付からの算出:
      1. 基準日 = event_date を offset_days だけずらす（offset_day_unit: 暦日 or 営業日）
      2. 開始 = (基準日 + start_time) + offset_minutes（ローカル壁時計時刻で加算）
      3. 休日ポリシーを適用
    例: offset_days=-3, unit=BUSINESS, start_time=09:00 → 「3営業日前の 9:00」

    :param events: (reference_sheet_event.id, event_date) のリスト
    """
    ctx = _context(tz_name, duration_minutes, window_start, window_end, calendar, holiday_policy)
    if offset_day_unit == DayUnit.BUSINESS and calendar is None:
        raise CalendarError("Business-day offsets require a business calendar.")

    def base_date(event_date: date) -> date:
        if offset_day_unit == DayUnit.BUSINESS:
            assert calendar is not None
            return calendar.add_business_days(event_date, offset_days)
        return event_date + timedelta(days=offset_days)

    return _collapse(
        _build(
            ctx,
            datetime.combine(base_date(event_date), start_time) + timedelta(minutes=offset_minutes),
            event_date,
            event_id,
        )
        for event_id, event_date in sorted(events, key=lambda e: e[1])
    )
