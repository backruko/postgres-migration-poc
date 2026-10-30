# src/infrastructure/repositories/Ope.py
"""domain_ope 用のリポジトリ群。commit は呼び出し側（Service 層・plan のコミット）で行います。"""
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.domain_ope.models import EventSource, OpeOrder, OpeOrderEvent, OrderEventType, OrderStatus


# ==========================================
#  OpeOrder Repository
# ==========================================
class OpeOrderRepository:

    def get_active_for_slot(
        self,
        db: Session,
        *,
        template_id: int,
        occurrence_date: date,
        sheet_event_id: int | None,
        one_off_instance_id: int | None,
    ) -> OpeOrder | None:
        """発生枠の、取消されていないオーダー（1つの枠に最大1件）"""
        stmt = select(OpeOrder).where(OpeOrder.template_id == template_id, OpeOrder.status != OrderStatus.CANCELED)
        if sheet_event_id is not None:
            stmt = stmt.where(OpeOrder.source_sheet_event_id == sheet_event_id)
        elif one_off_instance_id is not None:
            stmt = stmt.where(OpeOrder.one_off_instance_id == one_off_instance_id)
        else:
            stmt = stmt.where(
                OpeOrder.occurrence_date == occurrence_date,
                OpeOrder.source_sheet_event_id.is_(None),
                OpeOrder.one_off_instance_id.is_(None),
            )
        return db.scalars(stmt.with_for_update()).first()

    def get_events(self, db: Session, *, order_id: int) -> list[OpeOrderEvent]:
        stmt = select(OpeOrderEvent).where(OpeOrderEvent.order_id == order_id).order_by(OpeOrderEvent.id)
        return list(db.scalars(stmt).all())

    def add_event(
        self,
        db: Session,
        *,
        order: OpeOrder,
        event_type: OrderEventType,
        source: EventSource,
        actor_staff_id: int | None,
        from_status: OrderStatus | None = None,
        to_status: OrderStatus | None = None,
        reason: str | None = None,
        detail: dict[str, Any] | None = None,
        occurred_at: datetime | None = None,
    ) -> OpeOrderEvent:
        event = OpeOrderEvent(
            order_id=order.id, event_type=event_type, source=source, actor_staff_id=actor_staff_id,
            from_status=from_status, to_status=to_status, reason=reason, detail=detail,
        )
        if occurred_at is not None:
            event.occurred_at = occurred_at
        db.add(event)
        return event


# ==========================================
#  インスタンス化とエクスポート
# ==========================================
ope_order_repo = OpeOrderRepository()
