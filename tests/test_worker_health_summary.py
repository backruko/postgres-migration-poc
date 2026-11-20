# tests/test_worker_health_summary.py
"""
ワーカーの健康のまとめ（src/worker_health/summary.py・services.py・router.py。並行稼働）のテスト。
他のテストの行と混ざらないよう、遠い日付（2031 年）で組み立てる。

筋書き（日本時間）: D = 2031-03-10、今 = D+1 12:00
- 実行 1: D 00:00〜09:59 に毎分の心拍（1 回だけ 2,500 ミリ秒）。止まった印のないまま途絶える（強制終了）
- 実行 2: D 10:20 に起動、D+1 11:59 まで毎分。D 15:00〜15:07 にハング（心拍の間 7 分）
- エラー: D に release が 2 回、D+1 に dispatch が 1 回
期待: D は 途切れ 2（21 分・7 分）+ きれいに止まらなかった 1 + エラー 2 = 問題 5、D+1 は エラー 1
"""
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import insert

from src.core.clock import utc_now
from src.domain_events.models import DomainEvent
from src.domain_notification.models import Notification, NotificationRule, NotificationStatus
from src.domain_staff.scope import ClientScope
from src.infrastructure.database import SessionLocal, engine
from src.worker_health import services
from src.worker_health.models import StopReason, WorkerBeat, WorkerError, WorkerRun
from src.worker_health.summary import current_status, summarize

JST = ZoneInfo("Asia/Tokyo")
D = date(2031, 3, 10)
MIN = timedelta(minutes=1)

# 最後の心拍から何分たったか（止まった判定）を、同じ「いま」で確かめるため
pytestmark = pytest.mark.usefixtures("frozen_clock")




def at(day: date, hh: int, mm: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=JST)


NOW = at(D + timedelta(days=1), 12, 0)


