"""
オーダーの状態の操作（4.4）。どの操作もオーダーを行ロックしてから状態を確かめ、記録（ope_order_event）を1件追記します。

    pending ──release──▶ ready ──start──▶ in_progress ──finish──▶ finished
       │                   │                  │
       └───────hold────────┴──────hold────────┘ ──▶ on_hold ──resume──▶ 保留前の状態
    （finished / canceled 以外は cancel で canceled。cancel は plan の予定も取り消す）

- start は予定日（日本時間）以降のみ。それより前はリリース担当の承認（approve_early_start）が必要。
- 矛盾フラグのあるオーダーは、解決（resolve_conflict）するまでリリース・開始できない。
- 誰が操作できるかはルーターの @require_roles で決める（ここでは判定しない）。
"""
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from src.core.exceptions import BadRequestError, DuplicatedRecordError, NotFoundError
from src.domain_biz.models import BizMaster
from src.domain_ope.models import EventSource, OpeOrder, OrderEventType, OrderStatus
from src.domain_ope.schemas import (
    AutoReleaseResult, ConflictAction, ConflictResolveIn, OrderDetailResponse, OrderEventResponse,
)
from src.domain_plan import services as plan_services
from src.domain_plan.schemas import PlanInstanceCancel
from src.domain_plan.slot_events import SlotKey, live_instance_for_slot
from src.infrastructure.repositories import ope_order_repo, plan_instance_repo

# 予定日の判定に使うタイムゾーン（シートの日付・休日と同じく日本の日付）
LOCAL_TIMEZONE = ZoneInfo("Asia/Tokyo")

# 保留にできる状態・取り消せる状態（finished / canceled は最終状態）
HOLDABLE = (OrderStatus.PENDING, OrderStatus.READY, OrderStatus.IN_PROGRESS)
CANCELABLE = (*HOLDABLE, OrderStatus.ON_HOLD)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _local_today() -> date:
    return _now().astimezone(LOCAL_TIMEZONE).date()


def _scheduled_day(order: OpeOrder) -> date:
    return order.scheduled_start.astimezone(LOCAL_TIMEZONE).date()


# ==========================================
# 参照
# ==========================================
def _detail(db: Session, order: OpeOrder) -> OrderDetailResponse:
    events = ope_order_repo.get_events(db, order_id=order.id)
    return OrderDetailResponse.model_validate(
        {
            **{c.key: getattr(order, c.key) for c in OpeOrder.__table__.columns},
            "early_start_approved": any(e.event_type == OrderEventType.EARLY_START_APPROVED for e in events),
            "events": [OrderEventResponse.model_validate(e) for e in events],
        }
    )


def get_order(db: Session, order_id: int) -> OrderDetailResponse:
    order = db.get(OpeOrder, order_id)
    if not order:
        raise NotFoundError(f"Order(id={order_id}) not found.")
    return _detail(db, order)


# ==========================================
# 状態の操作
# ==========================================
def _get_locked(db: Session, order_id: int) -> OpeOrder:
    order = ope_order_repo.get_locked(db, id=order_id)
    if not order:
        raise NotFoundError(f"Order(id={order_id}) not found.")
    return order


def _require_status(order: OpeOrder, allowed: tuple[OrderStatus, ...], action: str) -> None:
    if order.status not in allowed:
        raise BadRequestError(
            f"Cannot {action} order {order.id}: it is {order.status.value} "
            f"(allowed: {', '.join(s.value for s in allowed)})."
        )


def _require_no_conflict(order: OpeOrder, action: str) -> None:
    if order.conflict:
        raise BadRequestError(f"Cannot {action} order {order.id} while it has a conflict; resolve it first.")


def _transition(
    db: Session, order: OpeOrder, to: OrderStatus, actor_staff_id: int, reason: str | None,
    detail: dict[str, Any] | None = None,
) -> None:
    from_status = order.status
    order.status = to
    ope_order_repo.add_event(
        db, order=order, event_type=OrderEventType.STATUS_CHANGED, from_status=from_status, to_status=to,
        source=EventSource.HUMAN, actor_staff_id=actor_staff_id, reason=reason, detail=detail,
    )


def _finish(db: Session, order: OpeOrder) -> OrderDetailResponse:
    db.commit()
    db.refresh(order)
    return _detail(db, order)


def _release(
    db: Session, order: OpeOrder, source: EventSource, actor_staff_id: int | None, reason: str | None,
    detail: dict[str, Any] | None = None,
) -> None:
    """pending → ready。手動・自動のリリースで共通（4.6 で SOP バージョンの固定もここで行う）"""
    order.released_at = _now()
    order.status = OrderStatus.READY
    ope_order_repo.add_event(
        db, order=order, event_type=OrderEventType.STATUS_CHANGED, from_status=OrderStatus.PENDING,
        to_status=OrderStatus.READY, source=source, actor_staff_id=actor_staff_id, reason=reason, detail=detail,
    )


