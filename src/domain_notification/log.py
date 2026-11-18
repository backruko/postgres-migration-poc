"""
通知の記録（アラームの記録。通知の仕組み N.5b、2026-11-12）。出来事のページの 2 つ目のタブ「通知」が使う。

出来事 → 通知 → 配信 → 1 回ごとの試み を、1 か所でたどれるように（QA・運用の確認。2026-11-12 の決定）。
- 新しい順。状態・種類（アラーム / テスト送信）・顧客・ルール・出来事・オーダー・期間で絞れる
- 出来事から通知へのつながりは、ここ（event_id で絞る）。出来事の側は通知を知らない（依存は一方向）
- 顧客の範囲がある職員には、その顧客のものだけ（顧客のないもの = 全顧客の宛先のテスト送信は出ない）
- 削除したルール・宛先も、そのときの名前で出す。顧客名・オーダーは出来事の写しから（オーダーの表は読まない）
"""
from datetime import datetime

from sqlalchemy import Select, select
from sqlalchemy.orm import Session, selectinload

from src.core.exceptions import BadRequestError, NotFoundError
from src.domain_events.models import DomainEvent
from src.domain_notification.models import (
    Notification, NotificationDelivery, NotificationDestination, NotificationRule, NotificationStatus,
)
from src.domain_notification.schemas import (
    AttemptOut, LogDelivery, LogEntry, LogEntryDetail, NotificationLogResponse,
)
from src.domain_staff.models import StaffUser
from src.domain_staff.scope import ALL_CLIENTS, ClientScope
from src.core.clock import assume_local

KINDS = ("alarm", "test")


def list_log(
    db: Session,
    *,
    status: NotificationStatus | None = None,
    kind: str | None = None,
    client_code: str | None = None,
    rule_id: int | None = None,
    event_id: int | None = None,
    order_id: int | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    limit: int = 100,
    scope: ClientScope = ALL_CLIENTS,
) -> NotificationLogResponse:
    """通知の記録（作った日時の新しい順）。created_from 以上・created_to 未満。タイムゾーンのない日時は日本時間"""
    created_from, created_to = assume_local(created_from), assume_local(created_to)
    _check_log_filters(kind, created_from, created_to)
    stmt = select(Notification).where(scope.clause(Notification.client_code))
    stmt = _where_notification(stmt, status=status, kind=kind, client_code=client_code, rule_id=rule_id, event_id=event_id)
    stmt = _where_order_and_period(stmt, order_id=order_id, created_from=created_from, created_to=created_to)
    rows = list(db.scalars(
        stmt.options(selectinload(Notification.deliveries).selectinload(NotificationDelivery.attempt_log))
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(limit + 1)
    ))
    return NotificationLogResponse(items=_describe(db, rows[:limit]), truncated=len(rows) > limit)


def _check_log_filters(kind: str | None, created_from: datetime | None, created_to: datetime | None) -> None:
    """期間（始めが終わりより前）、次に種類（alarm / test）を確かめる"""
    if created_from and created_to and created_from >= created_to:
        raise BadRequestError("'from' must be earlier than 'to'.")
    if kind is not None and kind not in KINDS:
        raise BadRequestError(f"kind must be one of {', '.join(KINDS)}.")


def _where_notification(
    stmt: Select, *, status: NotificationStatus | None, kind: str | None, client_code: str | None,
    rule_id: int | None, event_id: int | None,
) -> Select:
    """通知そのものの項目での絞り込み（指定のあるものだけ。すべて AND）。テスト送信 = ルールのない通知"""
    if status is not None:
        stmt = stmt.where(Notification.status == status)
    if kind == "alarm":
        stmt = stmt.where(Notification.rule_id.is_not(None))
    elif kind == "test":
        stmt = stmt.where(Notification.rule_id.is_(None))
    if client_code:
        stmt = stmt.where(Notification.client_code == client_code.strip().upper())
    if rule_id is not None:
        stmt = stmt.where(Notification.rule_id == rule_id)
    if event_id is not None:
        stmt = stmt.where(Notification.event_id == event_id)
    return stmt


