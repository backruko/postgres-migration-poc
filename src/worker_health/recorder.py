"""
ワーカーが自分の健康を書く（並行稼働 P.4a、2026-11-16）。

    recorder = HealthRecorder()
    recorder.start()                 # 起動: worker_run に 1 行
    recorder.error("release", err)   # 仕事が失敗した: worker_error に 1 行（ワーカーは続ける）
    recorder.beat(duration_ms)       # 1 回りが終わった: 最後の心拍と回数を更新し、worker_beat に 1 行（P.4b）
    recorder.stop(StopReason.SIGNAL) # 止まった

- 記録のために、ワーカーを止めない: どの呼び出しも例外を外に出さない（書けなければログに警告を出すだけ）
- データベースに書けない間（DB が止まっている等）は、エラーと回数をメモリーにためておき、次に書けたときにまとめて書く。
  起動の行もまだ書けていなければ、そのとき書く（起動の日時は最初の起動の時刻のまま）
- ためるエラーは MAX_PENDING 件まで。超えた分は数だけ数え、書けたときに「記録しきれなかった N 件」の 1 行を足す
- 心拍の行も同じようにためる（MAX_PENDING_BEATS = 7 日分まで。超えたら古いほうから捨てる: 捨てた分は途切れに見えるが、
  記録として正直）。90 日より古い心拍の行は、1 日 1 回、書くついでに消す
- 1 回の書き込みは 1 つの短いトランザクション（仕事のトランザクションとは別。仕事の失敗に巻き込まれない）
"""
from __future__ import annotations

import logging
import os
import socket
import traceback as tb
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import delete, update
from sqlalchemy.orm import Session

from src.worker_health.models import WorkerBeat, WorkerError, WorkerRun
from src.core.clock import utc_now as _utcnow

logger = logging.getLogger("worker.health")

MAX_PENDING = 500
MAX_PENDING_BEATS = 10080  # DB に書けない間にためる心拍（7 日分）
KEEP_BEATS = timedelta(days=90)
MAX_MESSAGE = 4000
MAX_TRACEBACK = 20000


@dataclass(frozen=True)
class _Pending:
    occurred_at: datetime
    job: str
    error_type: str
    message: str
    traceback: str


def error_type_of(error: BaseException) -> str:
    kind = type(error)
    name = kind.__qualname__ if kind.__module__ == "builtins" else f"{kind.__module__}.{kind.__qualname__}"
    return name[:200]


def _default_session_factory() -> Session:
    from src.infrastructure.database import SessionLocal

    return SessionLocal()


