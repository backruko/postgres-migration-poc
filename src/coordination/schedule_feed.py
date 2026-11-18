"""
mfa への作業の開始の時刻表（GET /ops/schedule-feed。並行稼働 P.1、2026-11-16）。

mfa は「この作業を始めて」と知らせる号砲（締切の警告ではない）。ファイルに書かれた時刻になると、その行の命令を実行する。
アダプターは毎晩 1 回、この一覧から先の日数分の予定を mfa のファイルと Access の取り込みファイルに書く（毎分の書き直しはしない）。
時刻は予定の開始。締切は文面に入れるために返す。文面・ファイルの形はアダプターが決める（システムは mfa を知らない）。

- 人が動く作業だけ: 分類が不定期・スペシャル（既定。categories で変えられる）。臨時に追加した予定はスペシャル
- 決まった時刻に始める作業だけ: テンプレートの「開始時刻に知らせる」（start_prompt）がオン。外からの合図（ファイルの到着など）を
  待つ作業はオフにして出さない（届いていないファイルの作業で鳴らないように。2026-11-16 の決定）。臨時の追加もテンプレートに従う
- まだ動いていないオーダーだけ: 未リリース・リリース済。取消・保留・作業中・完了は出さない（その枠は鳴らない。2026-11-16 の決定:
  mfa の今のスクリプトは「これをやって」としか言えない）
- 窓: from（既定はいまの分）から days 日先まで、開始の順
- key は行を突き合わせるため（新旧のファイルの照合）: order:<id>:start:<開始（UTC）>
- 顧客の範囲がある職員・キーには、その顧客のものだけ
"""
from datetime import datetime, timedelta, UTC

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.coordination.schemas import ScheduleFeedItem, ScheduleFeedResponse
from src.domain_biz.models import BizMaster
from src.domain_client.models import ClientMaster
from src.domain_ope.models import OpeOrder, OrderStatus
from src.domain_plan.category import CATEGORIES, ONE_OFF_CATEGORY, TaskCategory, category_of
from src.domain_plan.models import PlanTemplate
from src.domain_staff.scope import ALL_CLIENTS, ClientScope
from src.core.clock import LOCAL_TIMEZONE, utc_now

ACTIONABLE = (OrderStatus.PENDING, OrderStatus.READY)  # まだ動いていない（これから始める）
DEFAULT_CATEGORIES: tuple[TaskCategory, ...] = ("adhoc", "special")


def _key(order_id: int, start: datetime) -> str:
    return f"order:{order_id}:start:{start.astimezone(UTC).isoformat()}"


def get_schedule_feed(
    db: Session, *, start: datetime | None = None, days: int = 30, categories: tuple[TaskCategory, ...] = DEFAULT_CATEGORIES,
    limit: int = 5000, scope: ClientScope = ALL_CLIENTS, now: datetime | None = None,
) -> ScheduleFeedResponse:
    """start（タイムゾーンなしは日本時間。既定はいまの分）から days 日先までの、開始の時刻表"""
    now = now or utc_now()
    if start is None:
        start = now.replace(second=0, microsecond=0)
    elif start.tzinfo is None:
        start = start.replace(tzinfo=LOCAL_TIMEZONE)
    end = start + timedelta(days=days)
    wanted = set(categories)

    stmt = (
        select(OpeOrder, ClientMaster.name.label("client_name"), BizMaster.business_code, BizMaster.name.label("biz_name"),
               PlanTemplate.name.label("template_name"), PlanTemplate.ui_metadata)
        .join(ClientMaster, ClientMaster.code == OpeOrder.client_code)
        .join(BizMaster, BizMaster.id == OpeOrder.biz_master_id)
        .join(PlanTemplate, PlanTemplate.id == OpeOrder.template_id)
        .where(
            OpeOrder.status.in_(ACTIONABLE),
            PlanTemplate.start_prompt.is_(True),
            OpeOrder.scheduled_start >= start, OpeOrder.scheduled_start < end,  # ix_ope_order_start
            scope.clause(OpeOrder.client_code),
        )
        .order_by(OpeOrder.scheduled_start, OpeOrder.id)
        .execution_options(include_deleted=True)
    )
    items: list[ScheduleFeedItem] = []
    for row in db.execute(stmt):
        order: OpeOrder = row.OpeOrder
        # 分類はテンプレートの設定。臨時に追加した予定（単発）は人が動く作業なのでスペシャル
        category = ONE_OFF_CATEGORY if order.one_off_instance_id is not None else category_of(row.ui_metadata)
        if category not in wanted:
            continue
        items.append(ScheduleFeedItem(
            key=_key(order.id, order.scheduled_start), scheduled_start=order.scheduled_start, category=category,
            one_off=order.one_off_instance_id is not None, order_id=order.id, client_code=order.client_code,
            client_name=row.client_name, biz_code=row.business_code, biz_name=row.biz_name, title=row.template_name,
            occurrence_date=order.occurrence_date, scheduled_end=order.scheduled_end, deadline_at=order.deadline_at,
            status=order.status,
        ))
    return ScheduleFeedResponse(
        now=now, window_start=start, window_end=end, categories=[c for c in CATEGORIES if c in wanted],
        items=items[:limit], truncated=len(items) > limit,
    )
