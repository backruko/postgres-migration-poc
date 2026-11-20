# tests/test_worker_health.py
"""
ワーカーの健康の記録（src/worker_health、scripts/worker.py。並行稼働）のテスト。
- 起動で worker_run に 1 行、1 回りごとに心拍と回数、止まると時刻と理由（signal / crash）
- 仕事の失敗は 1 回につき worker_error に 1 行（種類・文面・トレースバック）。ほかの仕事は続く
- DB に書けない間はメモリーにためて、書けたときにまとめて書く（起動の日時・失敗の日時はもとのまま）。ためきれない分は数だけ
- 記録のための呼び出しは、DB が止まっていても例外を出さない
"""
import importlib.util
import sys
import threading
from datetime import datetime, timedelta, UTC
from pathlib import Path

import pytest
from sqlalchemy import select

from src.infrastructure.database import SessionLocal, engine
from src.worker_health.models import StopReason, WorkerError, WorkerRun
from src.worker_health.recorder import MAX_PENDING, HealthRecorder

_spec = importlib.util.spec_from_file_location("worker", Path(__file__).resolve().parent.parent / "scripts" / "worker.py")
assert _spec and _spec.loader
worker = importlib.util.module_from_spec(_spec)
sys.modules["worker"] = worker  # dataclass は sys.modules のモジュールを引く
_spec.loader.exec_module(worker)

T0 = datetime(2026, 11, 17, 2, 0, tzinfo=UTC)


@pytest.fixture
def connection():
    """記録はそれぞれ自分のセッションでコミットする。外側のトランザクションのセーブポイントの中だけにして、終わりに取り消す"""
    conn = engine.connect()
    outer = conn.begin()
    try:
        yield conn
    finally:
        outer.rollback()
        conn.close()


class Clock:
    def __init__(self):
        self.at = T0

    def __call__(self):
        return self.at

    def tick(self, minutes=1):
        self.at += timedelta(minutes=minutes)


class FlakyDb:
    """down の間は接続できない（データベースが止まっている）"""

    def __init__(self, conn):
        self.conn, self.down = conn, False

    def __call__(self):
        if self.down:
            raise ConnectionError("database is down")
        return SessionLocal(bind=self.conn, join_transaction_mode="create_savepoint")


def recorder(conn, clock=None, db=None):
    return HealthRecorder(db or FlakyDb(conn), now=clock or Clock(), host="test-host", pid=4242)


def rows(conn, model):
    with SessionLocal(bind=conn, join_transaction_mode="create_savepoint") as s:
        return list(s.scalars(select(model).order_by(model.id)))


def boom(message="boom"):
    raise ValueError(message)


def test_start_beats_and_a_clean_stop(connection):
    clock = Clock()
    r = recorder(connection, clock)
    r.start()
    run = rows(connection, WorkerRun)[-1]
    assert (run.host, run.pid, run.started_at, run.last_beat_at, run.passes, run.stopped_at) == ("test-host", 4242, T0, T0, 0, None)
    clock.tick()
    r.beat()
    clock.tick()
    r.beat()
    clock.tick(0)
    r.stop(StopReason.SIGNAL)
    run = rows(connection, WorkerRun)[-1]
    assert (run.passes, run.last_beat_at, run.stopped_at, run.stop_reason) == (2, T0 + timedelta(minutes=2), T0 + timedelta(minutes=2), "signal")


def test_each_job_failure_is_recorded_once_and_the_other_jobs_still_run(connection, monkeypatch):
    r = recorder(connection)
    r.start()
    ran: list[str] = []
    monkeypatch.setattr(worker, "run_release", lambda: ran.append("release"))
    monkeypatch.setattr(worker, "run_deadline_watch", lambda lookback: boom("deadline watch broke"))
    monkeypatch.setattr(worker, "run_dispatch", lambda: ran.append("dispatch"))
    monkeypatch.setattr(worker, "run_deliveries", lambda channels: ran.append("delivery"))
    monkeypatch.setattr(worker, "run_extension", lambda: ran.append("extension"))
    state = worker.PassState(extend_at=worker.parse_time("00:00"), lookback=timedelta(hours=24), channels=None)
    worker.run_pass(r, state)
    assert ran == ["extension", "release", "dispatch", "delivery"]  # 締切の監視が失敗しても、後の仕事は動く
    assert state.last_extension is not None
    errors = rows(connection, WorkerError)
    assert len(errors) == 1
    e = errors[0]
    assert (e.run_id, e.job, e.error_type, e.message) == (r.run_id, "deadline_watch", "ValueError", "deadline watch broke")
    assert "boom" in e.traceback and "ValueError: deadline watch broke" in e.traceback
    # 予定の生成が失敗したら、その日のうちに次の回りでまたやる
    monkeypatch.setattr(worker, "run_extension", lambda: boom("extension broke"))
    state.last_extension = None
    worker.run_pass(r, state)
    assert state.last_extension is None and [x.job for x in rows(connection, WorkerError)] == ["deadline_watch", "extension", "deadline_watch"]


