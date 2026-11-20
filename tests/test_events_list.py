# tests/test_events_list.py
"""
出来事の一覧（読み取りのみ。通知の仕組み）のテスト。
- 起きた日時の新しい順・件数の上限と truncated
- 種類・顧客・オーダー・期間（from 以上・to 未満）で絞れる。タイムゾーンのない日時は日本時間
- 顧客の範囲がある職員には、その顧客の出来事だけ（顧客のない出来事も見せない）
- from が to 以降なら 400

出来事は publish() で直接書き、このテストの顧客コードで絞って確かめます（顧客の表は使わない: domain_event に外部キーはない）。
"""
import random
from datetime import date, datetime, timedelta, UTC
from zoneinfo import ZoneInfo

import pytest

from tests.client_codes import new_client_code
from src.core.exceptions import BadRequestError
from src.domain_events.formats import EventType, OrderDeadlineV1
from src.domain_events.publish import publish
from src.domain_events.services import list_events
from src.domain_staff.scope import ClientScope
from src.infrastructure.database import SessionLocal

JST = ZoneInfo("Asia/Tokyo")
BASE = datetime(2031, 3, 3, 0, 0, tzinfo=UTC)  # ほかのテストの出来事と時刻が重ならない日


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def write(db, event_type: str, client_code: str | None, order_id: int, minutes: int) -> int:
    at = BASE + timedelta(minutes=minutes)
    payload = OrderDeadlineV1(
        order_id=order_id, client_code=client_code or "-", client_name="c", biz_master_id=1, business_code="b-01",
        biz_name="b", template_id=1, template_name="t", occurrence_date=date(2031, 3, 3), scheduled_start=at,
        scheduled_end=at, deadline_at=at, warn_at=None, status="pending",
    )
    event_id = publish(
        db, event_type=event_type, payload=payload, dedupe_key=f"test:{order_id}:{event_type}:{at.isoformat()}",
        occurred_at=at, client_code=client_code, subject_type="order", subject_id=order_id,
    )
    assert event_id is not None
    return event_id


@pytest.fixture
def events(db):
    a, b = new_client_code("E"), new_client_code("E")
    order_a, order_b, order_none = (random.randint(10**12, 10**13) for _ in range(3))
    ids = {
        "a_due": write(db, EventType.ORDER_DUE_SOON, a, order_a, 0),
        "a_late": write(db, EventType.ORDER_LATE, a, order_a, 30),
        "b_late": write(db, EventType.ORDER_LATE, b, order_b, 60),
        "none": write(db, EventType.ORDER_LATE, None, order_none, 90),
    }
    return {"a": a, "b": b, "order_a": order_a, "order_none": order_none, "ids": ids}


def ids_of(response) -> list[int]:
    return [e.id for e in response.items]


def test_newest_first_with_filters(db, events):
    ids = events["ids"]
    a = list_events(db, client_code=events["a"])
    assert ids_of(a) == [ids["a_late"], ids["a_due"]] and not a.truncated
    assert ids_of(list_events(db, client_code=events["a"].lower(), event_type=EventType.ORDER_DUE_SOON)) == [ids["a_due"]]
    assert ids_of(list_events(db, subject_type="order", subject_id=events["order_a"])) == [ids["a_late"], ids["a_due"]]

    first = list_events(db, client_code=events["a"], limit=1)
    assert ids_of(first) == [ids["a_late"]] and first.truncated

    item = first.items[0]
    assert (item.event_type, item.schema_version, item.subject_type, item.subject_id) == (EventType.ORDER_LATE, 1, "order", events["order_a"])
    assert item.payload["order_id"] == events["order_a"] and item.processed_at is None and item.attempts == 0


def test_period_is_from_inclusive_to_exclusive_and_naive_times_are_japan_time(db, events):
    ids = events["ids"]
    window = list_events(db, occurred_from=BASE + timedelta(minutes=30), occurred_to=BASE + timedelta(minutes=90))
    assert ids_of(window) == [ids["b_late"], ids["a_late"]]
    # 2031-03-03 09:30 日本時間 = 00:30 UTC
    naive = list_events(db, client_code=events["a"], occurred_from=datetime(2031, 3, 3, 9, 30))
    assert ids_of(naive) == [ids["a_late"]]
    with pytest.raises(BadRequestError):
        list_events(db, occurred_from=BASE, occurred_to=BASE)


def test_client_scope_shows_only_its_clients_and_hides_events_without_a_client(db, events):
    ids = events["ids"]
    start, end = BASE, BASE + timedelta(hours=2)
    everyone = set(ids_of(list_events(db, occurred_from=start, occurred_to=end)))
    assert set(ids.values()) <= everyone  # 全顧客の職員には顧客のない出来事も見える

    scoped = set(ids_of(list_events(db, scope=ClientScope.of([events["a"]]), occurred_from=start, occurred_to=end)))
    assert scoped == {ids["a_due"], ids["a_late"]}
    # 範囲の外の顧客を指定しても見えない
    assert ids_of(list_events(db, client_code=events["b"], scope=ClientScope.of([events["a"]]))) == []
    assert ids_of(list_events(db, subject_type="order", subject_id=events["order_none"], scope=ClientScope.of([events["a"]]))) == []
