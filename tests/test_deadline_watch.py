# tests/test_deadline_watch.py
"""
締切の監視と出来事の記録（通知の仕組み N.2）のテスト。
- 「まもなく」の開始を過ぎたら OrderDueSoon、締切を過ぎたら OrderLate を 1 回ずつ（何度呼んでも増えない）
- 見つけたときにもう締切を過ぎていれば OrderLate だけ
- 振り返りの幅（既定 24 時間）より前に過ぎたものは記録しない
- 完了・取消のオーダー、知らせなしのオーダーの「まもなく」は記録しない。論理削除したテンプレートのオーダーは記録する
- 締切が動けば、新しい締切でもう一度記録できる
- 中身はその時点の事実の写し（版 1）。publish() は同じキーを 2 回書かず、知らない種類・違う形を受け付けない

監視はテスト用 DB の全オーダーを見るので、結果はこのテストのオーダーに絞って確かめます。
"""
from datetime import date, datetime, time, timedelta, UTC
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from tests.client_codes import new_client_code
from src.domain_biz.schemas import BizMasterCreate
from src.domain_client.schemas import ClientCreate
from src.domain_events.formats import EventType, OrderDeadlineV1
from src.domain_events.models import DomainEvent
from src.domain_events.publish import publish
from src.domain_ope.deadline_watch import dedupe_key, record_deadline_events
from src.domain_ope.models import OpeOrder, OrderStatus
from src.domain_plan import services as plan_services
from src.domain_plan.models import DeadlineMode, PlanTemplate, TemplateType
from src.domain_plan.schemas import PlanInstanceOverride, RRuleTemplateCreate
from src.infrastructure.database import SessionLocal
from src.infrastructure.repositories import biz_master_repo, client_repo

JST = ZoneInfo("Asia/Tokyo")
MINUTE = timedelta(minutes=1)


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def biz_id(db):
    code = new_client_code("W")
    client_repo.create(db, obj_in=ClientCreate(code=code, name="watch client"))
    return biz_master_repo.create(
        db, obj_in=BizMasterCreate(client_code=code, name="watch biz", business_seq=1, manager_email=None, description=None),
    ).id


def make_order(db, biz_id, index: int = 3, **kwargs) -> OpeOrder:
    """毎日 9:00 開始・締切は開始の 5 時間後（14:00）・その 30 分前から「まもなく」のテンプレートの、index 番目のオーダー"""
    data = dict(
        biz_master_id=biz_id, name="Watch daily", template_type=TemplateType.RRULE, timezone="Asia/Tokyo",
        start_time=time(9, 0), duration_minutes=60, anchor_date=date(2026, 1, 1), rrule="FREQ=DAILY",
        deadline_mode=DeadlineMode.OFFSET, deadline_offset_minutes=300, warn_before_minutes=30,
    )
    data.update(kwargs)
    template = plan_services.create_template(db, RRuleTemplateCreate.model_validate(data))
    db.expire_all()
    orders = db.scalars(select(OpeOrder).where(OpeOrder.template_id == template.id).order_by(OpeOrder.scheduled_start))
    return list(orders)[index]


def events_of(db, order_id: int) -> list[DomainEvent]:
    stmt = select(DomainEvent).where(DomainEvent.subject_type == "order", DomainEvent.subject_id == order_id)
    return list(db.scalars(stmt.order_by(DomainEvent.id)))


def test_due_soon_then_late_once_each(db, biz_id):
    o = make_order(db, biz_id)
    assert o.warn_at is not None
    deadline, warn = o.deadline_at, o.warn_at

    first = record_deadline_events(db, now=warn + MINUTE)
    assert o.id in first.due_soon and o.id not in first.late
    again = record_deadline_events(db, now=warn + 2 * MINUTE)
    assert o.id not in again.due_soon  # もう記録済み

    late = record_deadline_events(db, now=deadline + MINUTE)
    assert o.id in late.late and o.id not in late.due_soon
    assert o.id not in record_deadline_events(db, now=deadline + 5 * MINUTE).late

    events = events_of(db, o.id)
    assert [(e.event_type, e.occurred_at) for e in events] == [(EventType.ORDER_DUE_SOON, warn), (EventType.ORDER_LATE, deadline)]
    late_event = events[1]
    assert late_event.schema_version == 1 and late_event.client_code == o.client_code
    assert late_event.dedupe_key == f"order:{o.id}:OrderLate:{deadline.astimezone(UTC).isoformat()}"
    assert late_event.processed_at is None and late_event.attempts == 0
    # その時点の事実の写し（通知の文面はこれだけで作れる）
    payload = late_event.payload
    assert payload["order_id"] == o.id and payload["status"] == "pending"
    assert (payload["client_name"], payload["biz_name"], payload["template_name"]) == ("watch client", "watch biz", "Watch daily")
    assert payload["occurrence_date"] == o.occurrence_date.isoformat()
    assert datetime.fromisoformat(payload["deadline_at"]) == deadline
    assert datetime.fromisoformat(payload["warn_at"]) == warn
    assert OrderDeadlineV1.model_validate(payload).business_code  # 版 1 の形で読める