def test_the_loop_records_a_crash_and_a_clean_stop(connection):
    r = recorder(connection)
    stop = threading.Event()
    passes = []

    def one_pass():
        passes.append(1)
        if len(passes) == 2:
            stop.set()

    waits = []

    def no_wait():  # 次の分を待たない（実際の待ちは最大 60 秒）
        waits.append(1)
        return 0

    worker.run_loop(r, stop, one_pass, seconds_to_wait=no_wait)
    assert len(waits) == 2  # 毎回の実行のあとに 1 回ずつ待つ
    run = rows(connection, WorkerRun)[-1]
    assert (run.passes, run.stop_reason) == (2, "signal")

    crashing = recorder(connection)
    with pytest.raises(RuntimeError):
        worker.run_loop(crashing, threading.Event(), lambda: (_ for _ in ()).throw(RuntimeError("loop broke")))
    run = rows(connection, WorkerRun)[-1]
    assert (run.id, run.passes, run.stop_reason) == (crashing.run_id, 0, "crash") and run.stopped_at is not None
    last = rows(connection, WorkerError)[-1]
    assert (last.run_id, last.job, last.error_type, last.message) == (crashing.run_id, "worker", "RuntimeError", "loop broke")


def test_while_the_database_is_down_everything_waits_in_memory(connection):
    clock = Clock()
    db = FlakyDb(connection)
    r = recorder(connection, clock, db)
    db.down = True
    r.start()  # 例外を出さない
    clock.tick()
    r.error("release", ValueError("db down"))
    r.beat()
    clock.tick()
    r.beat()
    assert r.run_id is None and r.pending_errors == 1 and rows(connection, WorkerRun) == []
    db.down = False
    clock.tick()
    r.beat()
    run = rows(connection, WorkerRun)[-1]
    assert (run.id, run.started_at, run.passes, run.last_beat_at) == (r.run_id, T0, 3, T0 + timedelta(minutes=3))
    errors = rows(connection, WorkerError)
    assert [(e.job, e.occurred_at) for e in errors] == [("release", T0 + timedelta(minutes=1))]  # 失敗したときの日時
    assert r.pending_errors == 0
    # いったん書けた後に DB が止まっても、回数は失われない
    db.down = True
    r.beat()
    db.down = False
    r.stop(StopReason.SIGNAL)
    assert rows(connection, WorkerRun)[-1].passes == 4  # 止まっている間の 1 回りも、止まる時に書く


def test_errors_beyond_the_memory_limit_are_counted(connection):
    db = FlakyDb(connection)
    r = recorder(connection, db=db)
    db.down = True
    for i in range(MAX_PENDING + 7):
        r.error("dispatch", ValueError(f"e{i}"))
    assert r.pending_errors == MAX_PENDING + 7
    db.down = False
    r.beat()
    errors = rows(connection, WorkerError)
    assert len(errors) == MAX_PENDING + 1
    assert errors[-1].error_type == "DroppedErrors" and "7 件" in errors[-1].message


def test_a_failing_session_never_raises_into_the_worker(connection):
    class Broken:
        def __call__(self):
            return SessionLocal(bind=connection, join_transaction_mode="create_savepoint")

    r = HealthRecorder(Broken(), now=Clock(), host="h" * 300, pid=1)  # 長すぎるホスト名は切る
    r.start()
    assert rows(connection, WorkerRun)[-1].host == "h" * 255

    class Weird(Exception):
        def __str__(self):
            raise RuntimeError("cannot print me")

    r.error("delivery", Weird())  # 文面を作れないエラーでも落ちない
    assert r.pending_errors == 0


