"""
ワーカーの健康のまとめ（並行稼働）。日本時間の 1 日ごとの数字と、期間の合計（承認の記録）。

承認に数えるもの:
- エラー: ワーカーが仕事の失敗を受け止めた回数（worker_error の行）
- きれいに止まらなかった実行: 落ちた（crash）か、止まった印のないまま心拍が途絶えた（強制終了・ハング）。終わった時刻の日に数える
- 途切れ: 心拍と心拍の間が 5 分を超えたところ（実行をまたいでも。今も続いていれば「今まで」）。重なるすべての日に出す。
  予定した停止（入れ替えなど）も途切れに出る。理由は承認の記録に QA が書く

数えないもの: 業務の結果（再送の後も届かなかった通知・5 回で諦めた出来事）。「処理済みの問題」として別に並べる
（それぞれの画面がある。宛先の打ち間違いでワーカーの承認が落ちないように）。

ワーカーが一度も動いていない時間（最初の心拍より前）は、途切れにも稼働率にも入れない。
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from itertools import pairwise

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.domain_events.models import DomainEvent
from src.domain_notification.dispatcher import MAX_EVENT_ATTEMPTS
from src.domain_notification.models import Notification, NotificationStatus
from src.domain_staff.scope import ALL_CLIENTS, ClientScope
from src.worker_health.models import StopReason, WorkerBeat, WorkerError, WorkerRun
from src.core.clock import LOCAL_TIMEZONE, local_date_of, utc_now as _now

GAP_LIMIT = timedelta(minutes=5)  # これを超える心拍の間は途切れ（ダッシュボードの帯も同じ）
FAILED = (NotificationStatus.FAILED, NotificationStatus.PARTLY_FAILED)


@dataclass(frozen=True)
class Gap:
    start: datetime  # 途切れる前の最後の心拍
    end: datetime  # 次の心拍（続いていれば今）
    ongoing: bool

    @property
    def minutes(self) -> float:
        return (self.end - self.start).total_seconds() / 60


@dataclass(frozen=True)
class UncleanEnd:
    run_id: int
    host: str
    ended_at: datetime  # crash なら止まった日時、それ以外は最後の心拍
    reason: str  # "crash" / "no_stop"（止まった印のないまま途絶えた）


@dataclass
class DaySummary:
    day: date
    start: datetime  # その日の 0 時（日本時間）
    end: datetime  # 次の日の 0 時か、今
    passes: int = 0
    slowest_pass_ms: int | None = None
    longest_gap_minutes: float | None = None
    uptime_percent: float | None = None  # 動いていた割合（最初の心拍より前は入れない。数えようがなければ None）
    gaps: list[Gap] | None = None
    errors_by_job: dict[str, int] | None = None
    unclean_ends: list[UncleanEnd] | None = None
    restarts: int = 0  # その日に起動した回数
    failed_notifications: int = 0  # 処理済みの問題: 再送の後も届かなかった通知（数えない）
    given_up_events: int = 0  # 処理済みの問題: 5 回で諦めた出来事（数えない）

    @property
    def errors(self) -> int:
        return sum((self.errors_by_job or {}).values())

    @property
    def problems(self) -> int:
        """承認に数える問題の数（エラー + きれいに止まらなかった実行 + 途切れ）"""
        return self.errors + len(self.unclean_ends or []) + len(self.gaps or [])


@dataclass
class Status:
    state: str  # ok / stale（心拍が 5 分より古い）/ stopped（止まった印がある）/ never（一度も動いていない）
    last_beat_at: datetime | None
    seconds_since_beat: float | None
    run_id: int | None
    host: str | None
    started_at: datetime | None


@dataclass
class PeriodSummary:
    first: date
    last: date
    days: list[DaySummary]
    status: Status
    uptime_percent: float | None = None

    @property
    def gaps(self) -> list[Gap]:
        seen: dict[datetime, Gap] = {}
        for d in self.days:
            for g in d.gaps or []:
                seen.setdefault(g.start, g)
        return sorted(seen.values(), key=lambda g: g.start)

    @property
    def problems(self) -> int:
        return sum(d.errors + len(d.unclean_ends or []) for d in self.days) + len(self.gaps)


def day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time(0), LOCAL_TIMEZONE)
    return start, start + timedelta(days=1)


def current_status(db: Session, now: datetime | None = None) -> Status:
    now = now or _now()
    run = db.scalars(select(WorkerRun).order_by(WorkerRun.last_beat_at.desc(), WorkerRun.id.desc()).limit(1)).first()
    if run is None:
        return Status("never", None, None, None, None, None)
    since = (now - run.last_beat_at).total_seconds()
    if run.stopped_at is not None:
        state = "stopped"
    elif now - run.last_beat_at > GAP_LIMIT:
        state = "stale"
    else:
        state = "ok"
    return Status(state, run.last_beat_at, max(0.0, since), run.id, run.host, run.started_at)


def _unclean_ends(db: Session, start: datetime, end: datetime, now: datetime) -> list[UncleanEnd]:
    latest_id = db.scalar(select(WorkerRun.id).order_by(WorkerRun.last_beat_at.desc(), WorkerRun.id.desc()).limit(1))
    runs = db.scalars(
        select(WorkerRun).where(
            ((WorkerRun.stop_reason == StopReason.CRASH) & (WorkerRun.stopped_at >= start) & (WorkerRun.stopped_at < end))
            | (WorkerRun.stopped_at.is_(None) & (WorkerRun.last_beat_at >= start) & (WorkerRun.last_beat_at < end))
        ).order_by(WorkerRun.id)
    )
    ends: list[UncleanEnd] = []
    for run in runs:
        if run.stop_reason == StopReason.CRASH and run.stopped_at is not None:
            ends.append(UncleanEnd(run.id, run.host, run.stopped_at, "crash"))
        elif run.stopped_at is None and (run.id != latest_id or now - run.last_beat_at > GAP_LIMIT):
            # 新しい実行が始まった（前のは黙って消えた）か、いちばん新しい実行の心拍が途絶えている
            ends.append(UncleanEnd(run.id, run.host, run.last_beat_at, "no_stop"))
    return ends


def summarize(db: Session, first: date, last: date, *, scope: ClientScope = ALL_CLIENTS, now: datetime | None = None) -> PeriodSummary:
    """first 〜 last（日本時間の日、両端を含む）。今日より後の日は返さない。今日は今まで"""
    now = now or _now()
    today = local_date_of(now)
    last = min(last, today)
    status = current_status(db, now)
    if last < first:
        return PeriodSummary(first, last, [], status)
    period_start, _ = day_bounds(first)
    period_end = min(day_bounds(last)[1], now)

    beats = list(db.execute(
        select(WorkerBeat.at, WorkerBeat.duration_ms).where(WorkerBeat.at >= period_start, WorkerBeat.at < period_end).order_by(WorkerBeat.at)
    ))
    before = db.scalar(select(func.max(WorkerBeat.at)).where(WorkerBeat.at < period_start))
    after = db.scalar(select(func.min(WorkerBeat.at)).where(WorkerBeat.at >= period_end))
    first_ever = db.scalar(select(func.min(WorkerBeat.at)))

    # 心拍の並び（期間の前の 1 つ + 期間の中 + 期間の後の 1 つ、無ければ今）。間が 5 分を超えたら途切れ
    points = ([before] if before else []) + [b.at for b in beats]
    ongoing_end = after is None
    if points:
        points.append(after if after is not None else now)
    intervals = list(pairwise(points))
    gaps = [Gap(a, b, ongoing=ongoing_end and i == len(intervals) - 1) for i, (a, b) in enumerate(intervals) if b - a > GAP_LIMIT]

    errors = db.execute(
        select(WorkerError.occurred_at, WorkerError.job).where(WorkerError.occurred_at >= period_start, WorkerError.occurred_at < period_end)
    ).all()
    restarts = list(db.scalars(select(WorkerRun.started_at).where(WorkerRun.started_at >= period_start, WorkerRun.started_at < period_end)))
    unclean = _unclean_ends(db, period_start, period_end, now)
    failed = list(db.scalars(
        select(Notification.completed_at).where(
            Notification.status.in_(FAILED), Notification.rule_id.is_not(None), Notification.completed_at >= period_start,
            Notification.completed_at < period_end, scope.clause(Notification.client_code),
        )
    ))
    given_up = list(db.scalars(
        select(DomainEvent.recorded_at).where(
            DomainEvent.processed_at.is_(None), DomainEvent.attempts >= MAX_EVENT_ATTEMPTS, DomainEvent.recorded_at >= period_start,
            DomainEvent.recorded_at < period_end, scope.clause(DomainEvent.client_code),
        )
    ))

    days: list[DaySummary] = []
    current = first
    while current <= last:
        start, end = day_bounds(current)
        end = min(end, now)
        d = DaySummary(day=current, start=start, end=end, gaps=[], errors_by_job={}, unclean_ends=[])
        in_day = [b for b in beats if start <= b.at < end]
        d.passes = len(in_day)
        durations = [b.duration_ms for b in in_day if b.duration_ms is not None]
        d.slowest_pass_ms = max(durations) if durations else None
        overlapping = [(a, b) for a, b in intervals if a < end and b > start]
        d.longest_gap_minutes = max(((b - a).total_seconds() / 60 for a, b in overlapping), default=None)
        assert d.gaps is not None and d.errors_by_job is not None and d.unclean_ends is not None
        d.gaps.extend(g for g in gaps if g.start < end and g.end > start)
        counts: dict[str, int] = defaultdict(int)
        for at, job in errors:
            if start <= at < end:
                counts[job] += 1
        d.errors_by_job.update(sorted(counts.items()))
        d.unclean_ends.extend(u for u in unclean if start <= u.ended_at < end)
        d.restarts = sum(1 for at in restarts if start <= at < end)
        d.failed_notifications = sum(1 for at in failed if at is not None and start <= at < end)
        d.given_up_events = sum(1 for at in given_up if start <= at < end)
        # 動いていた割合: 最初の心拍より前は数えない
        counted_from = max(start, first_ever) if first_ever else None
        if counted_from is not None and end > counted_from:
            down = sum(((min(g.end, end) - max(g.start, counted_from)).total_seconds() for g in d.gaps if min(g.end, end) > max(g.start, counted_from)), 0.0)
            d.uptime_percent = round(100 * (1 - down / (end - counted_from).total_seconds()), 2)
        days.append(d)
        current += timedelta(days=1)
    period = PeriodSummary(first, last, days, status)
    counted_from = max(period_start, first_ever) if first_ever else None
    if counted_from is not None and period_end > counted_from:
        down = sum(((min(g.end, period_end) - max(g.start, counted_from)).total_seconds()
                    for g in gaps if min(g.end, period_end) > max(g.start, counted_from)), 0.0)
        period.uptime_percent = round(100 * (1 - down / (period_end - counted_from).total_seconds()), 2)
    return period
