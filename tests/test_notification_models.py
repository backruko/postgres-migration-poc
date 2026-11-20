"""
通知の仕組みの表: 設計書「Notifications」→ Data model の決まりを、データベースが守ること。
（振る舞いはまだ無い。ここで確かめるのは一意・チェック・トリガー・外部キー）
"""
import uuid
from datetime import datetime, time, UTC

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from src.domain_client.schemas import ClientCreate
from src.domain_events.models import DomainEvent
from src.domain_notification.models import (
    DeliveryStatus, Notification, NotificationChannel, NotificationDelivery, NotificationDestination, NotificationRule,
    NotificationStatus, QuietMode, rule_destination,
)
from src.infrastructure.database import SessionLocal
from src.infrastructure.repositories import client_repo
from tests.client_codes import new_client_code


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def now():
    return datetime.now(UTC)


def event(db, key: str | None = None, **kw) -> DomainEvent:
    e = DomainEvent(
        event_type=kw.pop("event_type", "order.late"), occurred_at=now(), client_code=kw.pop("client_code", None),
        subject_type="order", subject_id=kw.pop("subject_id", 1), payload=kw.pop("payload", {"deadline": "x"}),
        dedupe_key=key or f"test:{uuid.uuid4().hex}",
    )
    db.add(e)
    db.flush()
    return e


def destination(db, name: str | None = None, **kw) -> NotificationDestination:
    d = NotificationDestination(
        name=name or f"dest {uuid.uuid4().hex[:8]}", channel=NotificationChannel.EMAIL, address="ops@example.com", **kw,
    )
    db.add(d)
    db.flush()
    return d


def rule(db, **kw) -> NotificationRule:
    r = NotificationRule(
        name=kw.pop("name", "late orders"), event_types=kw.pop("event_types", ["order.late"]),
        template_code=kw.pop("template_code", "order.late"), **kw,
    )
    db.add(r)
    db.flush()
    return r


def notification(db, e: DomainEvent, r: NotificationRule, **kw) -> Notification:
    n = Notification(event_id=e.id, rule_id=r.id, dedupe_key=kw.pop("dedupe_key", e.dedupe_key),
                     subject="遅れ", body="本文", **kw)
    db.add(n)
    db.flush()
    return n


def expect_refused(db, action, error: type[DBAPIError] = IntegrityError):
    """action の書き込みが断られること。セーブポイントで巻き戻すので、テストの続きは同じセッションで書ける"""
    sp = db.begin_nested()
    with pytest.raises(error):
        action()
        db.flush()
    sp.rollback()


# ==========================================
# domain_event（アウトボックス）
# ==========================================
def test_the_same_fact_is_recorded_once(db):
    key = f"order:1:late:{uuid.uuid4().hex}"
    event(db, key)
    expect_refused(db, lambda: event(db, key))


def test_an_event_can_only_have_its_processing_columns_changed(db):
    e = event(db)
    # 処理の列は変えられる
    db.execute(update(DomainEvent).where(DomainEvent.id == e.id).values(processed_at=now(), attempts=1, last_error="x"))
    db.flush()
    # 記録そのもの（中身・種類・キー・日時）は変えられない
    for values in ({"payload": {"deadline": "changed"}}, {"event_type": "order.due_soon"}, {"dedupe_key": "other"},
                   {"occurred_at": now()}, {"client_code": "ZZZZ"}):
        expect_refused(db, lambda v=values: db.execute(update(DomainEvent).where(DomainEvent.id == e.id).values(**v)), DBAPIError)
    expect_refused(db, lambda: db.execute(delete(DomainEvent).where(DomainEvent.id == e.id)), DBAPIError)
    db.expire_all()
    assert db.get(DomainEvent, e.id).payload == {"deadline": "x"}


def test_unprocessed_events_use_the_partial_index(db):
    names = set(db.scalars(text("SELECT indexname FROM pg_indexes WHERE tablename = 'domain_event'")))
    assert {"ix_domain_event_unprocessed", "ix_domain_event_subject", "ix_domain_event_type_occurred"} <= names
    definition = db.scalar(text("SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_domain_event_unprocessed'"))
    assert "WHERE (processed_at IS NULL)" in definition


