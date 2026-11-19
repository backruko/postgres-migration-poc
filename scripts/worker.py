"""
定期処理のワーカー（デプロイ: docker compose の worker サービス）。Windows のタスク スケジューラーの 2 つの登録
（release_orders.py を 15 分ごと・extend_plans.py を毎日 00:05）の代わりに、1 つのプロセスで回し続けます。

- 自動リリース: 毎分 0 秒すぎ（リリース時刻は分単位なので、設定時刻から 1 分以内にリリースされる）。止まっていた間の分は
  次の回でまとめてリリースする（release_due_orders は何度実行しても同じ結果）
- 予定の生成: 毎日 00:05（日本時間。WORKER_EXTEND_AT で変更可）以降に 1 回。起動したときにその日の分がまだなら、すぐ実行する
- 締切の監視: 毎分、リリースの後に。時刻を過ぎたオーダーの OrderDueSoon・OrderLate を出来事として記録する（まだ送らない）。
  振り返りの幅は WORKER_EVENT_LOOKBACK_HOURS 時間（既定 24）。止まっていた間の分は次の回で追いつく
- 通知の振り分け: 毎分、締切の監視の後に。未処理の出来事を、当てはまるルールの通知にする
- 通知の配信: 毎分、振り分けの後に。送る時刻が来た配信を送る。メールは SMTP_HOST があれば SMTP（N.4a）、
  なければ開発では偽物（送らない）・本番ではメールのチャネルなし（起動時のログに、どちらかを出す）
- どれかが失敗してもログに残して続ける。SIGTERM / Ctrl+C で今の処理が終わってから止まる
- 健康の記録（並行稼働 P.4a）: 起動・毎回りの心拍・止まった理由を worker_run に、仕事の失敗を 1 回ずつ worker_error に書く
  （src/worker_health。DB に書けない間はメモリーにためて後で書く。記録が失敗してもワーカーは止まらない）

    python -m scripts.worker

接続先は DATABASE_URL（環境変数。無ければ .env）。ログは標準出力（docker compose logs worker で見る）。
"""
import logging
import os
import signal
import sys
import threading
import time as time_module
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# src は上の sys.path の後で読む（前に置くと、python scripts/<名前>.py で動かしたときに src が見つからない）
from src.core.clock import LOCAL_TIMEZONE, local_date_of, utc_now  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("worker")



def parse_time(value: str) -> time:
    hours, minutes = value.strip().split(":")
    return time(int(hours), int(minutes))


def seconds_until_next_run(now: datetime) -> float:
    """次の分の 1 秒すぎまでの秒数（毎分 1 回リリースを確かめる）"""
    next_minute = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
    return max(1.0, (next_minute - now).total_seconds() + 1)


def extension_due(now: datetime, last_run: date | None, at: time) -> bool:
    """予定の生成をする時か（日本時間のその日に、at を過ぎて、まだしていない）"""
    local = now.astimezone(LOCAL_TIMEZONE)
    return local.time() >= at and last_run != local.date()


def run_release() -> None:
    from src.domain_ope.services import release_due_orders
    from src.infrastructure.database import SessionLocal

    db = SessionLocal()
    try:
        result = release_due_orders(db)
        if result.released:
            logger.info("Released %d of %d pending automatic orders: %s", len(result.released), result.checked, result.released)
    finally:
        db.close()


def run_extension() -> None:
    from src.domain_plan.services import extend_all_templates
    from src.infrastructure.database import SessionLocal

    db = SessionLocal()
    try:
        result = extend_all_templates(db)
        logger.info(
            "Plan extension: checked %d templates, extended %d, without upcoming occurrence: %s",
            result.templates_checked, result.templates_extended, result.without_upcoming or "none",
        )
    finally:
        db.close()


def run_deadline_watch(lookback: timedelta) -> None:
    from src.domain_ope.deadline_watch import record_deadline_events
    from src.infrastructure.database import SessionLocal

    db = SessionLocal()
    try:
        result = record_deadline_events(db, lookback=lookback)
        db.commit()
        if result.late or result.due_soon:
            logger.info("Deadline events recorded: late %s, due soon %s", result.late or "none", result.due_soon or "none")
    finally:
        db.close()