def _where_order_and_period(
    stmt: Select, *, order_id: int | None, created_from: datetime | None, created_to: datetime | None,
) -> Select:
    """オーダー（出来事の対象から。オーダーの表は読まない）と、作った日時の期間 [from, to) での絞り込み"""
    if order_id is not None:
        stmt = stmt.join(DomainEvent, DomainEvent.id == Notification.event_id).where(
            DomainEvent.subject_type == "order", DomainEvent.subject_id == order_id,
        )
    if created_from:
        stmt = stmt.where(Notification.created_at >= created_from)
    if created_to:
        stmt = stmt.where(Notification.created_at < created_to)
    return stmt


def get_log_entry(db: Session, notification_id: int, *, scope: ClientScope = ALL_CLIENTS) -> LogEntryDetail:
    """通知 1 件（本文・リンクも）。範囲の外は 404"""
    notification = db.scalar(
        select(Notification).where(Notification.id == notification_id)
        .options(selectinload(Notification.deliveries).selectinload(NotificationDelivery.attempt_log))
    )
    not_found = f"Notification(id={notification_id}) not found."
    if notification is None:
        raise NotFoundError(not_found)
    scope.require(notification.client_code, not_found)
    [entry] = _describe(db, [notification])
    return LogEntryDetail(**entry.model_dump(), body=notification.body, link=notification.link)


def _describe(db: Session, rows: list[Notification]) -> list[LogEntry]:
    if not rows:
        return []
    events = {e.id: e for e in db.scalars(select(DomainEvent).where(DomainEvent.id.in_({n.event_id for n in rows})))}
    rule_ids = {n.rule_id for n in rows if n.rule_id is not None}
    rule_names = {r.id: r.name for r in db.execute(
        select(NotificationRule.id, NotificationRule.name).where(NotificationRule.id.in_(rule_ids)).execution_options(include_deleted=True)
    )} if rule_ids else {}
    destination_ids = {d.destination_id for n in rows for d in n.deliveries}
    destinations = {d.id: d for d in db.scalars(
        select(NotificationDestination).where(NotificationDestination.id.in_(destination_ids)).execution_options(include_deleted=True)
    )} if destination_ids else {}
    staff_ids = {n.failure_acknowledged_by_id for n in rows if n.failure_acknowledged_by_id}
    staff = {s.id: s.name for s in db.execute(select(StaffUser.id, StaffUser.name).where(StaffUser.id.in_(staff_ids)))} if staff_ids else {}

    out = []
    for n in rows:
        event = events[n.event_id]
        client_name = event.payload.get("client_name")
        out.append(LogEntry(
            id=n.id, created_at=n.created_at, completed_at=n.completed_at, status=n.status, suppressed_reason=n.suppressed_reason,
            is_test=n.rule_id is None, event_id=n.event_id, event_type=event.event_type, client_code=n.client_code,
            client_name=client_name if isinstance(client_name, str) else None,
            order_id=event.subject_id if event.subject_type == "order" else None, subject=n.subject,
            rule_id=n.rule_id, rule_name=rule_names.get(n.rule_id) if n.rule_id is not None else None,
            deliveries=[
                LogDelivery(
                    destination_id=d.destination_id, destination_name=destinations[d.destination_id].name,
                    address=destinations[d.destination_id].address, status=d.status, attempts=d.attempts,
                    next_attempt_at=d.next_attempt_at, sent_at=d.sent_at, last_error=d.last_error,
                    attempt_log=[
                        AttemptOut(attempt_no=a.attempt_no, attempted_at=a.attempted_at, address=a.address, ok=a.ok,
                                   permanent=a.permanent, error=a.error, provider_message_id=a.provider_message_id)
                        for a in d.attempt_log
                    ],
                )
                for d in n.deliveries
            ],
            acknowledged_at=n.failure_acknowledged_at,
            acknowledged_by_name=staff.get(n.failure_acknowledged_by_id) if n.failure_acknowledged_by_id else None,
        ))
    return out
