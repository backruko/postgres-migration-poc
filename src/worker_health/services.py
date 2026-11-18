"""ワーカーの健康（並行稼働 P.4b）: まとめを API の形に、エラーの一覧"""
from collections import defaultdict
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.core.exceptions import BadRequestError
from src.domain_staff.scope import ALL_CLIENTS, ClientScope
from src.worker_health.models import WorkerError, WorkerRun
from src.worker_health.schemas import (
    WorkerErrorItem, WorkerErrorListResponse, WorkerGap, WorkerHealthDay, WorkerHealthState, WorkerHealthSummaryResponse,
    WorkerHealthTotal, WorkerStatus, WorkerUncleanEnd,
)
from src.worker_health.summary import Gap, Status, current_status, summarize
from src.core.clock import LOCAL_TIMEZONE, local_date_of, utc_now as _now

DEFAULT_DAYS = 14
MAX_DAYS = 62


def _status(s: Status) -> WorkerStatus:
    return WorkerStatus(state=s.state, ok=s.state == "ok", last_beat_at=s.last_beat_at, seconds_since_beat=s.seconds_since_beat,
                        run_id=s.run_id, host=s.host, started_at=s.started_at)


def public_health(db: Session, now: datetime | None = None) -> WorkerHealthState:
    """外の監視用（ログインなし）: 状態と最後の心拍だけ（マシンの名前などは出さない）"""
    s = current_status(db, now)
    return WorkerHealthState(state=s.state, ok=s.state == "ok", last_beat_at=s.last_beat_at, seconds_since_beat=s.seconds_since_beat)


def _gap(g: Gap) -> WorkerGap:
    return WorkerGap(start=g.start, end=g.end, minutes=round(g.minutes, 1), ongoing=g.ongoing)


def _period(first: date | None, last: date | None, now: datetime) -> tuple[date, date]:
    today = local_date_of(now)
    last = last or today
    first = first or last - timedelta(days=DEFAULT_DAYS - 1)
    if first > last:
        raise BadRequestError("'from' must not be later than 'to'.")
    if (last - first).days + 1 > MAX_DAYS:
        raise BadRequestError(f"The period can be at most {MAX_DAYS} days.")
    return first, last


def get_summary(db: Session, *, first: date | None = None, last: date | None = None, scope: ClientScope = ALL_CLIENTS,
                now: datetime | None = None) -> WorkerHealthSummaryResponse:
    now = now or _now()
    first, last = _period(first, last, now)
    period = summarize(db, first, last, scope=scope, now=now)
    days = [
        WorkerHealthDay(
            day=d.day, start=d.start, end=d.end, ok=d.problems == 0, problems=d.problems, passes=d.passes,
            uptime_percent=d.uptime_percent,
            longest_gap_minutes=None if d.longest_gap_minutes is None else round(d.longest_gap_minutes, 1),
            slowest_pass_ms=d.slowest_pass_ms, errors=d.errors, errors_by_job=d.errors_by_job or {},
            gaps=[_gap(g) for g in d.gaps or []],
            unclean_ends=[WorkerUncleanEnd(run_id=u.run_id, host=u.host, ended_at=u.ended_at, reason=u.reason) for u in d.unclean_ends or []],
            restarts=d.restarts, failed_notifications=d.failed_notifications, given_up_events=d.given_up_events,
        )
        for d in period.days
    ]
    by_job: dict[str, int] = defaultdict(int)
    for d in period.days:
        for job, n in (d.errors_by_job or {}).items():
            by_job[job] += n
    gaps = period.gaps
    longest = [d.longest_gap_minutes for d in period.days if d.longest_gap_minutes is not None]
    total = WorkerHealthTotal(
        ok=period.problems == 0, problems=period.problems, days=len(days), passes=sum(d.passes for d in period.days),
        uptime_percent=period.uptime_percent, errors=sum(by_job.values()), errors_by_job=dict(sorted(by_job.items())),
        gaps=len(gaps), longest_gap_minutes=round(max(longest), 1) if longest else None,
        unclean_ends=sum(len(d.unclean_ends or []) for d in period.days), restarts=sum(d.restarts for d in period.days),
        failed_notifications=sum(d.failed_notifications for d in period.days),
        given_up_events=sum(d.given_up_events for d in period.days),
    )
    return WorkerHealthSummaryResponse(period_from=first, period_to=last, status=_status(period.status), total=total, days=days)


def list_errors(db: Session, *, first: date | None = None, last: date | None = None, job: str | None = None, limit: int = 200,
                now: datetime | None = None) -> WorkerErrorListResponse:
    """エラーの一覧（新しい順）。期間は日本時間の日（両端を含む）"""
    now = now or _now()
    first, last = _period(first, last, now)
    start = datetime.combine(first, time(0), LOCAL_TIMEZONE)
    end = datetime.combine(last + timedelta(days=1), time(0), LOCAL_TIMEZONE)
    where = [WorkerError.occurred_at >= start, WorkerError.occurred_at < end]
    if job:
        where.append(WorkerError.job == job)
    total = db.scalar(select(func.count(WorkerError.id)).where(*where)) or 0
    rows = db.execute(
        select(WorkerError, WorkerRun.host).join(WorkerRun, WorkerRun.id == WorkerError.run_id)
        .where(*where).order_by(WorkerError.occurred_at.desc(), WorkerError.id.desc()).limit(limit)
    ).all()
    items = [
        WorkerErrorItem(id=e.id, run_id=e.run_id, host=host, occurred_at=e.occurred_at, job=e.job, error_type=e.error_type,
                        message=e.message, traceback=e.traceback)
        for e, host in rows
    ]
    return WorkerErrorListResponse(items=items, total=total)
