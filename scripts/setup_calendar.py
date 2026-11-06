"""
営業日カレンダーを 1 つ作ります。新しいデータベース（本番のサーバー）を使い始めるときに 1 回だけ動かすスクリプトです
（カレンダーを作る画面はありません。予定テンプレートはカレンダーを選ぶので、テンプレートより先に作ります）。

    python scripts/setup_calendar.py [--code JP_BANK] [--name 銀行営業日] [--weekend 6 7]

--weekend は週末として休む曜日（ISO: 1=月 … 7=日）。既定は土日。
同じコードのカレンダーがもうあれば、何も変えずにそれを表示して終わります（2 回動かしても大丈夫）。
休日（祝日・年末年始など）は、作ったあとで画面の「設定 → 休日」から登録します。
接続先は .env の DATABASE_URL（環境変数 DATABASE_URL が優先）。
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pydantic import ValidationError  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from src.coordination import order_sync  # noqa: E402
from src.core.exceptions import BusinessException  # noqa: E402
from src.domain_plan.models import BusinessCalendar  # noqa: E402
from src.domain_plan.schemas import BusinessCalendarCreate  # noqa: E402
from src.domain_plan.services import create_calendar  # noqa: E402
from src.infrastructure.database import SessionLocal, engine  # noqa: E402
from src.infrastructure.repositories import business_calendar_repo  # noqa: E402

engine.echo = False

DEFAULT_CODE = "JP_BANK"
DEFAULT_NAME = "銀行営業日"


def setup_calendar(db: Session, calendar_in: BusinessCalendarCreate) -> tuple[BusinessCalendar, bool]:
    """カレンダーを作る。同じコードのものがあればそれを返す（変えない）。戻り値の 2 つ目は作ったかどうか"""
    existing = business_calendar_repo.get_by_code(db, code=calendar_in.code)
    if existing:
        return existing, False
    return create_calendar(db, calendar_in), True


def describe(calendar: BusinessCalendar) -> str:
    return (f"id={calendar.id} code={calendar.code} name={calendar.name} "
            f"weekend_days={sorted(calendar.weekend_days)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a business calendar (once, on a new database).")
    parser.add_argument("--code", default=DEFAULT_CODE)
    parser.add_argument("--name", default=DEFAULT_NAME)
    parser.add_argument("--weekend", nargs="+", type=int, default=[6, 7], metavar="ISO_WEEKDAY",
                        help="weekend days, ISO weekdays 1=Mon ... 7=Sun (default: 6 7)")
    args = parser.parse_args()

    try:
        calendar_in = BusinessCalendarCreate(code=args.code, name=args.name, weekend_days=args.weekend)
    except ValidationError as e:
        print(f"Invalid input: {e}", file=sys.stderr)
        return 1

    # plan を変えるスクリプトは受け手を登録する（新しいカレンダーには予定がないので、実際には何も起きない）
    order_sync.register()
    db = SessionLocal()
    try:
        calendar, created = setup_calendar(db, calendar_in)
        if created:
            print(f"Created business calendar {describe(calendar)}")
        else:
            print(f"Business calendar {calendar.code} already exists, nothing changed: {describe(calendar)}")
            if sorted(calendar.weekend_days) != sorted(calendar_in.weekend_days):
                print("Note: its weekend days differ from --weekend; change them in the API if needed.", file=sys.stderr)
    except BusinessException as e:
        print(f"Could not create the business calendar: {e.message}", file=sys.stderr)
        return 1
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