def release_order(db: Session, order_id: int, actor_staff_id: int, comment: str | None = None) -> OrderDetailResponse:
    """pending → ready（リリース担当・業務管理者。自動リリースの業務でも手動でリリースできる）"""
    order = _get_locked(db, order_id)
    _require_status(order, (OrderStatus.PENDING,), "release")
    _require_no_conflict(order, "release")
    _release(db, order, EventSource.HUMAN, actor_staff_id, comment)
    return _finish(db, order)


# ==========================================
# 自動リリース（scripts/release_orders.py から15分ごとに実行）
# ==========================================
# 対象の絞り込み: リリースは最大で予定日の30日前（release_days_before の上限）
MAX_RELEASE_LEAD = timedelta(days=31)


def release_moment(order: OpeOrder, biz: BizMaster) -> datetime:
    """自動リリースの日時 = 予定日（日本時間）の release_days_before 日前の release_time（日本時間）"""
    day = _scheduled_day(order) - timedelta(days=biz.release_days_before)
    return datetime.combine(day, biz.release_time, tzinfo=LOCAL_TIMEZONE)


def release_due_orders(db: Session) -> AutoReleaseResult:
    """
    自動リリースの業務の pending オーダーのうち、リリース日時を過ぎたものを ready にします（システムの記録）。
    矛盾のあるオーダーは飛ばします（人が解決してから）。止まっていた間の分もまとめてリリースします。
    何度実行しても同じ結果になります（手動でリリース済み・取消済みのものは対象外）。
    """
    now = _now()
    candidates = ope_order_repo.get_pending_automatic(db, starting_before=now + MAX_RELEASE_LEAD)
    released = []
    for order, biz in candidates:
        moment = release_moment(order, biz)
        if moment <= now:
            _release(db, order, EventSource.SYSTEM, None, None, {"cause": "automatic release", "release_at": moment.isoformat()})
            released.append(order.id)
    db.commit()
    return AutoReleaseResult(checked=len(candidates), released=released)


def approve_early_start(db: Session, order_id: int, actor_staff_id: int, comment: str | None = None) -> OrderDetailResponse:
    """予定日より前の開始を承認します（リリース担当）。承認後は予定日前でも start できます。"""
    order = _get_locked(db, order_id)
    _require_status(order, (OrderStatus.PENDING, OrderStatus.READY), "approve an early start for")
    if _scheduled_day(order) <= _local_today():
        raise BadRequestError(f"Order {order.id} is scheduled for today or earlier; no approval is needed to start it.")
    if ope_order_repo.has_event(db, order_id=order.id, event_type=OrderEventType.EARLY_START_APPROVED):
        raise DuplicatedRecordError(f"An early start of order {order.id} has already been approved.")
    ope_order_repo.add_event(
        db, order=order, event_type=OrderEventType.EARLY_START_APPROVED, source=EventSource.HUMAN,
        actor_staff_id=actor_staff_id, reason=comment, detail={"scheduled_day": _scheduled_day(order).isoformat()},
    )
    return _finish(db, order)


def start_order(db: Session, order_id: int, actor_staff_id: int, comment: str | None = None) -> OrderDetailResponse:
    """ready → in_progress（作業者）。予定日（日本時間）以降のみ。それより前はリリース担当の承認が必要。"""
    order = _get_locked(db, order_id)
    _require_status(order, (OrderStatus.READY,), "start")
    _require_no_conflict(order, "start")
    early = _scheduled_day(order) > _local_today()
    if early and not ope_order_repo.has_event(db, order_id=order.id, event_type=OrderEventType.EARLY_START_APPROVED):
        raise BadRequestError(
            f"Order {order.id} is scheduled for {_scheduled_day(order).isoformat()}; "
            "starting before that day needs a releaser's approval."
        )
    _transition(db, order, OrderStatus.IN_PROGRESS, actor_staff_id, comment, {"early_start": True} if early else None)
    return _finish(db, order)


def hold_order(db: Session, order_id: int, actor_staff_id: int, reason: str) -> OrderDetailResponse:
    """pending / ready / in_progress → on_hold（日付なし。再開すると保留前の状態に戻る）"""
    order = _get_locked(db, order_id)
    _require_status(order, HOLDABLE, "hold")
    order.status_before_hold = order.status
    _transition(db, order, OrderStatus.ON_HOLD, actor_staff_id, reason)
    return _finish(db, order)


def resume_order(db: Session, order_id: int, actor_staff_id: int, comment: str | None = None) -> OrderDetailResponse:
    """on_hold → 保留前の状態"""
    order = _get_locked(db, order_id)
    _require_status(order, (OrderStatus.ON_HOLD,), "resume")
    assert order.status_before_hold is not None  # CHECK 制約で保証
    back_to = order.status_before_hold
    order.status_before_hold = None
    _transition(db, order, back_to, actor_staff_id, comment)
    return _finish(db, order)


