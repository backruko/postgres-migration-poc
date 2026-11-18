"""
自動リリースのジョブ。自動リリース（AUTOMATIC）の業務の pending オーダーのうち、リリース日時
（予定日の release_days_before 日前の release_time。日本時間。既定 03:00 = 夜間バッチの後）を過ぎたものを ready にします。
何度実行しても同じ結果になります。止まっていた間の分も次の実行でまとめてリリースします。

    python scripts/release_orders.py

OS のスケジューラーから15分ごとに実行します（業務ごとにリリース時刻が違うため。リリースは設定時刻から最大15分遅れ）。
接続先は .env の DATABASE_URL（環境変数 DATABASE_URL が優先）。終了コードは 0（失敗時は 1）。
"""
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("release_orders")

from src.domain_ope.services import release_due_orders  # noqa: E402
from src.infrastructure.database import SessionLocal, engine  # noqa: E402

engine.echo = False  # 実行ログに全SQLを出さない（アプリ側の echo 設定はそのまま）


def main() -> int:
    db = SessionLocal()
    try:
        result = release_due_orders(db)
    except Exception:
        logger.exception("Automatic release failed")
        return 1
    finally:
        db.close()
    logger.info("Checked %d pending orders of automatic businesses, released %d: %s",
                result.checked, len(result.released), result.released or "none")
    return 0


if __name__ == "__main__":
    sys.exit(main())
