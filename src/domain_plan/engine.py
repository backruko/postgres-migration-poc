# src/domain_plan/engine.py
"""
繰り返し展開エンジン（純粋関数のみ。DB / Session には一切触れません）

すべての計算は「ローカル壁時計時刻 + IANAタイムゾーン」で行い、最後にUTCへ変換します。
これにより、DST（夏時間）をまたいでも「毎週月曜 9:00」が 9:00 のまま維持されます。
"""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable
from zoneinfo import ZoneInfo

from dateutil.rrule import rrulestr


@dataclass(frozen=True)
class Occurrence:
    start: datetime  # UTC（aware）
    end: datetime    # UTC（aware）
    occurrence_date: date  # 発生枠の識別子（時刻が変わっても同じ枠を指すローカル日付）
    source_sheet_event_id: int | None = None

    @property
    def key(self) -> tuple[datetime, datetime, int | None]:
        """差分照合（reconcile）用のキー"""
        return (self.start, self.end, self.source_sheet_event_id)


def _build(
    local_start: datetime,
    tz: ZoneInfo,
    duration_minutes: int,
    window_start: datetime,
    window_end: datetime,
    holidays: frozenset[date],
    occurrence_date: date | None = None,
    source_sheet_event_id: int | None = None,
) -> Occurrence | None:
    """ローカル開始時刻（naive）から Occurrence を作り、窓外・休日なら None を返します。"""
    # 休日判定は「実際に作業するローカル日付」で行う
    if local_start.date() in holidays:
        return None
    start = local_start.replace(tzinfo=tz).astimezone(timezone.utc)
    if not (window_start <= start < window_end):
        return None
    end = (local_start + timedelta(minutes=duration_minutes)).replace(tzinfo=tz).astimezone(timezone.utc)
    return Occurrence(
        start=start,
        end=end,
        occurrence_date=occurrence_date or local_start.date(),
        source_sheet_event_id=source_sheet_event_id,
    )


def _local_bounds(window_start: datetime, window_end: datetime, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """UTCの窓をローカルnaiveに変換（DSTの端を取りこぼさないよう前後1日の余裕を持たせる）"""
    lower = window_start.astimezone(tz).replace(tzinfo=None) - timedelta(days=1)
    upper = window_end.astimezone(tz).replace(tzinfo=None) + timedelta(days=1)
    return lower, upper


def expand_rrule(
    *,
    rrule: str,
    anchor_date: date,
    start_time: time,
    tz_name: str,
    duration_minutes: int,
    window_start: datetime,
    window_end: datetime,
    holidays: Iterable[date] = (),
) -> list[Occurrence]:
    tz = ZoneInfo(tz_name)
    holiday_set = frozenset(holidays)
    rule = rrulestr(rrule, dtstart=datetime.combine(anchor_date, start_time))
    lower, upper = _local_bounds(window_start, window_end, tz)

    results: list[Occurrence] = []
    for local_start in rule.between(lower, upper, inc=True):  # type: ignore[union-attr]
        occ = _build(local_start, tz, duration_minutes, window_start, window_end, holiday_set)
        if occ:
            results.append(occ)
    return results


def expand_explicit_dates(
    *,
    dates: Iterable[date],
    start_time: time,
    tz_name: str,
    duration_minutes: int,
    window_start: datetime,
    window_end: datetime,
    holidays: Iterable[date] = (),
) -> list[Occurrence]:
    tz = ZoneInfo(tz_name)
    holiday_set = frozenset(holidays)

    results: list[Occurrence] = []
    for d in sorted(set(dates)):
        occ = _build(datetime.combine(d, start_time), tz, duration_minutes, window_start, window_end, holiday_set)
        if occ:
            results.append(occ)
    return results


def expand_sheet_events(
    *,
    events: Iterable[tuple[int, date]],
    start_time: time,
    offset_minutes: int,
    tz_name: str,
    duration_minutes: int,
    window_start: datetime,
    window_end: datetime,
    holidays: Iterable[date] = (),
) -> list[Occurrence]:
    """
    シート日付からの算出: start = (event_date + start_time) + offset_minutes
    オフセットはローカル壁時計時刻で加算します（例: start_time=09:00, offset=-2880 → 2日前の9:00）。

    :param events: (reference_sheet_event.id, event_date) のリスト
    """
    tz = ZoneInfo(tz_name)
    holiday_set = frozenset(holidays)

    results: list[Occurrence] = []
    for event_id, event_date in sorted(events, key=lambda e: e[1]):
        local_start = datetime.combine(event_date, start_time) + timedelta(minutes=offset_minutes)
        occ = _build(local_start, tz, duration_minutes, window_start, window_end, holiday_set, event_date, event_id)
        if occ:
            results.append(occ)
    return results
