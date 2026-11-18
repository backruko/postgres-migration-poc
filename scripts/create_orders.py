"""
運用開始時のオーダー作成。今以降に開始する有効な予定のうち、オーダーの無い発生枠に pending のオーダーを作ります
（過去の予定には作りません）。何度実行しても同じ結果になります。以後の予定は plan の変更に合わせて自動で作られます。

    python scripts/create_orders.py

接続先は .env の DATABASE_URL（環境変数 DATABASE_URL が優先）。
"""
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("create_orders")

from src.coordination import order_sync  # noqa: E402
from src.infrastructure.database import SessionLocal, engine  # noqa: E402

engine.echo = False


def main() -> int:
    order_sync.register()
    db = SessionLocal()
    try:
        created = order_sync.create_missing_orders(db)
    except Exception:
        logger.exception("Order creation failed")
        return 1
    finally:
        db.close()
    logger.info("Created %d orders", created)
    return 0


if __name__ == "__main__":
    sys.exit(main())