def run_dispatch() -> None:
    from src.domain_notification.dispatcher import dispatch_events
    from src.infrastructure.database import SessionLocal

    db = SessionLocal()
    try:
        result = dispatch_events(db)
        db.commit()
        if result.notifications or result.suppressed or result.failed_events:
            logger.info(
                "Dispatch: %d events, %d notifications (%d held for quiet hours), suppressed %s, failed events %s",
                result.events, result.notifications, result.held, result.suppressed or "none", result.failed_events or "none",
            )
    finally:
        db.close()


def run_deliveries(channels) -> None:
    from src.domain_notification.delivery import deliver_due
    from src.infrastructure.database import SessionLocal

    db = SessionLocal()
    try:
        result = deliver_due(db, channels=channels)
        if result.sent or result.retrying or result.failed:
            logger.info(
                "Deliveries: sent %d, retrying %d, failed %d; notifications completed %s",
                result.sent, result.retrying, result.failed, result.completed or "none",
            )
    finally:
        db.close()


# 仕事の名前（worker_error.job）と、失敗したときのログの文
JOB_FAILED = {
    "extension": "Plan extension failed (will retry on the next pass)",
    "release": "Automatic release failed (will retry in a minute)",
    "deadline_watch": "Deadline watch failed (will retry in a minute)",
    "dispatch": "Notification dispatch failed (will retry in a minute)",
    "delivery": "Notification delivery failed (will retry in a minute)",
}


def run_job(recorder, job: str, fn: Callable[..., None], *args) -> bool:
    """仕事を 1 つ。失敗はログと健康の記録に残し、False を返す（ほかの仕事は続ける）"""
    try:
        fn(*args)
        return True
    except Exception as error:
        logger.exception(JOB_FAILED[job])
        recorder.error(job, error)
        return False


@dataclass
class PassState:
    extend_at: time
    lookback: timedelta
    channels: object
    last_extension: date | None = None


def run_pass(recorder, state: PassState) -> None:
    """毎分の 1 回り（予定の生成は 1 日 1 回）"""
    now = utc_now()
    if extension_due(now, state.last_extension, state.extend_at):  # noqa: SIM102  (「時刻か」と「成功したか」を分けて読む)
        if run_job(recorder, "extension", run_extension):
            state.last_extension = local_date_of(now)
    run_job(recorder, "release", run_release)
    run_job(recorder, "deadline_watch", run_deadline_watch, state.lookback)
    run_job(recorder, "dispatch", run_dispatch)
    run_job(recorder, "delivery", run_deliveries, state.channels)


def run_loop(recorder, stop: threading.Event, one_pass: Callable[[], None]) -> None:
    """止めるまで毎分回す。起動・心拍・止まった理由を健康の記録に書く。ループの外まで落ちたら crash として記録してから落ちる"""
    from src.worker_health.models import StopReason

    recorder.start()
    try:
        while not stop.is_set():
            began = time_module.monotonic()
            one_pass()
            recorder.beat(round((time_module.monotonic() - began) * 1000))
            stop.wait(seconds_until_next_run(utc_now()))
    except Exception as error:
        logger.exception("Worker crashed")
        recorder.error("worker", error)
        recorder.stop(StopReason.CRASH)
        raise
    recorder.stop(StopReason.SIGNAL)


def main() -> int:
    from src.coordination import order_sync
    from src.domain_notification.channels import default_channels, describe
    from src.infrastructure.database import engine
    from src.worker_health.recorder import HealthRecorder

    engine.echo = False  # 毎分の実行ログに SQL を出さない
    order_sync.register()  # 予定を作る処理がオーダーも作るように（アプリと同じ受け手）
    channels = default_channels()
    state = PassState(
        extend_at=parse_time(os.environ.get("WORKER_EXTEND_AT", "00:05")),
        lookback=timedelta(hours=float(os.environ.get("WORKER_EVENT_LOOKBACK_HOURS", "24"))),
        channels=channels,
    )

    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())

    logger.info(
        "Worker started (automatic release, deadline watch, notification dispatch and delivery every minute, lookback %gh; "
        "plan extension daily after %s JST)",
        state.lookback.total_seconds() / 3600, state.extend_at.strftime("%H:%M"),
    )
    logger.info("Notification channels: %s", describe(channels))
    recorder = HealthRecorder()
    run_loop(recorder, stop, lambda: run_pass(recorder, state))
    logger.info("Worker stopped (health record run %s)", recorder.run_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
