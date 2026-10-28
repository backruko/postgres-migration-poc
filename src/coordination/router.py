from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from src.core.schemas.error import COMMON_ERRORS
from src.infrastructure.database import get_db
from src.coordination.schemas import DailyDashboardResponse
from src.coordination.services import get_daily_dashboard

router = APIRouter(prefix="/ops", tags=["ops"])

# ==========================================
# 日次ダッシュボード（全顧客横断）エンドポイント
# ==========================================

@router.get("/daily", response_model=DailyDashboardResponse, responses={**COMMON_ERRORS})
def get_daily_dashboard_endpoint(
    target_date: date | None = Query(None, alias="date", description="表示する日 (例: 2026-11-02)。省略時は timezone での今日"),
    timezone: str = Query("Asia/Tokyo", description="日付の振り分けに使うタイムゾーン"),
    db: Session = Depends(get_db),
):
    """
    指定日に予定されている全顧客の予定を、顧客 → 業務の順にまとめて返します。
    その日の年間計画シートの日付と、各営業日カレンダーでの休日かどうかも返します。
    過去の日付では、無効化・取消された予定も理由付きで返します。
    """
    return get_daily_dashboard(db=db, target_date=target_date, tz_name=timezone)