class HealthRecorder:
    def __init__(
        self,
        session_factory: Callable[[], Session] = _default_session_factory,
        *,
        now: Callable[[], datetime] = _utcnow,
        host: str | None = None,
        pid: int | None = None,
    ):
        self._session_factory = session_factory
        self._now = now
        self.host = (host or socket.gethostname())[:255]
        self.pid = pid if pid is not None else os.getpid()
        self.started_at = now()
        self.run_id: int | None = None
        self._pending: list[_Pending] = []
        self._dropped = 0
        self._passes = 0  # まだ書けていない回数
        self._beat_at: datetime | None = None
        self._beats: list[tuple[datetime, int | None]] = []
        self._pruned_on = None  # 古い心拍を最後に消した日（UTC）
        self._stop: tuple[datetime, str] | None = None

    # --- ワーカーから呼ぶ ---------------------------------------------------
    def start(self) -> None:
        self._write()

    def beat(self, duration_ms: int | None = None) -> None:
        self._passes += 1
        self._beat_at = self._now()
        if len(self._beats) >= MAX_PENDING_BEATS:
            self._beats.pop(0)  # 新しいほうを残す
        self._beats.append((self._beat_at, duration_ms))
        self._write()

    def error(self, job: str, error: BaseException) -> None:
        try:
            text = "".join(tb.format_exception(type(error), error, error.__traceback__))
            entry = _Pending(self._now(), job[:32], error_type_of(error), str(error)[:MAX_MESSAGE] or repr(error)[:MAX_MESSAGE],
                             text[-MAX_TRACEBACK:])
        except Exception:  # 記録のための処理が失敗しても、ワーカーは続ける
            logger.warning("Could not format a worker error for the health record", exc_info=True)
            return
        if len(self._pending) >= MAX_PENDING:
            self._dropped += 1
        else:
            self._pending.append(entry)
        self._write()  # すぐ書く（この後でプロセスが殺されても残るように）

    def stop(self, reason: str) -> None:
        self._stop = (self._now(), reason)
        self._write()

    @property
    def pending_errors(self) -> int:
        return len(self._pending) + self._dropped

    # --- 書き込み -----------------------------------------------------------
    def _write(self) -> None:
        """ためているものを 1 つのトランザクションで書く。書けたときだけメモリーを空にする（失敗したら次に持ち越す）"""
        try:
            with self._session_factory() as db:
                run_id = self._run_row(db)
                self._update_run(db, run_id)
                self._add_pending_rows(db, run_id)
                today = self._now().date()
                prune = self._pruned_on != today
                if prune:
                    db.execute(delete(WorkerBeat).where(WorkerBeat.at < self._now() - KEEP_BEATS))
                self._add_dropped_row(db, run_id)
                db.commit()
        except Exception as error:
            logger.warning("Could not write the worker health record (kept in memory, %d errors pending): %s",
                           self.pending_errors, error)
            return
        self._written(run_id, pruned_on=today if prune else None)

    def _run_row(self, db: Session) -> int:
        """この起動の worker_run の id（まだ書けていなければ、起動の日時のまま行を作る）"""
        if self.run_id is not None:
            return self.run_id
        run = WorkerRun(host=self.host, pid=self.pid, started_at=self.started_at, last_beat_at=self.started_at, passes=0)
        db.add(run)
        db.flush()
        return run.id

    def _update_run(self, db: Session, run_id: int) -> None:
        """回数を足し、最後の心拍と止まり方を書く（どれも、ためているものがあるときだけ）"""
        values: dict = {}
        if self._passes:
            values["passes"] = WorkerRun.passes + self._passes
        if self._beat_at is not None:
            values["last_beat_at"] = self._beat_at
        if self._stop is not None:
            values["stopped_at"], values["stop_reason"] = self._stop
        if values:
            db.execute(update(WorkerRun).where(WorkerRun.id == run_id).values(**values))

    def _add_pending_rows(self, db: Session, run_id: int) -> None:
        """ためているエラーと心拍の行を足す（日時は、起きたときのまま）"""
        for p in self._pending:
            db.add(WorkerError(run_id=run_id, occurred_at=p.occurred_at, job=p.job, error_type=p.error_type,
                               message=p.message, traceback=p.traceback))
        for at, duration_ms in self._beats:
            db.add(WorkerBeat(run_id=run_id, at=at, duration_ms=duration_ms))

    def _add_dropped_row(self, db: Session, run_id: int) -> None:
        """ためきれなかったエラーがあれば、その数を 1 行で残す"""
        if self._dropped:
            db.add(WorkerError(
                run_id=run_id, occurred_at=self._now(), job="worker", error_type="DroppedErrors",
                message=f"データベースに書けない間に {self._dropped} 件のエラーがあり、ためきれずに記録できませんでした（ログを見てください）",
                traceback="",
            ))

    def _written(self, run_id: int, *, pruned_on: date | None) -> None:
        """書けた後: run_id を覚え、ためていたものを空にする。古い心拍を消した日も、書けたときだけ覚える"""
        self.run_id = run_id
        self._pending.clear()
        self._dropped = 0
        self._passes = 0
        self._beat_at = None
        self._beats.clear()
        if pruned_on is not None:
            self._pruned_on = pruned_on