@pytest.fixture
def db():
    connection = engine.connect()
    outer = connection.begin()
    session = SessionLocal(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        connection.close()


def run(db, start: datetime, last: datetime, host="worker-1", **kw) -> WorkerRun:
    r = WorkerRun(host=host, pid=1, started_at=start, last_beat_at=last, passes=0, **kw)
    db.add(r)
    db.flush()
    return r


def beats(db, r: WorkerRun, first: datetime, last: datetime, *, skip: tuple[datetime, datetime] | None = None, slow: datetime | None = None):
    rows, t = [], first
    while t <= last:
        if not (skip and skip[0] < t < skip[1]):
            rows.append({"run_id": r.id, "at": t, "duration_ms": 2500 if t == slow else 100})
        t += MIN
    db.execute(insert(WorkerBeat), rows)


def error(db, r: WorkerRun, when: datetime, job: str):
    db.add(WorkerError(run_id=r.id, occurred_at=when, job=job, error_type="ValueError", message=f"{job} broke", traceback="tb"))
    db.flush()


@pytest.fixture
def story(db):
    r1 = run(db, at(D, 0), at(D, 9, 59))
    beats(db, r1, at(D, 0), at(D, 9, 59), slow=at(D, 3, 0))
    r2 = run(db, at(D, 10, 20), at(D + timedelta(days=1), 11, 59))
    beats(db, r2, at(D, 10, 20), at(D + timedelta(days=1), 11, 59), skip=(at(D, 15, 0), at(D, 15, 7)))
    error(db, r2, at(D, 11), "release")
    error(db, r2, at(D, 11, 1), "release")
    error(db, r2, at(D + timedelta(days=1), 8), "dispatch")
    return r1, r2


def test_days_count_passes_gaps_unclean_ends_and_errors(db, story):
    r1, r2 = story
    period = summarize(db, D, D + timedelta(days=5), now=NOW)  # 今日より後の日は返さない
    day, today = period.days
    assert (day.day, today.day) == (D, D + timedelta(days=1))
    assert day.passes == 600 + 820 - 6 and day.slowest_pass_ms == 2500
    assert [(g.start, round(g.minutes), g.ongoing) for g in day.gaps or []] == [(at(D, 9, 59), 21, False), (at(D, 15, 0), 7, False)]
    assert day.longest_gap_minutes == 21
    assert [(u.run_id, u.ended_at, u.reason) for u in day.unclean_ends or []] == [(r1.id, at(D, 9, 59), "no_stop")]
    assert day.errors_by_job == {"release": 2} and day.restarts == 2 and day.problems == 5
    assert day.uptime_percent == round(100 * (1 - 28 / 1440), 2)
    # 今日は今まで（12:00）。問題はエラー 1 だけ
    assert today.end == NOW and today.passes == 720 and today.gaps == [] and today.unclean_ends == []
    assert today.errors_by_job == {"dispatch": 1} and today.problems == 1 and today.uptime_percent == 100.0
    assert period.problems == 6 and len(period.gaps) == 2
    assert period.uptime_percent == round(100 * (1 - 28 / 2160), 2)
    assert period.status.state == "ok" and period.status.run_id == r2.id


def test_a_silent_worker_is_stale_and_its_gap_is_ongoing(db, story):
    _, r2 = story
    later = NOW + timedelta(minutes=30)
    status = current_status(db, later)
    assert (status.state, status.run_id, round(status.seconds_since_beat or 0)) == ("stale", r2.id, 31 * 60)
    today = summarize(db, D + timedelta(days=1), D + timedelta(days=1), now=later).days[0]
    assert [(g.start, g.ongoing) for g in today.gaps or []] == [(at(D + timedelta(days=1), 11, 59), True)]
    assert [(u.run_id, u.reason) for u in today.unclean_ends or []] == [(r2.id, "no_stop")]  # 今の実行でも、途絶えていれば数える


def test_a_crash_counts_and_a_clean_stop_does_not_but_the_gap_until_the_restart_does(db):
    day = D + timedelta(days=30)
    crashed = run(db, at(day, 0), at(day, 0, 59), stopped_at=at(day, 0, 59), stop_reason=StopReason.CRASH)
    beats(db, crashed, at(day, 0), at(day, 0, 59))
    clean = run(db, at(day, 1, 0), at(day, 1, 59), stopped_at=at(day, 1, 59), stop_reason=StopReason.SIGNAL)
    beats(db, clean, at(day, 1, 0), at(day, 1, 59))
    restarted = run(db, at(day, 2, 30), at(day, 2, 40))
    beats(db, restarted, at(day, 2, 30), at(day, 2, 40))
    now = at(day, 2, 41)
    d = summarize(db, day, day, now=now).days[0]
    assert [(u.run_id, u.reason) for u in d.unclean_ends or []] == [(crashed.id, "crash")]
    assert [round(g.minutes) for g in d.gaps or []] == [31]  # 予定した停止でも、再起動までの 31 分は途切れ
    assert d.restarts == 3 and d.problems == 2
    assert current_status(db, now).state == "ok"
    assert current_status(db, at(day, 2, 41)).run_id == restarted.id


def test_before_the_first_beat_nothing_counts(db):
    day = D + timedelta(days=60)
    r = run(db, at(day, 12), at(day, 12, 10))
    beats(db, r, at(day, 12), at(day, 12, 10))
    d = summarize(db, day - timedelta(days=1), day, now=at(day, 12, 11)).days
    assert (d[0].passes, d[0].uptime_percent, d[0].gaps) == (0, None, [])  # まだ一度も動いていない日
    assert (d[1].passes, d[1].uptime_percent, d[1].problems) == (11, 100.0, 0)


def test_handled_problems_are_listed_but_not_counted_and_follow_the_client_scope(db, story):
    rule = NotificationRule(name="r", event_types=["order.late"], template_code="order.late")
    db.add(rule)
    db.flush()
    for code, status in (("AAAA", NotificationStatus.FAILED), ("BBBB", NotificationStatus.PARTLY_FAILED), ("AAAA", NotificationStatus.SENT)):
        e = DomainEvent(event_type="OrderLate", occurred_at=at(D, 9), client_code=code, payload={}, dedupe_key=f"t:{uuid.uuid4().hex}")
        db.add(e)
        db.flush()
        db.add(Notification(event_id=e.id, rule_id=rule.id, dedupe_key=e.dedupe_key, client_code=code, status=status,
                            subject="s", body="b", completed_at=at(D, 10)))
    gave_up = DomainEvent(event_type="OrderLate", occurred_at=at(D, 9), recorded_at=at(D, 9), client_code="AAAA", payload={},
                          dedupe_key=f"t:{uuid.uuid4().hex}", attempts=5)
    db.add(gave_up)
    db.flush()
    day = summarize(db, D, D, now=NOW).days[0]
    assert (day.failed_notifications, day.given_up_events, day.problems) == (2, 1, 5)
    scoped = summarize(db, D, D, scope=ClientScope(frozenset({"BBBB"})), now=NOW).days[0]
    assert (scoped.failed_notifications, scoped.given_up_events) == (1, 0)


def test_service_shapes_the_period_and_refuses_bad_ranges(db, story):
    summary = services.get_summary(db, first=D, last=D + timedelta(days=1), now=NOW)
    t = summary.total
    assert (t.ok, t.problems, t.days, t.passes, t.errors, t.errors_by_job, t.gaps, t.longest_gap_minutes, t.unclean_ends, t.restarts) == (
        False, 6, 2, 1414 + 720, 3, {"dispatch": 1, "release": 2}, 2, 21.0, 1, 2)
    assert [d.ok for d in summary.days] == [False, False] and summary.status.ok
    assert summary.model_dump(by_alias=True)["from"] == D
    default = services.get_summary(db, now=NOW)  # 省略時は今日までの 14 日
    assert (default.period_from, default.period_to) == (D - timedelta(days=12), D + timedelta(days=1))
    from src.core.exceptions import BadRequestError
    with pytest.raises(BadRequestError):
        services.get_summary(db, first=D, last=D - timedelta(days=1), now=NOW)
    with pytest.raises(BadRequestError):
        services.get_summary(db, first=D - timedelta(days=62), last=D, now=NOW)
    errors = services.list_errors(db, first=D, last=D + timedelta(days=1), now=NOW)
    assert errors.total == 3 and [e.job for e in errors.items] == ["dispatch", "release", "release"]
    assert services.list_errors(db, first=D, last=D, job="release", now=NOW).total == 2




def test_health_endpoint_answers_503_when_not_ok_and_is_mounted_without_login(db):
    """HTTP（ログインなし・200 / 503）は 8001 の確認で。ここでは関数と、ルーターの付け方を見る"""
    from main import app
    from src.domain_staff.auth import require_login
    from src.worker_health.router import worker_health_endpoint

    r = run(db, utc_now() - timedelta(minutes=1), utc_now())
    ok = worker_health_endpoint(db)
    assert ok.status_code == 200 and b'"state":"ok"' in ok.body and b"host" not in ok.body
    r.last_beat_at = utc_now() - timedelta(minutes=6)
    db.flush()
    stale = worker_health_endpoint(db)
    assert stale.status_code == 503 and b'"state":"stale"' in stale.body

    def needs_login(prefix: str) -> bool:
        included = next(x for x in app.routes if getattr(getattr(x, "original_router", None), "prefix", None) == prefix)
        return any(d.dependency is require_login for d in included.include_context.dependencies)  # type: ignore[attr-defined]

    assert not needs_login("/health") and needs_login("/worker-health")


def test_the_docker_healthcheck_url_answers_without_login_or_a_database():
    """deploy/docker-compose.yml の app の healthcheck が叩く URL が、ルートとしてあり、データベースなしで ok を返す
    （無い URL を叩くと app が healthy にならず、worker も起動しない）"""
    import re
    from pathlib import Path

    from main import app
    from src.worker_health.router import app_health_endpoint

    compose = (Path(__file__).resolve().parent.parent / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")
    [path] = re.findall(r"urlopen\('http://127\.0\.0\.1:8000(/[^']*)'\)", compose)
    assert path == "/health/app" and path in app.openapi()["paths"]
    assert app_health_endpoint() == {"status": "ok"}
