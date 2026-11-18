"""
締切の監視（通知の仕組み N.2、2026-11-12）。ワーカーが毎分呼び、時刻を過ぎたオーダーの出来事を記録します。

締切は誰も操作しないうちに過ぎるので、操作の場所では記録できない。そこで監視が、保存済みの締切（deadline_at）と
「まもなく」の開始（warn_at）を見て、OrderDueSoon・OrderLate を domain_event に書く。まだ何も送らない（N.3 から）。

- 遅れ・まもなくの決め方はダッシュボードの「要対応」（links.is_late / is_due_soon）と同じ: 完了・取消以外。
  遅れ = 締切を過ぎた。まもなく = warn_at を過ぎて締切の前
- 振り返りの幅（既定 24 時間）: 時刻がこの幅の中で過ぎたものだけ記録する。ワーカーが止まっていた間の分は
  次の回で追いつくが、何週間も前から遅れたままの古いオーダーは記録しない（N.3 で送り始めたとき、
  一度に何百通もの知らせにならない。2026-11-12 の決定）
- 見つけたときにもう締切を過ぎていれば OrderLate だけ（締切の後の「まもなく」は意味がない。同じ決定）
- 1 件のオーダーにつき、その時刻 1 回だけ: キーは「オーダー・種類・その時刻」。締切が動けば時刻が変わり、また記録できる
- 中身はその時点の事実の写し（formats.OrderDeadlineV1）。コミットは呼び出し側（ワーカー）
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, UTC

from sqlalchemy import select
from sqlalchemy.orm import InstrumentedAttribute, Session

from src.domain_biz.models import BizMaster
from src.domain_client.models import ClientMaster
from src.domain_events.formats import EventType, OrderDeadlineV1
from src.domain_events.models import DomainEvent
from src.domain_events.publish import publish
from src.domain_ope.links import FINAL
from src.domain_ope.models import OpeOrder
from src.domain_plan.models import PlanTemplate
from src.core.clock import utc_now

DEFAULT_LOOKBACK = timedelta(hours=24)
SUBJECT_ORDER = "order"


@dataclass
class DeadlineWatchResult:
    """この回に新しく記録した出来事のオーダー ID（もう記録済みのものは入らない）"""
    due_soon: list[int] = field(default_factory=list)
    late: list[int] = field(default_factory=list)


def dedupe_key(order_id: int, event_type: str, moment: datetime) -> str:
    """同じ事実を 2 回記録しないためのキー（時刻は UTC で書く。締切が動けば別のキーになる）"""
    return f"{SUBJECT_ORDER}:{order_id}:{event_type}:{moment.astimezone(UTC).isoformat()}"


def record_deadline_events(
    db: Session, *, now: datetime | None = None, lookback: timedelta = DEFAULT_LOOKBACK,
) -> DeadlineWatchResult:
    """時刻を過ぎたオーダーの OrderLate・OrderDueSoon を記録する（何度呼んでも同じ結果。コミットしない）"""
    now = now or utc_now()
    since = now - lookback
    result = DeadlineWatchResult()
    late = _candidates(
        db, EventType.ORDER_LATE, OpeOrder.deadline_at,
        (OpeOrder.deadline_at < now, OpeOrder.deadline_at >= since),
    )
    for row in late:
        if _record(db, EventType.ORDER_LATE, row.OpeOrder.deadline_at, row):
            result.late.append(row.OpeOrder.id)
    # まもなく: 締切の前のものだけ（もう過ぎていれば上の遅れだけ）
    due_soon = _candidates(
        db, EventType.ORDER_DUE_SOON, OpeOrder.warn_at,
        (OpeOrder.warn_at <= now, OpeOrder.warn_at >= since, OpeOrder.deadline_at > now),
    )
    for row in due_soon:
        assert row.OpeOrder.warn_at is not None
        if _record(db, EventType.ORDER_DUE_SOON, row.OpeOrder.warn_at, row):
            result.due_soon.append(row.OpeOrder.id)
    return result


def _candidates(db: Session, event_type: str, moment: InstrumentedAttribute, conditions: tuple):
    """
    完了・取消以外で、条件に当てはまり、その時刻の出来事がまだないオーダー（名前と一緒に）。
    部分インデックス ix_ope_order_open_deadline / ix_ope_order_open_warn と ix_domain_event_subject を使う。
    テンプレート・業務が論理削除されていても対象（名前は削除前のもの）
    """
    already = (
        select(DomainEvent.id)
        .where(
            DomainEvent.subject_type == SUBJECT_ORDER,
            DomainEvent.subject_id == OpeOrder.id,
            DomainEvent.event_type == event_type,
            DomainEvent.occurred_at == moment,
        )
        .exists()
    )
    stmt = (
        select(OpeOrder, ClientMaster.name.label("client_name"), BizMaster.business_code, BizMaster.name.label("biz_name"),
               PlanTemplate.name.label("template_name"))
        .join(ClientMaster, ClientMaster.code == OpeOrder.client_code)
        .join(BizMaster, BizMaster.id == OpeOrder.biz_master_id)
        .join(PlanTemplate, PlanTemplate.id == OpeOrder.template_id)
        .where(OpeOrder.status.not_in(FINAL), *conditions, ~already)
        .order_by(moment, OpeOrder.id)
        # 論理削除したテンプレート・業務の、まだ開いているオーダーも監視する（既定の絞り込みでは消えてしまう）
        .execution_options(include_deleted=True)
    )
    return db.execute(stmt).all()


def _record(db: Session, event_type: str, moment: datetime, row) -> bool:
    order: OpeOrder = row.OpeOrder
    payload = OrderDeadlineV1(
        order_id=order.id,
        client_code=order.client_code,
        client_name=row.client_name,
        biz_master_id=order.biz_master_id,
        business_code=row.business_code,
        biz_name=row.biz_name,
        template_id=order.template_id,
        template_name=row.template_name,
        occurrence_date=order.occurrence_date,
        scheduled_start=order.scheduled_start,
        scheduled_end=order.scheduled_end,
        deadline_at=order.deadline_at,
        warn_at=order.warn_at,
        status=order.status.value,
    )
    event_id = publish(
        db, event_type=event_type, payload=payload, dedupe_key=dedupe_key(order.id, event_type, moment),
        occurred_at=moment, client_code=order.client_code, subject_type=SUBJECT_ORDER, subject_id=order.id,
    )
    return event_id is not None
