# tests/test_notification_delivery.py
"""
配信（通知の仕組み）のテスト（偽物のチャネル）。
- 送れた → sent（送った日時・送り先の ID）。宛先がすべて終わると通知の状態をまとめる（sent / partly_failed / failed）
- 一時的な失敗は 1・5・15・60 分後にもう一度、5 回目で failed。永続的な失敗・無効な宛先はすぐ failed
- チャネルの例外・チャネルがないときは一時的な失敗
- 知らせない時間帯で待たせた配信は、時間帯の終わりに送られる
配信は 1 件ごとにコミットするので、テスト全体を外側のトランザクションで包み、終わりに取り消します。
"""
import uuid
from datetime import date, datetime, time, timedelta, UTC
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from tests.client_codes import new_client_code
from src.domain_biz.schemas import BizMasterCreate
from src.domain_client.schemas import ClientCreate
from src.domain_events.formats import EventType, OrderDeadlineV1
from src.domain_events.publish import publish
from src.domain_notification import services
from src.domain_notification.channels import FakeChannel, SendResult
from src.domain_notification.delivery import ERROR_DESTINATION_UNUSABLE, deliver_due
from src.domain_notification.dispatcher import dispatch_events
from src.domain_notification.models import (
    DeliveryStatus, Notification, NotificationChannel, NotificationDelivery, NotificationDestination, NotificationStatus,
    QuietMode,
)
from src.domain_notification.templates import ORDER_DEADLINE
from src.infrastructure.database import SessionLocal, engine
from src.infrastructure.repositories import biz_master_repo, client_repo

JST = ZoneInfo("Asia/Tokyo")
NOON = datetime(2026, 11, 12, 12, 0, tzinfo=JST)
MIN = timedelta(minutes=1)


