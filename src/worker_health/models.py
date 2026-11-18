"""
ワーカーの健康の記録（並行稼働 P.4a、2026-11-16）。

並行稼働の承認の条件の 1 つは「14 日間、ワーカーに処理されないエラー・落ちることが無かった」こと。いままではログ（標準出力）に
出すだけで、数えられず、コンテナを作り直すと消えた。そこで、ワーカー自身が次の 2 つをデータベースに書く。

- worker_run: ワーカーのプロセス 1 つにつき 1 行。起動・最後の心拍（毎分の 1 回りの終わりに更新）・止まった時刻と理由。
  止まった理由が無いまま心拍が途絶えた行は、落ちたか強制終了された印（P.4b で数える）
- worker_beat: 1 回りごとに 1 行（P.4b で追加。日ごとの集計と、プロセスの中の途切れを見つけるため）
- worker_error: 仕事（リリース・予定の生成・締切の監視・通知の振り分け・配信）が失敗して、ワーカーが受け止めた 1 回ごとに 1 行。
  エラーの種類・文面・トレースバック。データベースに書けない間はワーカーのメモリーにためて、書けるようになったら書く

業務の結果（メールが再送の後も届かなかった・出来事を 5 回で諦めた）はここに書かない。それぞれの画面にある（2026-11-16 の決定）。
"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database import Base


class StopReason:
    SIGNAL = "signal"  # SIGTERM / Ctrl+C で、今の処理が終わってから止まった（きれいな停止）
    CRASH = "crash"  # 想定外のエラーで、ループの外まで落ちた


class WorkerRun(Base):
    __tablename__ = "worker_run"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    host: Mapped[str] = mapped_column(String(255), nullable=False, comment="動いているマシン（コンテナ）の名前")
    pid: Mapped[int] = mapped_column(Integer, nullable=False, comment="プロセス ID")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="起動した日時")
    last_beat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="最後の心拍（毎分の 1 回りが終わった日時。起動のときは起動の日時）"
    )
    passes: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), comment="終えた 1 回りの数")
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="止まった日時（無ければ動いているか、落ちた）")
    stop_reason: Mapped[str | None] = mapped_column(String(16), comment="止まった理由: signal（きれいな停止）/ crash")

    __table_args__ = (Index("ix_worker_run_started_at", "started_at"),)


class WorkerBeat(Base):
    """1 回りごとの心拍（P.4b、2026-11-16 の決定）。日ごとの回数・動いていた割合・5 分を超える途切れ・いちばん遅い回りを数えるため。
    1 日に約 1,440 行。90 日より古い行はワーカーが消す"""

    __tablename__ = "worker_beat"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("worker_run.id", ondelete="CASCADE"), nullable=False, comment="どのワーカーのプロセスか"
    )
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="1 回りが終わった日時")
    duration_ms: Mapped[int | None] = mapped_column(Integer, comment="その 1 回りにかかった時間（ミリ秒）")

    __table_args__ = (
        Index("ix_worker_beat_at", "at"),
        Index("ix_worker_beat_run_id", "run_id"),
    )


class WorkerError(Base):
    __tablename__ = "worker_error"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("worker_run.id", ondelete="CASCADE"), nullable=False, comment="どのワーカーのプロセスか"
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="失敗した日時")
    job: Mapped[str] = mapped_column(String(32), nullable=False, comment="どの仕事か（release / extension / deadline_watch / dispatch / delivery / worker）")
    error_type: Mapped[str] = mapped_column(String(200), nullable=False, comment="エラーの種類（例: sqlalchemy.exc.OperationalError）")
    message: Mapped[str] = mapped_column(Text, nullable=False, comment="エラーの文面")
    traceback: Mapped[str] = mapped_column(Text, nullable=False, comment="トレースバック（調べるため）")

    __table_args__ = (
        Index("ix_worker_error_occurred_at", "occurred_at"),
        Index("ix_worker_error_run_id", "run_id"),
    )
