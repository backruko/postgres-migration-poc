"""
顧客側の出来事の信号（7.3）。POST /ope/signals で受け取った信号をすべて ope_signal に残し、オーダーと照合する。

照合: 顧客コード + 信号の種類 → 信号で完了する業務（AUTOMATIC。顧客ごとに一意）→ key（ルール上の日付 YYYY-MM-DD）の
取消されていないオーダー。
- 未完了のオーダーが1件で ready / 作業中 → 完了にする（システムの記録。detail に信号の ID）= matched
- 未完了が無く完了済みがある → duplicate（前の信号ですでに完了。何もしない）
- 未完了が複数 → ambiguous。業務・オーダーが無い・日付でない・未リリース・保留中・矛盾あり → unmatched（理由付き）
照合できなかった信号は人が対応する（handle_signal: オーダーを指定して完了にするか、対応済みにするだけ）。
"""
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.exceptions import BadRequestError, NotFoundError
from src.domain_biz.models import BizMaster, CompletionMode
from src.domain_ope.models import EventSource, OpeOrder, OpeSignal, OrderEventType, OrderStatus, SignalResult
from src.domain_ope.schemas import SignalHandleIn, SignalIn, SignalResponse
from src.infrastructure.repositories import ope_order_repo

FINISHABLE = (OrderStatus.READY, OrderStatus.IN_PROGRESS)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _match(db: Session, signal_type: str, client_code: str, key: str) -> tuple[SignalResult, OpeOrder | None, str | None]:
    """(結果, 対応したオーダー, 説明)。オーダーは行ロックして返す"""
    biz = db.scalars(select(BizMaster).where(
        BizMaster.client_code == client_code, BizMaster.signal_type == signal_type,
        BizMaster.completion_mode == CompletionMode.AUTOMATIC,
    )).first()
    if biz is None:
        return SignalResult.UNMATCHED, None, f"no business of client {client_code} completes by signal type '{signal_type}'"
    try:
        day = date.fromisoformat(key)
    except ValueError:
        return SignalResult.UNMATCHED, None, f"key '{key}' is not a date (YYYY-MM-DD)"
    orders = list(db.scalars(
        select(OpeOrder)
        .where(OpeOrder.biz_master_id == biz.id, OpeOrder.occurrence_date == day, OpeOrder.status != OrderStatus.CANCELED)
        .order_by(OpeOrder.id)
        .with_for_update()
    ))
    if not orders:
        return SignalResult.UNMATCHED, None, f"no open order of {biz.business_code} for {day.isoformat()}"
    open_orders = [o for o in orders if o.status != OrderStatus.FINISHED]
    if not open_orders:
        return SignalResult.DUPLICATE, orders[-1], f"order {orders[-1].id} was already finished"
    if len(open_orders) > 1:
        ids = ", ".join(str(o.id) for o in open_orders)
        return SignalResult.AMBIGUOUS, None, f"{len(open_orders)} open orders of {biz.business_code} for {day.isoformat()} ({ids})"
    order = open_orders[0]
    if order.conflict:
        return SignalResult.UNMATCHED, None, f"order {order.id} has a conflict ({order.conflict_reason})"
    if order.status == OrderStatus.ON_HOLD:
        return SignalResult.UNMATCHED, None, f"order {order.id} is on hold"
    if order.status == OrderStatus.PENDING:
        return SignalResult.UNMATCHED, None, f"order {order.id} is not released yet"
    return SignalResult.MATCHED, order, None


def finish_by_signal(
    db: Session, order: OpeOrder, signal: OpeSignal, source: EventSource, actor_staff_id: int | None,
    reason: str | None = None,
) -> None:
    """ready / 作業中のオーダーを完了にする（信号の ID を記録に残す）"""
    from_status = order.status
    order.status = OrderStatus.FINISHED
    detail: dict[str, Any] = {"cause": "signal" if source == EventSource.SYSTEM else "signal handled", "signal_id": signal.id}
    ope_order_repo.add_event(
        db, order=order, event_type=OrderEventType.STATUS_CHANGED, from_status=from_status, to_status=OrderStatus.FINISHED,
        source=source, actor_staff_id=actor_staff_id, reason=reason, detail=detail,
    )


def receive_signal(db: Session, signal_in: SignalIn, sender_staff_id: int) -> SignalResponse:
    """信号を受け取って残し、照合する（照合できなくても捨てない）。"""
    client_code = signal_in.client_code.upper()
    result, order, note = _match(db, signal_in.type, client_code, signal_in.key)
    signal = OpeSignal(
        signal_type=signal_in.type, client_code=client_code, key=signal_in.key, received_at=signal_in.received_at,
        sender_staff_id=sender_staff_id, payload=signal_in.payload, result=result,
        matched_order_id=order.id if order else None, result_note=note,
    )
    db.add(signal)
    db.flush()
    if result == SignalResult.MATCHED and order is not None:
        finish_by_signal(db, order, signal, EventSource.SYSTEM, None)
    db.commit()
    db.refresh(signal)
    return SignalResponse.model_validate(signal)


def handle_signal(db: Session, signal_id: int, actor_staff_id: int, handle_in: SignalHandleIn) -> SignalResponse:
    """
    照合できなかった信号に人が対応する（作業者・リリース担当・業務管理者）。
    order_id を指定すると、その顧客の ready / 作業中のオーダーを完了にしてから対応済みにする。指定しなければ対応済みにするだけ。
    """
    signal = db.scalars(select(OpeSignal).where(OpeSignal.id == signal_id).with_for_update()).first()
    if signal is None:
        raise NotFoundError(f"Signal(id={signal_id}) not found.")
    if signal.result not in (SignalResult.UNMATCHED, SignalResult.AMBIGUOUS):
        raise BadRequestError(f"Signal {signal.id} is {signal.result.value}; only unmatched / ambiguous signals are handled.")
    if signal.handled_at is not None:
        raise BadRequestError(f"Signal {signal.id} was already handled.")
    if handle_in.order_id is not None:
        order = ope_order_repo.get_locked(db, id=handle_in.order_id)
        if order is None or order.client_code != signal.client_code:
            raise NotFoundError(f"Order(id={handle_in.order_id}) of client {signal.client_code} not found.")
        if order.status not in FINISHABLE:
            raise BadRequestError(
                f"Order {order.id} is {order.status.value}; only ready / in-progress orders can be finished by a signal."
            )
        finish_by_signal(db, order, signal, EventSource.HUMAN, actor_staff_id, handle_in.note)
    signal.handled_at = _now()
    signal.handled_by_staff_id = actor_staff_id
    signal.handled_note = handle_in.note
    db.commit()
    db.refresh(signal)
    return SignalResponse.model_validate(signal)