def test_every_pass_leaves_a_beat_row_kept_through_an_outage_and_old_rows_are_pruned(connection):
    from src.worker_health.models import WorkerBeat
    from src.worker_health.recorder import KEEP_BEATS

    clock = Clock()
    db = FlakyDb(connection)
    old = HealthRecorder(db, now=lambda: T0 - KEEP_BEATS - timedelta(days=1), host="old", pid=1)
    old.start()
    old.beat(50)  # 91 日前の心拍
    r = recorder(connection, clock, db)
    r.start()
    clock.tick()
    r.beat(120)
    db.down = True
    clock.tick()
    r.beat(130)  # DB が止まっている間の 1 回り
    db.down = False
    clock.tick()
    r.beat(140)
    beats = [(b.at, b.duration_ms) for b in rows(connection, WorkerBeat)]
    assert beats == [(T0 + timedelta(minutes=1), 120), (T0 + timedelta(minutes=2), 130), (T0 + timedelta(minutes=3), 140)]  # 古い行は消えた


class CommitFails:
    """fail の間は、書き込みの最後（commit）で失敗する（接続はできるが、書き終えられない）"""

    def __init__(self, conn):
        self.conn, self.fail = conn, False

    def __call__(self):
        session = SessionLocal(bind=self.conn, join_transaction_mode="create_savepoint")
        if self.fail:
            def commit():
                raise RuntimeError("commit failed")
            session.commit = commit  # type: ignore[method-assign]
        return session


def test_a_write_that_fails_at_commit_keeps_everything_for_the_next_write(connection):
    # C901（複雑さの上限）で _write を分ける前に固定する: 途中で失敗した書き込みは何も残さず、すべてを次に持ち越す
    from src.worker_health.models import WorkerBeat

    clock = Clock()
    db = CommitFails(connection)
    r = HealthRecorder(db, now=clock, host="commit-host", pid=7)
    db.fail = True
    r.start()
    clock.tick()
    r.error("release", ValueError("x"))
    r.beat(10)
    assert r.run_id is None and r.pending_errors == 1
    assert [run for run in rows(connection, WorkerRun) if run.host == "commit-host"] == []

    db.fail = False
    clock.tick()
    r.beat(20)
    [run] = [run for run in rows(connection, WorkerRun) if run.host == "commit-host"]
    assert (run.id, run.started_at, run.passes, run.last_beat_at) == (r.run_id, T0, 2, T0 + timedelta(minutes=2))
    assert [(e.job, e.occurred_at) for e in rows(connection, WorkerError) if e.run_id == run.id] == [
        ("release", T0 + timedelta(minutes=1))]
    assert [(b.at, b.duration_ms) for b in rows(connection, WorkerBeat) if b.run_id == run.id] == [
        (T0 + timedelta(minutes=1), 10), (T0 + timedelta(minutes=2), 20)]
    assert r.pending_errors == 0


def test_old_beats_are_pruned_once_a_day_and_only_after_a_successful_write(connection):
    from src.worker_health.models import WorkerBeat
    from src.worker_health.recorder import KEEP_BEATS

    clock = Clock()
    db = CommitFails(connection)
    r = HealthRecorder(db, now=clock, host="prune-host", pid=8)
    r.start()  # その日の 1 回目: 古い心拍を消す（まだ無い）

    def old_beat_written():
        old = HealthRecorder(CommitFails(connection), now=lambda: T0 - KEEP_BEATS - timedelta(days=1), host="old", pid=1)
        old.start()
        old.beat(50)
        return any(b.duration_ms == 50 for b in rows(connection, WorkerBeat))

    assert old_beat_written()
    clock.tick()
    r.beat()  # 同じ日の 2 回目: 消さない
    assert any(b.duration_ms == 50 for b in rows(connection, WorkerBeat))

    clock.tick(24 * 60)
    db.fail = True
    r.beat()  # 次の日だが、書けなかった: 消したことにならない
    db.fail = False
    assert any(b.duration_ms == 50 for b in rows(connection, WorkerBeat))
    clock.tick()
    r.beat()  # 次の日に書けた: 消す
    assert not any(b.duration_ms == 50 for b in rows(connection, WorkerBeat))
