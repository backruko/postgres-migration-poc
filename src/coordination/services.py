from datetime import date

from sqlalchemy.orm import Session

from src.coordination.schemas import DailyDashboardResponse


def get_daily_dashboard(db: Session, target_date: date | None, tz_name: str) -> DailyDashboardResponse:
    """
    指定日（省略時は指定タイムゾーンでの今日）に予定されている全顧客の予定・シート日付・休日を返します。
    ※ 骨組みのみ。実装は次のステップで行います。
    """
    raise NotImplementedError("get_daily_dashboard is not implemented yet")