# ==========================================
# 宛先・ルール（設定）
# ==========================================
def test_destination_names_are_unique_among_live_destinations(db):
    name = f"ops mailbox {uuid.uuid4().hex[:6]}"
    first = destination(db, name)
    expect_refused(db, lambda: destination(db, name))
    first.soft_delete()
    db.flush()
    destination(db, name)  # 削除した宛先の名前は、また使える


def test_destination_client_must_exist(db):
    expect_refused(db, lambda: destination(db, client_code="ZZZZ"))
    code = new_client_code("N")
    client_repo.create(db, obj_in=ClientCreate(code=code, name="notification client"))
    assert destination(db, client_code=code).client_code == code


def test_rule_needs_event_types_and_whole_quiet_hours(db):
    expect_refused(db, lambda: rule(db, event_types=[]))
    expect_refused(db, lambda: rule(db, dedupe_window_minutes=0))
    expect_refused(db, lambda: rule(db, quiet_start=time(22, 0)))  # 始まりだけは不可
    expect_refused(db, lambda: rule(db, quiet_start=time(22, 0), quiet_end=time(7, 0)))  # 扱いが無い
    r = rule(db, quiet_start=time(22, 0), quiet_end=time(7, 0), quiet_mode=QuietMode.HOLD, dedupe_window_minutes=60)
    assert r.quiet_mode == QuietMode.HOLD


def test_rule_destinations_and_a_destination_in_use_is_not_removed(db):
    d1, d2 = destination(db), destination(db)
    r = rule(db)
    r.destinations = [d1, d2]
    db.flush()
    db.expire(r)
    assert [d.id for d in r.destinations] == [d1.id, d2.id]
    # ルールが使っている宛先の行は消せない（論理削除する）
    expect_refused(db, lambda: db.execute(delete(NotificationDestination).where(NotificationDestination.id == d1.id)))
    # ルールの行を消すと、つながりも消える
    db.execute(delete(NotificationRule).where(NotificationRule.id == r.id))
    db.flush()
    assert db.scalar(select(rule_destination.c.rule_id).where(rule_destination.c.rule_id == r.id)) is None


# ==========================================
# 通知・配信（記録）
# ==========================================
def test_one_alarm_per_rule_and_fact(db):
    e = event(db)
    r1, r2 = rule(db), rule(db)
    notification(db, e, r1)
    expect_refused(db, lambda: notification(db, e, r1))  # 同じルール × 同じ事実は 1 回だけ
    notification(db, e, r2)  # 別のルールなら出せる


def test_a_suppressed_alarm_says_why(db):
    e, r = event(db), rule(db)
    expect_refused(db, lambda: notification(db, e, r, status=NotificationStatus.SUPPRESSED))
    expect_refused(db, lambda: notification(db, e, r, suppressed_reason="理由だけ"))
    n = notification(db, e, r, status=NotificationStatus.SUPPRESSED, suppressed_reason="知らせない時間帯")
    assert n.status == NotificationStatus.SUPPRESSED


def test_one_delivery_per_destination_and_sent_means_sent_at(db):
    e, r, d = event(db), rule(db), destination(db)
    n = notification(db, e, r)

    def deliver(**kw):
        db.add(NotificationDelivery(notification_id=n.id, destination_id=d.id, **kw))
        db.flush()

    expect_refused(db, lambda: deliver(status=DeliveryStatus.SENT))  # 届けたなら日時が要る
    expect_refused(db, lambda: deliver(sent_at=now()))  # 届けていないのに日時は不可
    expect_refused(db, lambda: deliver(attempts=-1))
    deliver(status=DeliveryStatus.SENT, sent_at=now(), attempts=1)
    expect_refused(db, lambda: deliver())  # 同じ宛先には 1 回だけ
    db.expire(n)
    assert [x.status for x in n.deliveries] == [DeliveryStatus.SENT]
    # 宛先も通知も、配信の記録がある間は消せない
    expect_refused(db, lambda: db.execute(delete(Notification).where(Notification.id == n.id)))
    expect_refused(db, lambda: db.execute(delete(DomainEvent).where(DomainEvent.id == e.id)), DBAPIError)


def test_due_deliveries_use_the_partial_index(db):
    definition = db.scalar(text("SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_notification_delivery_due'"))
    assert "PENDING" in definition and "RETRYING" in definition
