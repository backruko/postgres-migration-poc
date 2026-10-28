"""
日次の予定生成ジョブ。全顧客の有効なテンプレートについて、生成済み区間を
「今から90日先」と「次の予定」の遅い方まで延ばします（上限2年）。何度実行しても同じ結果になります。

    python scripts/extend_plans.py

OS のスケジューラーから1日1回実行します（例: cron `5 0 * * *`、Windows のタスク スケジューラー）。
接続先は .env の DATABASE_URL（環境変数 DATABASE_URL が優先）。
次の予定が2年以内に無いテンプレートの ID を最後に表示し、終了コードは 0 です（失敗時は 1）。
"""
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("extend_plans")

from src.domain_plan.services import extend_all_templates  # noqa: E402
from src.infrastructure.database import SessionLocal, engine  # noqa: E402

engine.echo = False  # 毎日の実行ログに全SQLを出さない（アプリ側の echo 設定はそのまま）


def main() -> int:
    db = SessionLocal()
    try:
        result = extend_all_templates(db)
    except Exception:
        logger.exception("Plan generation job failed")
        return 1
    finally:
        db.close()
    logger.info(
        "Checked %d templates, extended %d, without upcoming occurrence: %s",
        result.templates_checked, result.templates_extended, result.without_upcoming or "none",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