@pytest.fixture
def db():
    """コミットは外側のトランザクションのセーブポイントの中だけ。終わりにすべて取り消す"""
    connection = engine.connect()
    outer = connection.begin()
    session = SessionLocal(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        connection.close()


@pytest.fixture
def client(db):
    code = new_client_code("O")
    client_repo.create(db, obj_in=ClientCreate(code=code, name="配信の顧客"))
    biz = biz_master_repo.create(db, obj_in=BizMasterCreate(client_code=code, name="配信の業務", business_seq=1, manager_email=None, description=None))
    return {"code": code, "biz": biz.id, "biz_code": biz.business_code}


def notification_for(db, client, addresses: list[str], now: datetime = NOON, **rule_kwargs) -> Notification:
    """宛先と、その顧客のルールを作り、遅れの出来事を 1 件振り分けて、その通知を返す（コミットする）"""
    destinations = [services.create_destination(db, name="配信先 " + uuid.uuid4().hex[:8], address=a) for a in addresses]
    rule = services.create_rule(
        db, name="配信のルール " + uuid.uuid4().hex[:6], event_types=[EventType.ORDER_LATE],
        destination_ids=[d.id for d in destinations], template_code=ORDER_DEADLINE, client_code=client["code"], **rule_kwargs,
    )
    order_id = int(uuid.uuid4().int % 10**12)
    deadline = NOON - 5 * MIN
    payload = OrderDeadlineV1(
        order_id=order_id, client_code=client["code"], client_name="配信の顧客", biz_master_id=client["biz"],
        business_code=client["biz_code"], biz_name="配信の業務", template_id=1, template_name="夜間の取込",
        occurrence_date=date(2026, 11, 12), scheduled_start=deadline - timedelta(hours=1), scheduled_end=deadline,
        deadline_at=deadline, warn_at=None, status="ready",
    )
    publish(db, event_type=EventType.ORDER_LATE, payload=payload, occurred_at=deadline, client_code=client["code"],
            subject_type="order", subject_id=order_id, dedupe_key=f"order:{order_id}:OrderLate:{deadline.isoformat()}")
    dispatch_events(db, now=now, batch_size=100_000)
    db.commit()
    notification = db.scalar(select(Notification).where(Notification.rule_id == rule.id))
    assert notification is not None
    return notification


def deliveries(db, n: Notification) -> list[NotificationDelivery]:
    db.expire_all()
    return list(db.scalars(select(NotificationDelivery).where(NotificationDelivery.notification_id == n.id).order_by(NotificationDelivery.id)))


def run(db, now: datetime, channel=None):
    return deliver_due(db, now=now, channels={NotificationChannel.EMAIL: channel or FakeChannel()})


def test_sent_to_every_destination_and_the_notification_is_completed(db, client):
    n = notification_for(db, client, ["ops@example.com", "team@example.com"])
    fake = FakeChannel()
    result = run(db, NOON, fake)
    assert result.sent >= 2
    ds = deliveries(db, n)
    assert [(d.status, d.attempts, d.sent_at, d.next_attempt_at) for d in ds] == [(DeliveryStatus.SENT, 1, NOON, None)] * 2
    assert all((d.provider_message_id or "").startswith("fake:") for d in ds)
    mine = [s for s in fake.sent if s[1] == n.subject]
    assert sorted(a for a, _, _ in mine) == ["ops@example.com", "team@example.com"]
    assert "夜間の取込" in mine[0][2]
    db.refresh(n)
    assert (n.status, n.completed_at) == (NotificationStatus.SENT, NOON)

    again = FakeChannel()
    run(db, NOON + MIN, again)
    assert [s for s in again.sent if s[1] == n.subject] == []  # 送ったものは二度と送らない


def test_temporary_failures_retry_at_1_5_15_60_minutes_then_fail(db, client):
    n = notification_for(db, client, ["fail-temp@example.com"])
    now = NOON
    expected_waits = [1, 5, 15, 60]
    for attempt, wait in enumerate(expected_waits, start=1):
        run(db, now)
        [d] = deliveries(db, n)
        assert (d.status, d.attempts, d.next_attempt_at) == (DeliveryStatus.RETRYING, attempt, now + wait * MIN)
        assert "not answering" in (d.last_error or "")
        run(db, now + wait * MIN - timedelta(seconds=1))  # まだ時刻が来ていない
        assert deliveries(db, n)[0].attempts == attempt
        now = now + wait * MIN
    run(db, now)
    [d] = deliveries(db, n)
    assert (d.status, d.attempts, d.next_attempt_at, d.sent_at) == (DeliveryStatus.FAILED, 5, None, None)
    db.refresh(n)
    assert (n.status, n.completed_at) == (NotificationStatus.FAILED, now)


def test_a_permanent_failure_fails_at_once_and_mixed_results_are_partly_failed(db, client):
    n = notification_for(db, client, ["fail-perm@example.com", "ops@example.com"])
    run(db, NOON)
    perm, ok = deliveries(db, n)
    assert (perm.status, perm.attempts, perm.next_attempt_at) == (DeliveryStatus.FAILED, 1, None)
    assert ok.status == DeliveryStatus.SENT
    db.refresh(n)
    assert n.status == NotificationStatus.PARTLY_FAILED


def test_a_destination_disabled_after_the_alarm_is_not_sent(db, client):
    n = notification_for(db, client, ["ops@example.com"])
    [d] = deliveries(db, n)
    destination = db.get(NotificationDestination, d.destination_id)
    assert destination is not None
    destination.enabled = False
    db.commit()
    fake = FakeChannel()
    run(db, NOON, fake)
    [d] = deliveries(db, n)
    assert (d.status, d.last_error) == (DeliveryStatus.FAILED, ERROR_DESTINATION_UNUSABLE)
    assert [s for s in fake.sent if s[1] == n.subject] == []


class Broken:
    def send(self, destination, notification) -> SendResult:
        raise ConnectionError("socket closed")


def test_channel_errors_and_missing_channels_are_temporary(db, client):
    n = notification_for(db, client, ["ops@example.com"])
    run(db, NOON, Broken())
    [d] = deliveries(db, n)
    assert (d.status, d.attempts) == (DeliveryStatus.RETRYING, 1) and "ConnectionError: socket closed" in (d.last_error or "")

    deliver_due(db, now=NOON + MIN, channels={})
    [d] = deliveries(db, n)
    assert (d.status, d.attempts) == (DeliveryStatus.RETRYING, 2) and "no channel is configured for email" in (d.last_error or "")


def test_held_deliveries_go_out_when_quiet_hours_end(db, client):
    night = datetime(2026, 11, 12, 23, 0, tzinfo=JST)
    n = notification_for(db, client, ["ops@example.com"], now=night,
                         quiet_start=time(22, 0), quiet_end=time(7, 0), quiet_mode=QuietMode.HOLD)
    morning = datetime(2026, 11, 13, 7, 0, tzinfo=JST)
    run(db, morning - MIN)
    assert deliveries(db, n)[0].status == DeliveryStatus.PENDING
    fake = FakeChannel()
    run(db, morning, fake)
    [d] = deliveries(db, n)
    assert (d.status, d.sent_at) == (DeliveryStatus.SENT, morning)
    [(_, _, body)] = [s for s in fake.sent if s[1] == n.subject]
    assert "締切（11/12 11:55）を過ぎても完了していないオーダーがあります。" in body  # その時刻の事実のまま


def test_utc_and_local_now_are_the_same_moment(db, client):
    n = notification_for(db, client, ["ops@example.com"])
    run(db, NOON.astimezone(UTC))
    assert deliveries(db, n)[0].status == DeliveryStatus.SENT


def test_a_delivery_locked_by_another_worker_is_skipped():
    """もう 1 つのワーカーが送っている配信（行がロックされている）は取らない。終われば次の回に送る（本当にコミットして確かめる）"""
    from sqlalchemy import delete, text
    from src.domain_events.models import DomainEvent
    from src.domain_notification.models import NotificationDeliveryAttempt, NotificationDestination, NotificationRule

    db = SessionLocal()
    made: dict[str, list[int]] = {}
    try:
        code = new_client_code("O")
        client_repo.create(db, obj_in=ClientCreate(code=code, name="ロックの顧客"))
        biz = biz_master_repo.create(db, obj_in=BizMasterCreate(client_code=code, name="ロックの業務", business_seq=1, manager_email=None, description=None))
        n = notification_for(db, {"code": code, "biz": biz.id, "biz_code": biz.business_code}, ["ops@example.com"])
        [d] = deliveries(db, n)
        made = {"delivery": [d.id], "notification": [n.id], "event": [n.event_id], "rule": [n.rule_id or 0], "destination": [d.destination_id]}

        other = engine.connect()  # もう 1 つのワーカー
        lock = other.begin()
        other.execute(text("SELECT id FROM notification_delivery WHERE id = :id FOR UPDATE"), {"id": d.id})
        fake = FakeChannel()
        deliver_due(db, now=NOON, channels={NotificationChannel.EMAIL: fake})
        assert deliveries(db, n)[0].status == DeliveryStatus.PENDING and [s for s in fake.sent if s[1] == n.subject] == []
        lock.rollback()
        other.close()

        deliver_due(db, now=NOON, channels={NotificationChannel.EMAIL: fake})
        assert deliveries(db, n)[0].status == DeliveryStatus.SENT
        assert len([s for s in fake.sent if s[1] == n.subject]) == 1
    finally:
        db.rollback()
        if made:
            db.execute(delete(NotificationDeliveryAttempt).where(NotificationDeliveryAttempt.delivery_id.in_(made["delivery"])))
            db.execute(delete(NotificationDelivery).where(NotificationDelivery.id.in_(made["delivery"])))
            db.execute(delete(Notification).where(Notification.id.in_(made["notification"])))
            db.execute(text("DELETE FROM notification_rule_destination WHERE rule_id = ANY(:ids)"), {"ids": made["rule"]})
            db.execute(delete(NotificationRule).where(NotificationRule.id.in_(made["rule"])))
            db.execute(delete(NotificationDestination).where(NotificationDestination.id.in_(made["destination"])))
            db.execute(text("SET LOCAL session_replication_role = replica"))  # 出来事は変えられない（トリガー）: テスト用 DB の後片付けだけ外す
            db.execute(delete(DomainEvent).where(DomainEvent.id.in_(made["event"])))
            db.commit()
        db.close()


from tests.test_smtp_channel import smtp_server  # noqa: E402,F401  テストの中の SMTP サーバー


def test_end_to_end_through_smtp(db, client, smtp_server):
    """出来事 → 振り分け → SMTP で送る → 配信に Message-ID・通知は sent（本物の smtplib）"""
    from email import message_from_bytes, policy
    from src.domain_notification.smtp import SmtpChannel, SmtpSettings

    n = notification_for(db, client, ["ops@example.com"])
    smtp = SmtpChannel(SmtpSettings(host="127.0.0.1", port=smtp_server.server_address[1], timeout_seconds=5))
    deliver_due(db, now=NOON, channels={NotificationChannel.EMAIL: smtp})
    [d] = deliveries(db, n)
    assert d.status == DeliveryStatus.SENT and (d.provider_message_id or "").startswith("<")
    mine = [m for _, data in smtp_server.received if (m := message_from_bytes(data, policy=policy.default))["Message-ID"] == d.provider_message_id]
    assert len(mine) == 1 and mine[0]["Subject"] == n.subject
    db.refresh(n)
    assert n.status == NotificationStatus.SENT
