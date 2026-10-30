"""
plan の発生枠の変更をオーダーに反映します（plan → ope の追従）。

plan がコミットする直前に、変更された発生枠の一覧を受け取り（domain_plan.slot_events）、同じトランザクションで
オーダーを作成・移動・取消・矛盾フラグにします。設計書の「Propagating plan changes」の表のとおり:

| plan の変更             | pending / ready | 保留中         | 作業中                 | 完了 / 取消 |
|-------------------------|-----------------|----------------|------------------------|-------------|
| 人が日時を変更           | 追従            | 追従（保留のまま）| 拒否（plan の変更ごと） | 影響なし    |
| 人が取消                 | 取消（理由付き） | 取消           | 拒否                   | 影響なし    |
| システムが枠を動かした    | 追従            | 追従           | そのまま + 矛盾フラグ   | 影響なし    |
| 枠が消えた（システム）    | システムが取消   | 矛盾フラグ      | 矛盾フラグ             | 影響なし    |
| 新しい枠                 | 新しい pending オーダー（今以降の予定のみ）                                  |

取消されたオーダーは履歴として残り、同じ枠が戻ったときは新しいオーダーを作ります。
起動時に register() で plan に受け手として登録します（アプリ: main.py、スクリプト・テストも同様）。
"""
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.error_codes import ErrorCode
from src.core.exceptions import DuplicatedRecordError
from src.domain_biz.models import BizMaster
from src.domain_ope.models import EventSource, OpeOrder, OrderEventType, OrderStatus
from src.domain_plan.models import PlanInstance, PlanOccurrenceCache, PlanTemplate, VoidReason
from src.domain_plan.slot_events import (
    SlotChange, SlotKey, live_instance_for_slot, register_slot_listener, slot_key_of,
)
from src.infrastructure.repositories import ope_order_repo

# plan に追従する（人・システムが日時を動かせる）状態
FOLLOWING = (OrderStatus.PENDING, OrderStatus.READY, OrderStatus.ON_HOLD)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def register() -> None:
    """plan の発生枠の変更の受け手として登録します（何度呼んでも1回だけ登録される）。"""
    register_slot_listener(apply_slot_changes)


def apply_slot_changes(db: Session, changes: list[SlotChange]) -> None:
    now = _now()
    for change in changes:
        _apply(db, change, now)


def _find_order(db: Session, slot: SlotKey) -> OpeOrder | None:
    return ope_order_repo.get_active_for_slot(
        db, template_id=slot.template_id, occurrence_date=slot.occurrence_date,
        sheet_event_id=slot.sheet_event_id, one_off_instance_id=slot.one_off_instance_id,
    )


def _apply(db: Session, change: SlotChange, now: datetime) -> None:
    live = live_instance_for_slot(db, change.slot)
    order = _find_order(db, change.slot)
    if order is None:
        # 新しい枠（または取消後に戻った枠）。過去の予定にはオーダーを作らない（運用開始より前は plan の履歴のみ）
        if live is not None and live.scheduled_start >= now:
            create_order(db, change.slot, live, _source(change), change.actor_staff_id, _cause(change))
        return
    if order.status not in FOLLOWING and order.status != OrderStatus.IN_PROGRESS:
        return  # 完了したオーダーは影響を受けない
    if live is None:
        _slot_removed(db, order, change)
    elif (live.id, live.scheduled_start, live.scheduled_end) != (
        order.current_instance_id, order.scheduled_start, order.scheduled_end
    ):
        _slot_moved(db, order, live, change)


# ==========================================
# 記録の出どころ
# ==========================================
def _source(change: SlotChange) -> EventSource:
    # 人の操作でも職員が分からない呼び出し（スクリプト・テスト）はシステムの処理として記録する
    return EventSource.HUMAN if change.by_person and change.actor_staff_id is not None else EventSource.SYSTEM


def _cause(change: SlotChange, **extra: Any) -> dict[str, Any]:
    detail: dict[str, Any] = {"cause": "manual plan change" if change.by_person else "plan change"}
    if change.void_reasons:
        detail["plan_reasons"] = sorted({r.value for r in change.void_reasons})
    detail.update(extra)
    return detail


def _plan_reason(change: SlotChange) -> str | None:
    """システムによる取消・矛盾の理由（plan の無効化の理由）"""
    reasons = [r for r in change.void_reasons if r not in (VoidReason.MANUAL, VoidReason.REVERTED)]
    return reasons[-1].value if reasons else None


def _refuse_in_progress(order: OpeOrder) -> None:
    raise DuplicatedRecordError(
        f"Order {order.id} is in progress, so its schedule cannot be changed or cancelled.",
        error_code=ErrorCode.ORDER_IN_PROGRESS,
    )


