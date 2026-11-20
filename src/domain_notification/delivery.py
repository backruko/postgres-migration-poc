"""
配信（通知の仕組み）。ワーカーが毎分、振り分けのすぐ後に呼びます。

送る時刻（next_attempt_at）が来た配信を、宛先のチャネルで送り、結果を記録する。知らせない時間帯で待たせた配信も、
時間帯の終わりの時刻が来れば、ここでふつうに送られる（文面はその時刻の事実のまま）。

- 送れた → sent（送った日時・送り先の ID）
- 一時的な失敗 → retrying。次は 1・5・15・60 分後。5 回目も失敗したら failed（約 80 分であきらめる）
- 永続的な失敗（宛先が存在しない など）→ すぐ failed。宛先が無効・削除されていても failed（送らない）
- 宛先がすべて終わったら、通知の状態をまとめる: 全部届いた sent・一部 partly_failed・全部だめ failed（終わった日時も）
- 1 件ずつ、自分のトランザクションで送って記録する（コミットする）。送った後・記録の前にプロセスが止まると、
  その 1 件だけもう一度送られることがある（少なくとも 1 回届ける）。まとめてコミットすると、重複が最大 1 回分の件数になるため
- 1 回ごとに notification_delivery_attempt を 1 行書く（どのアドレスへ・結果・返事）
- 宛先のテスト送信（ルールなしの通知）は再試行しない: 1 回の結果をそのまま見せる
- 1 件ずつ FOR UPDATE SKIP LOCKED で取るので、ワーカーが 2 つ動いても同じ配信を 2 回送らない
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.domain_notification.channels import Channel, SendResult, default_channels
from src.domain_notification.models import (
    DeliveryStatus, Notification, NotificationChannel, NotificationDelivery, NotificationDeliveryAttempt, NotificationDestination,
    NotificationStatus,
)
from src.core.clock import utc_now

MAX_ATTEMPTS = 5
BACKOFF = (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=15), timedelta(minutes=60))  # 1〜4 回目の失敗の後
BATCH_SIZE = 200
DUE = (DeliveryStatus.PENDING, DeliveryStatus.RETRYING)

ERROR_DESTINATION_UNUSABLE = "the destination is disabled or deleted"
ERROR_NO_CHANNEL = "no channel is configured for"


@dataclass
class DeliveryResult:
    sent: int = 0
    retrying: int = 0
    failed: int = 0
    completed: dict[str, int] = field(default_factory=dict)  # 終わった通知の数（状態ごと）


def deliver_due(
    db: Session, *, now: datetime | None = None, batch_size: int = BATCH_SIZE,
    channels: dict[NotificationChannel, Channel] | None = None,
) -> DeliveryResult:
    """送る時刻が来た配信を送る（1 件ごとにコミットする）"""
    now = now or utc_now()
    channels = channels if channels is not None else default_channels()
    result = DeliveryResult()
    # 部分インデックス ix_notification_delivery_due で引く。ここではロックしない（1 件ずつ取り直してロックする）
    ids = list(db.scalars(
        select(NotificationDelivery.id)
        .where(NotificationDelivery.status.in_(DUE), NotificationDelivery.next_attempt_at <= now)
        .order_by(NotificationDelivery.next_attempt_at, NotificationDelivery.id)
        .limit(batch_size)
    ))
    db.rollback()
    for delivery_id in ids:
        _deliver_one(db, delivery_id, now, channels, result)
    return result


def _deliver_one(
    db: Session, delivery_id: int, now: datetime, channels: dict[NotificationChannel, Channel], result: DeliveryResult,
) -> None:
    delivery = db.scalar(
        select(NotificationDelivery)
        .where(NotificationDelivery.id == delivery_id, NotificationDelivery.status.in_(DUE),
               NotificationDelivery.next_attempt_at <= now)
        .with_for_update(skip_locked=True)
    )
    if delivery is None:  # ほかのワーカーが取った・もう送った
        db.rollback()
        return
    # 宛先は論理削除されていても読む（既定の絞り込みでは消えてしまう）
    destination = db.scalar(
        select(NotificationDestination).where(NotificationDestination.id == delivery.destination_id)
        .execution_options(include_deleted=True)
    )
    assert destination is not None  # 配信が指す宛先は消せない（外部キー RESTRICT・論理削除だけ）
    notification = delivery.notification

    if not destination.enabled or destination.is_deleted:
        outcome = SendResult(ok=False, error=ERROR_DESTINATION_UNUSABLE, permanent=True)
    elif destination.channel not in channels:
        outcome = SendResult(ok=False, error=f"{ERROR_NO_CHANNEL} {destination.channel.value}", permanent=False)
    else:
        try:
            outcome = channels[destination.channel].send(destination, notification)
        except Exception as error:  # アダプターの不具合・通信の例外は、一時的な失敗として扱う
            outcome = SendResult(ok=False, error=f"{type(error).__name__}: {error}", permanent=False)

    delivery.attempts += 1
    delivery.last_attempt_at = now
    # 1 回ごとの記録（このときのアドレス。アドレスを直した後の再試行は、新しいアドレスになる）
    db.add(NotificationDeliveryAttempt(
        delivery_id=delivery.id, attempt_no=delivery.attempts, attempted_at=now, address=destination.address, ok=outcome.ok,
        permanent=bool(not outcome.ok and outcome.permanent),
        error=None if outcome.ok else (outcome.error or "unknown error")[:2000], provider_message_id=outcome.provider_message_id,
    ))
    if outcome.ok:
        delivery.status, delivery.sent_at, delivery.next_attempt_at = DeliveryStatus.SENT, now, None
        delivery.provider_message_id = outcome.provider_message_id
        result.sent += 1
    else:
        delivery.last_error = (outcome.error or "unknown error")[:2000]
        # テスト送信（ルールなし）は 1 回だけ: 頼んだ人に、すぐ結果を見せる
        if outcome.permanent or delivery.attempts >= MAX_ATTEMPTS or notification.rule_id is None:
            delivery.status, delivery.next_attempt_at = DeliveryStatus.FAILED, None
            result.failed += 1
        else:
            delivery.status = DeliveryStatus.RETRYING
            delivery.next_attempt_at = now + BACKOFF[delivery.attempts - 1]
            result.retrying += 1
    db.flush()
    completed = _complete_notification(db, notification.id, now)
    if completed:
        result.completed[completed.value] = result.completed.get(completed.value, 0) + 1
    db.commit()


def _complete_notification(db: Session, notification_id: int, now: datetime) -> NotificationStatus | None:
    """宛先がすべて終わったら、通知の状態をまとめる（通知の行をロックして、2 つのワーカーが同時にまとめても食い違わない）"""
    notification = db.scalar(select(Notification).where(Notification.id == notification_id).with_for_update())
    assert notification is not None
    if notification.status != NotificationStatus.PENDING:
        return None
    statuses = list(db.scalars(select(NotificationDelivery.status).where(NotificationDelivery.notification_id == notification_id)))
    if any(s in DUE for s in statuses):
        return None
    sent = sum(s == DeliveryStatus.SENT for s in statuses)
    notification.status = (
        NotificationStatus.SENT if sent == len(statuses)
        else NotificationStatus.FAILED if sent == 0
        else NotificationStatus.PARTLY_FAILED
    )
    notification.completed_at = now
    return notification.status
