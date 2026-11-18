"""
オーダーの締切（N.2、2026-11-05 の決定）。締切はオーダーの行に保存する（SLA の正。表示のたびに計算しない）。

- 作成時に、テンプレートの締切の決め方（予定の終了 / 開始から / 固定の時刻）とタイムゾーンをオーダーに写す（copy_rule）。
  以後テンプレートを変えても、写した決め方は変わらない（リリース前のオーダーだけ写し直す: N.3）。
- 予定が動いたら、このオーダー自身の写した決め方で締切を計算し直す（restamp）。
  開始から = 予定開始 + 分（予定と一緒に動く）。固定の時刻 = 予定日（予定開始のローカル日付）+ 何日 の その時刻
  （同じ日の中で動いても締切は動かない。日が変われば、その日の時刻）。予定の終了 = scheduled_end。
- 「まもなく」は締切の warn_before_minutes 分前から（0 なら出さない: warn_at = NULL）。
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from src.domain_ope.models import OpeOrder
from src.domain_plan.models import DeadlineMode, PlanTemplate


def copy_rule(order: OpeOrder, template: PlanTemplate) -> None:
    """テンプレートの締切の決め方を、オーダーに写す（作成時。N.3 ではリリース前の写し直しにも使う）"""
    order.deadline_mode = template.deadline_mode
    order.deadline_offset_minutes = template.deadline_offset_minutes
    order.deadline_time = template.deadline_time
    order.deadline_day_offset = template.deadline_day_offset
    order.deadline_timezone = template.timezone
    order.warn_before_minutes = template.warn_before_minutes


def compute(order: OpeOrder) -> tuple[datetime, datetime | None]:
    """オーダーの写した決め方と、今の予定開始・終了から、(締切, 「まもなく」の開始) を返す"""
    if order.deadline_mode == DeadlineMode.OFFSET:
        assert order.deadline_offset_minutes is not None
        deadline = order.scheduled_start + timedelta(minutes=order.deadline_offset_minutes)
    elif order.deadline_mode == DeadlineMode.FIXED_TIME:
        assert order.deadline_time is not None and order.deadline_day_offset is not None
        zone = ZoneInfo(order.deadline_timezone)
        day = order.scheduled_start.astimezone(zone).date() + timedelta(days=order.deadline_day_offset)
        deadline = datetime.combine(day, order.deadline_time, tzinfo=zone)
    else:
        deadline = order.scheduled_end
    warn = deadline - timedelta(minutes=order.warn_before_minutes) if order.warn_before_minutes else None
    return deadline, warn


def restamp(order: OpeOrder) -> None:
    """締切と「まもなく」の開始を、今の予定から計算し直して保存する（作成・予定の移動・写し直しのあと）"""
    order.deadline_at, order.warn_at = compute(order)
