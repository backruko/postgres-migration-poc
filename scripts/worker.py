"""
定期処理のワーカー（デプロイ: docker compose の worker サービス）。Windows のタスク スケジューラーの 2 つの登録
（release_orders.py を 15 分ごと・extend_plans.py を毎日 00:05）の代わりに、1 つのプロセスで回し続けます。

- 自動リリース: 毎分 0 秒すぎ（リリース時刻は分単位なので、設定時刻から 1 分以内にリリースされる）。止まっていた間の分は
  次の回でまとめてリリースする（release_due_orders は何度実行しても同じ結果）
- 予定の生成: 毎日 00:05（日本時間。WORKER_EXTEND_AT で変更可）以降に 1 回。起動したときにその日の分がまだなら、すぐ実行する
- どちらかが失敗してもログに残して続ける。SIGTERM / Ctrl+C で今の処理が終わってから止まる

    python scripts/worker.py

接続先は DATABASE_URL（環境変数。無ければ .env）。ログは標準出力（docker compose logs worker で見る）。
"""
import logging
import os
import signal
import sys
import threading
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("worker")

LOCAL_TIMEZONE = ZoneInfo("Asia/Tokyo")


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


def main() -> int:
    from src.coordination import order_sync
    from src.infrastructure.database import engine

    engine.echo = False  # 毎分の実行ログに SQL を出さない
    order_sync.register()  # 予定を作る処理がオーダーも作るように（アプリと同じ受け手）
    extend_at = parse_time(os.environ.get("WORKER_EXTEND_AT", "00:05"))

    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())

    logger.info("Worker started (automatic release every minute, plan extension daily after %s JST)", extend_at.strftime("%H:%M"))
    last_extension: date | None = None
    while not stop.is_set():
        now = datetime.now(timezone.utc)
        if extension_due(now, last_extension, extend_at):
            try:
                run_extension()
            except Exception:
                logger.exception("Plan extension failed (will retry on the next pass)")
            else:
                last_extension = now.astimezone(LOCAL_TIMEZONE).date()
        try:
            run_release()
        except Exception:
            logger.exception("Automatic release failed (will retry in a minute)")
        stop.wait(seconds_until_next_run(datetime.now(timezone.utc)))
    logger.info("Worker stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