# ==========================================
# オーダーの作成・追従・取消・矛盾
# ==========================================
def create_order(
    db: Session, slot: SlotKey, instance: PlanInstance, source: EventSource, actor_staff_id: int | None,
    detail: dict[str, Any],
) -> OpeOrder | None:
    template = db.get(PlanTemplate, slot.template_id)
    biz = db.get(BizMaster, template.biz_master_id) if template else None
    if template is None or biz is None or biz.is_deleted:
        return None  # 削除された業務の予定にはオーダーを作らない
    order = OpeOrder(
        template_id=slot.template_id,
        occurrence_date=instance.occurrence_date,
        source_sheet_event_id=slot.sheet_event_id,
        one_off_instance_id=slot.one_off_instance_id,
        current_instance_id=instance.id,
        biz_master_id=biz.id,
        client_code=biz.client_code,
        scheduled_start=instance.scheduled_start,
        scheduled_end=instance.scheduled_end,
        status=OrderStatus.PENDING,
    )
    db.add(order)
    db.flush()
    ope_order_repo.add_event(
        db, order=order, event_type=OrderEventType.CREATED, to_status=OrderStatus.PENDING,
        source=source, actor_staff_id=actor_staff_id if source == EventSource.HUMAN else None, detail=detail,
    )
    return order


def _slot_moved(db: Session, order: OpeOrder, live: PlanInstance, change: SlotChange) -> None:
    before = {"start": order.scheduled_start.isoformat(), "end": order.scheduled_end.isoformat(),
              "instance_id": order.current_instance_id}
    after = {"start": live.scheduled_start.isoformat(), "end": live.scheduled_end.isoformat(), "instance_id": live.id}
    if order.status == OrderStatus.IN_PROGRESS:
        if change.by_person:
            _refuse_in_progress(order)
        # システムが動かした: 作業中のオーダーはそのまま、矛盾として人の判断を待つ
        _raise_conflict(db, order, change, f"作業中に予定の日時が変わりました（新しい開始: {after['start']}）",
                        _cause(change, before=before, after=after))
        return
    times_changed = (order.scheduled_start, order.scheduled_end) != (live.scheduled_start, live.scheduled_end)
    order.current_instance_id = live.id
    order.occurrence_date = live.occurrence_date
    order.scheduled_start = live.scheduled_start
    order.scheduled_end = live.scheduled_end
    if times_changed:
        source = _source(change)
        ope_order_repo.add_event(
            db, order=order, event_type=OrderEventType.RESCHEDULED, source=source,
            actor_staff_id=change.actor_staff_id if source == EventSource.HUMAN else None,
            reason=change.note if change.by_person else None, detail=_cause(change, before=before, after=after),
        )


def _slot_removed(db: Session, order: OpeOrder, change: SlotChange) -> None:
    if change.by_person:
        if order.status == OrderStatus.IN_PROGRESS:
            _refuse_in_progress(order)
        _cancel(db, order, change, change.note)
    elif order.status in (OrderStatus.PENDING, OrderStatus.READY):
        _cancel(db, order, change, _plan_reason(change))
    else:
        # 保留中・作業中のオーダーは自動で取り消さず、矛盾として人の判断を待つ
        _raise_conflict(db, order, change, f"予定が無くなりました（{_plan_reason(change) or 'plan'}）", _cause(change))


def _cancel(db: Session, order: OpeOrder, change: SlotChange, reason: str | None) -> None:
    source = _source(change)
    from_status = order.status
    order.status = OrderStatus.CANCELED
    order.status_before_hold = None
    ope_order_repo.add_event(
        db, order=order, event_type=OrderEventType.STATUS_CHANGED, from_status=from_status,
        to_status=OrderStatus.CANCELED, source=source,
        actor_staff_id=change.actor_staff_id if source == EventSource.HUMAN else None,
        reason=reason, detail=_cause(change),
    )


def _raise_conflict(db: Session, order: OpeOrder, change: SlotChange, reason: str, detail: dict[str, Any]) -> None:
    order.conflict = True
    order.conflict_reason = reason
    ope_order_repo.add_event(
        db, order=order, event_type=OrderEventType.CONFLICT_RAISED, source=EventSource.SYSTEM, actor_staff_id=None,
        reason=reason, detail=detail,
    )


# ==========================================
# 運用開始時のオーダー作成（scripts/create_orders.py）
# ==========================================
def create_missing_orders(db: Session) -> int:
    """
    今以降に開始する有効な予定のうち、オーダーの無い発生枠にオーダーを作ります（何度実行しても同じ結果）。
    過去の予定にはオーダーを作りません（運用開始より前は plan の履歴のみ）。
    """
    stmt = (
        select(PlanInstance)
        .join(PlanOccurrenceCache, PlanOccurrenceCache.instance_id == PlanInstance.id)
        .where(PlanOccurrenceCache.starts_at >= _now())
        .order_by(PlanInstance.template_id, PlanInstance.scheduled_start)
    )
    created = 0
    for instance in db.scalars(stmt).all():
        slot = slot_key_of(db, instance)
        if _find_order(db, slot) is None and create_order(
            db, slot, instance, EventSource.SYSTEM, None, {"cause": "initial order creation"}
        ):
            created += 1
    db.commit()
    return created
