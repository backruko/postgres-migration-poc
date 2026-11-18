"""
届かなかったアラームの一覧と「確認した」（通知の仕組み N.4b、2026-11-12）。日次ダッシュボードが使う。

- 届かなかった = 再試行の後も、宛先のどれかに届かなかった通知（failed: どこにも / partly_failed: 一部に）
- 職員が「確認した」を押すまで、赤い帯と件数に出続ける（古くても消えない。2026-11-12 の決定）。確認したものは 7 日間、
  一覧に薄く残る
- 確認できるのは、作業者・確認者・リリース担当・業務管理者（閲覧者・監査担当・システム管理者は見るだけ）。自分の範囲の顧客のものだけ
- 再試行中の配信の数と、いちばん新しい失敗の理由も返す（メールサーバーが落ちたことに、約 80 分待たずに気づける）
- 通知のドメインはオーダーの表を読まない: 顧客名・予定日は出来事の写しから
"""
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from src.core.exceptions import BadRequestError, NotFoundError
from src.domain_events.models import DomainEvent
from src.domain_notification.models import (
    DeliveryStatus, Notification, NotificationDelivery, NotificationDestination, NotificationRule, NotificationStatus,
)
from src.domain_notification.schemas import FailedDelivery, FailedNotification, NotificationFailuresResponse
from src.domain_staff.models import StaffUser
from src.domain_staff.scope import ALL_CLIENTS, ClientScope
from src.core.clock import utc_now as _now

FAILED = (NotificationStatus.FAILED, NotificationStatus.PARTLY_FAILED)
KEEP_ACKNOWLEDGED = timedelta(days=7)
LIMIT = 200


def get_failures(db: Session, *, scope: ClientScope = ALL_CLIENTS, now: datetime | None = None, limit: int = LIMIT) -> NotificationFailuresResponse:
    now = now or _now()
    # 範囲のある職員には、顧客のない通知は出ない（IN）。宛先のテスト送信（ルールなし）はアラームではないので数えない（N.5a）
    in_scope = scope.clause(Notification.client_code) & Notification.rule_id.is_not(None)
    unacknowledged = db.scalar(
        select(func.count(Notification.id))
        .where(Notification.status.in_(FAILED), Notification.failure_acknowledged_at.is_(None), in_scope)
    ) or 0
    retrying_rows = db.execute(
        select(NotificationDelivery.last_attempt_at, NotificationDelivery.last_error)
        .join(Notification, Notification.id == NotificationDelivery.notification_id)
        .where(NotificationDelivery.status == DeliveryStatus.RETRYING, in_scope)
        .order_by(NotificationDelivery.last_attempt_at.desc())
    ).all()
    rows = list(db.scalars(
        select(Notification)
        .where(
            Notification.status.in_(FAILED), in_scope,
            (Notification.failure_acknowledged_at.is_(None)) | (Notification.failure_acknowledged_at >= now - KEEP_ACKNOWLEDGED),
        )
        .options(selectinload(Notification.deliveries))
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(limit + 1)
    ))
    items = _describe(db, rows[:limit])
    return NotificationFailuresResponse(
        unacknowledged=unacknowledged,
        retrying=len(retrying_rows),
        last_retry_error=retrying_rows[0].last_error if retrying_rows else None,
        last_retry_at=retrying_rows[0].last_attempt_at if retrying_rows else None,
        items=items,
        truncated=len(rows) > limit,
    )


def acknowledge_failure(db: Session, notification_id: int, *, actor: StaffUser, scope: ClientScope = ALL_CLIENTS) -> FailedNotification:
    """届かなかったアラームを「確認した」にする（もう確認済みなら、最初の確認のまま返す）"""
    notification = db.scalar(select(Notification).where(Notification.id == notification_id).with_for_update())
    not_found = f"Notification(id={notification_id}) not found."
    if notification is None:
        raise NotFoundError(not_found)
    scope.require(notification.client_code, not_found)
    if notification.status not in FAILED or notification.rule_id is None:
        raise BadRequestError("Only alarms that failed to be delivered can be confirmed.")
    if notification.failure_acknowledged_at is None:
        notification.failure_acknowledged_at = _now()
        notification.failure_acknowledged_by_id = actor.id
        db.commit()
    return _describe(db, [notification])[0]


def _describe(db: Session, rows: list[Notification]) -> list[FailedNotification]:
    if not rows:
        return []
    events = {e.id: e for e in db.scalars(select(DomainEvent).where(DomainEvent.id.in_({n.event_id for n in rows})))}
    # 削除したルール・宛先も、そのときの名前で出す（既定の論理削除の絞り込みを外す）
    rule_names = dict(db.execute(
        select(NotificationRule.id, NotificationRule.name).where(NotificationRule.id.in_({n.rule_id for n in rows}))
        .execution_options(include_deleted=True)
    ).all())
    destination_ids = {d.destination_id for n in rows for d in n.deliveries}
    destinations = {d.id: d for d in db.scalars(
        select(NotificationDestination).where(NotificationDestination.id.in_(destination_ids)).execution_options(include_deleted=True)
    )} if destination_ids else {}
    staff_ids = {n.failure_acknowledged_by_id for n in rows if n.failure_acknowledged_by_id}
    staff_names = dict(db.execute(select(StaffUser.id, StaffUser.name).where(StaffUser.id.in_(staff_ids))).all()) if staff_ids else {}

    out = []
    for n in rows:
        event = events[n.event_id]
        payload = event.payload
        occurrence = payload.get("occurrence_date")
        out.append(FailedNotification(
            id=n.id, status=n.status, created_at=n.created_at, completed_at=n.completed_at, event_type=event.event_type,
            client_code=n.client_code, client_name=payload.get("client_name"),
            order_id=event.subject_id if event.subject_type == "order" else None,
            occurrence_date=date.fromisoformat(occurrence) if isinstance(occurrence, str) else None,
            subject=n.subject, rule_id=n.rule_id, rule_name=rule_names.get(n.rule_id, "") if n.rule_id is not None else "",
            deliveries=[
                FailedDelivery(
                    destination_id=d.destination_id, destination_name=destinations[d.destination_id].name,
                    address=destinations[d.destination_id].address, status=d.status, attempts=d.attempts,
                    last_error=d.last_error, sent_at=d.sent_at,
                )
                for d in n.deliveries
            ],
            acknowledged_at=n.failure_acknowledged_at, acknowledged_by_id=n.failure_acknowledged_by_id,
            acknowledged_by_name=staff_names.get(n.failure_acknowledged_by_id) if n.failure_acknowledged_by_id else None,
        ))
    return out