def test_only_late_when_first_seen_after_the_deadline(db, biz_id):
    o = make_order(db, biz_id)
    result = record_deadline_events(db, now=o.deadline_at + 5 * MINUTE)
    assert o.id in result.late and o.id not in result.due_soon
    assert [e.event_type for e in events_of(db, o.id)] == [EventType.ORDER_LATE]


def test_times_older_than_the_lookback_are_skipped(db, biz_id):
    o = make_order(db, biz_id)
    later = o.deadline_at + timedelta(hours=25)
    assert o.id not in record_deadline_events(db, now=later).late                      # 既定の 24 時間より前
    assert o.id in record_deadline_events(db, now=later, lookback=timedelta(hours=48)).late


def test_canceled_orders_and_orders_without_warning(db, biz_id):
    canceled = make_order(db, biz_id)
    canceled.status = OrderStatus.CANCELED
    db.flush()
    silent = make_order(db, biz_id, warn_before_minutes=0)  # 知らせなし: warn_at がない
    assert silent.warn_at is None

    during = record_deadline_events(db, now=silent.deadline_at - 10 * MINUTE)
    assert silent.id not in during.due_soon
    after = record_deadline_events(db, now=canceled.deadline_at + MINUTE)
    assert canceled.id not in after.late
    assert silent.id in after.late  # 知らせなしでも遅れは記録する
    assert events_of(db, canceled.id) == []


def test_a_moved_deadline_can_alarm_again(db, biz_id):
    o = make_order(db, biz_id)
    first_deadline = o.deadline_at
    assert o.id in record_deadline_events(db, now=first_deadline + MINUTE).late

    # 予定を 2 時間遅らせる → 締切も 2 時間後（開始からの決め方）。新しい締切を過ぎたら、もう一度
    plan_services.override_instance(db, o.current_instance_id, PlanInstanceOverride(
        scheduled_start=o.scheduled_start + timedelta(hours=2),
    ))
    db.expire_all()
    moved = db.get(OpeOrder, o.id)
    assert moved is not None and moved.deadline_at == first_deadline + timedelta(hours=2)
    assert o.id not in record_deadline_events(db, now=moved.deadline_at - MINUTE).late
    assert o.id in record_deadline_events(db, now=moved.deadline_at + MINUTE).late
    assert [e.occurred_at for e in events_of(db, o.id) if e.event_type == EventType.ORDER_LATE] == [
        first_deadline, moved.deadline_at,
    ]



def test_orders_of_a_deleted_template_are_still_watched(db, biz_id):
    """論理削除したテンプレートのオーダーでも、開いていれば記録する（既定の論理削除の絞り込みで消えない）"""
    o = make_order(db, biz_id)
    template = db.get(PlanTemplate, o.template_id)
    assert template is not None
    template.is_deleted = True
    db.flush()
    assert o.id in record_deadline_events(db, now=o.deadline_at + MINUTE).late
    assert events_of(db, o.id)[0].payload["template_name"] == "Watch daily"

def _payload(order_id: int = 1) -> OrderDeadlineV1:
    at = datetime(2026, 11, 12, 5, 0, tzinfo=UTC)
    return OrderDeadlineV1(
        order_id=order_id, client_code="ZZZZ", client_name="c", biz_master_id=1, business_code="ZZZZ-01", biz_name="b",
        template_id=1, template_name="t", occurrence_date=date(2026, 11, 12), scheduled_start=at, scheduled_end=at,
        deadline_at=at, warn_at=None, status="pending",
    )


def test_publish_writes_once_and_checks_the_format(db):
    at = datetime(2026, 11, 12, 5, 0, tzinfo=UTC)
    key = dedupe_key(987654321, EventType.ORDER_LATE, at)
    first = publish(db, event_type=EventType.ORDER_LATE, payload=_payload(), dedupe_key=key, occurred_at=at)
    assert first is not None
    assert publish(db, event_type=EventType.ORDER_LATE, payload=_payload(), dedupe_key=key, occurred_at=at) is None
    assert db.scalar(select(DomainEvent.event_type).where(DomainEvent.id == first)) == EventType.ORDER_LATE

    with pytest.raises(ValueError):
        publish(db, event_type="OrderExploded", payload=_payload(), dedupe_key=key + ":x", occurred_at=at)
    with pytest.raises(ValueError):
        publish(db, event_type=EventType.ORDER_LATE, payload=_payload(), dedupe_key=key + ":y", occurred_at=at.replace(tzinfo=None))
    with pytest.raises(ValidationError):
        OrderDeadlineV1.model_validate({**_payload().model_dump(), "extra": 1})  # 知らない項目は受け付けない