def finish_order(db: Session, order_id: int, actor_staff_id: int, comment: str | None = None) -> OrderDetailResponse:
    """in_progress → finished（作業者。ステップ（Phase 5）ができるまでの暫定の完了操作）"""
    order = _get_locked(db, order_id)
    _require_status(order, (OrderStatus.IN_PROGRESS,), "finish")
    _transition(db, order, OrderStatus.FINISHED, actor_staff_id, comment)
    return _finish(db, order)


def _cancel_plan_instance(db: Session, order: OpeOrder, actor_staff_id: int, reason: str) -> bool:
    """
    オーダーの plan の予定がまだ有効なら取り消します（カレンダーからも消え、同じ枠に新しいオーダーが作られないように）。
    オーダーを先に取消済みにしてから呼ぶため、plan の変更の受け手（order_sync）は何もしません。
    plan の取消はコミットまで行います。
    """
    found = plan_instance_repo.get_with_void(db, id=order.current_instance_id)
    if found is None or found[1] is not None:
        return False  # 枠はすでに消えている
    plan_services.cancel_instance(db, order.current_instance_id, PlanInstanceCancel(note=reason), actor_staff_id=actor_staff_id)
    return True


def cancel_order(db: Session, order_id: int, actor_staff_id: int, reason: str) -> OrderDetailResponse:
    """finished / canceled 以外 → canceled（リリース担当・業務管理者。理由必須。plan の予定も取り消す）"""
    order = _get_locked(db, order_id)
    _require_status(order, CANCELABLE, "cancel")
    order.status_before_hold = None
    order.conflict = False
    order.conflict_reason = None
    _transition(db, order, OrderStatus.CANCELED, actor_staff_id, reason)
    db.flush()
    if not _cancel_plan_instance(db, order, actor_staff_id, reason):
        db.commit()
    db.refresh(order)
    return _detail(db, order)


def _slot_of(order: OpeOrder) -> SlotKey:
    if order.source_sheet_event_id is not None:
        return SlotKey(order.template_id, "sheet_event", order.source_sheet_event_id, order.occurrence_date)
    if order.one_off_instance_id is not None:
        return SlotKey(order.template_id, "one_off", order.one_off_instance_id, order.occurrence_date)
    return SlotKey(order.template_id, "date", order.occurrence_date, order.occurrence_date)


def resolve_conflict(db: Session, order_id: int, actor_staff_id: int, resolve_in: ConflictResolveIn) -> OrderDetailResponse:
    """
    矛盾を解決します（リリース担当・業務管理者）。
    - accept_plan: plan の新しい日時を採用（plan の予定が無くなっていた場合はオーダーを取消）
    - keep: オーダーをそのまま続ける（フラグだけ外す）
    - cancel: オーダーを取り消す（plan の予定がまだあれば、それも取り消す。理由必須）
    """
    if resolve_in.action == ConflictAction.CANCEL and not resolve_in.reason:
        raise BadRequestError("A reason is required to cancel an order.")
    order = _get_locked(db, order_id)
    if not order.conflict:
        raise BadRequestError(f"Order {order.id} has no conflict to resolve.")

    previous_reason = order.conflict_reason
    order.conflict = False
    order.conflict_reason = None
    detail: dict[str, Any] = {"action": resolve_in.action.value, "conflict_reason": previous_reason}
    cancel = resolve_in.action == ConflictAction.CANCEL
    if resolve_in.action == ConflictAction.ACCEPT_PLAN:
        live = live_instance_for_slot(db, _slot_of(order))
        if live is None:
            cancel = True  # plan の予定が無くなっていた → 採用 = 取消
        else:
            detail["before"] = {"start": order.scheduled_start.isoformat(), "end": order.scheduled_end.isoformat()}
            detail["after"] = {"start": live.scheduled_start.isoformat(), "end": live.scheduled_end.isoformat()}
            order.current_instance_id = live.id
            order.occurrence_date = live.occurrence_date
            order.scheduled_start = live.scheduled_start
            order.scheduled_end = live.scheduled_end

    from_status = order.status
    if cancel:
        order.status = OrderStatus.CANCELED
        order.status_before_hold = None
    ope_order_repo.add_event(
        db, order=order, event_type=OrderEventType.CONFLICT_RESOLVED, source=EventSource.HUMAN,
        actor_staff_id=actor_staff_id, reason=resolve_in.reason, detail=detail,
        from_status=from_status if cancel else None, to_status=OrderStatus.CANCELED if cancel else None,
    )
    db.flush()
    if not (cancel and _cancel_plan_instance(db, order, actor_staff_id, resolve_in.reason or "conflict resolved")):
        db.commit()
    db.refresh(order)
    return _detail(db, order)
