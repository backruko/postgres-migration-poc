# tests/test_notification_dispatch.py
"""
通知の振り分け（通知の仕組み N.3a）のテスト。
- 当てはまるルールごとに通知 1 件・宛先ごとに配信（待ち）。出来事は処理済み。同じ事実から 2 通目は出ない
- 当てはまらない: ほかの顧客・ほかの業務・ほかの種類・無効・削除したルール
- 知らせない: 重複の期間（同じルール・オーダー・種類。まもなくは遅れを消さない）・知らせない時間帯（知らせない）・
  使える宛先がない・文面がない。知らせない時間帯（後で）は、配信を時間帯の終わりまで待たせる
- 出来事の処理の失敗はその出来事だけ取り消し、回数と理由を残す。5 回でもう取らない
- 文面（日本語・日本時間）と、APP_BASE_URL があるときだけのリンク
- ルール・宛先を作るときの確かめ
"""
import uuid
from datetime import date, datetime, time, timedelta, UTC
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from tests.client_codes import new_client_code
from src.core.exceptions import BadRequestError, DuplicatedRecordError, NotFoundError
from src.domain_biz.schemas import BizMasterCreate
from src.domain_client.schemas import ClientCreate
from src.domain_events.formats import EventType, OrderDeadlineV1
from src.domain_events.models import DomainEvent
from src.domain_events.publish import publish
from src.domain_notification import dispatcher, services
from src.domain_notification.dispatcher import dispatch_events, quiet_until
from src.domain_notification.models import (
    DeliveryStatus, Notification, NotificationChannel, NotificationStatus, QuietMode,
)
from src.domain_notification.templates import ORDER_DEADLINE
from src.infrastructure.config import settings
from src.infrastructure.database import SessionLocal
from src.infrastructure.repositories import biz_master_repo, client_repo

JST = ZoneInfo("Asia/Tokyo")
NOON = datetime(2026, 11, 12, 12, 0, tzinfo=JST)  # 知らせない時間帯の外
BIG = 100_000  # テスト用 DB のほかの未処理の出来事があっても、このテストの出来事を必ず取る


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def client(db):
    """顧客 1 つと業務 2 つ（業務で絞るルールのため）"""
    code = new_client_code("N")
    client_repo.create(db, obj_in=ClientCreate(code=code, name="通知の顧客"))
    bizes = [
        biz_master_repo.create(db, obj_in=BizMasterCreate(client_code=code, name=f"通知の業務{i}", business_seq=i, manager_email=None, description=None))
        for i in (1, 2)
    ]
    return {"code": code, "biz": [b.id for b in bizes], "biz_code": [b.business_code for b in bizes]}


def destination(db, client_code: str | None = None, **kwargs):
    return services.create_destination(
        db, name="宛先 " + uuid.uuid4().hex[:8], address=kwargs.pop("address", "ops@example.com"), client_code=client_code, **kwargs,
    )


def rule(db, client_code: str | None, destinations, **kwargs):
    data = dict(
        name="ルール " + uuid.uuid4().hex[:6], event_types=[EventType.ORDER_LATE, EventType.ORDER_DUE_SOON],
        destination_ids=[d.id for d in destinations], template_code=ORDER_DEADLINE, client_code=client_code,
    )
    data.update(kwargs)
    return services.create_rule(db, **data)  # pyright: ignore[reportArgumentType]


def event(db, client, *, order_id: int | None = None, event_type: str = EventType.ORDER_LATE, biz: int = 0,
          deadline: datetime | None = None) -> DomainEvent:
    order_id = order_id or int(uuid.uuid4().int % 10**12)
    deadline = deadline or (NOON - timedelta(minutes=5))
    payload = OrderDeadlineV1(
        order_id=order_id, client_code=client["code"], client_name="通知の顧客", biz_master_id=client["biz"][biz],
        business_code=client["biz_code"][biz], biz_name=f"通知の業務{biz + 1}", template_id=1, template_name="毎日の取込",
        occurrence_date=date(2026, 11, 12), scheduled_start=deadline - timedelta(hours=2), scheduled_end=deadline,
        deadline_at=deadline, warn_at=deadline - timedelta(minutes=30), status="in_progress",
    )
    moment = deadline if event_type == EventType.ORDER_LATE else deadline - timedelta(minutes=30)
    event_id = publish(
        db, event_type=event_type, payload=payload, occurred_at=moment, client_code=client["code"],
        subject_type="order", subject_id=order_id,
        dedupe_key=f"order:{order_id}:{event_type}:{moment.astimezone(UTC).isoformat()}",
    )
    found = db.get(DomainEvent, event_id)
    assert found is not None
    return found


def notifications_of(db, e: DomainEvent) -> list[Notification]:
    return list(db.scalars(select(Notification).where(Notification.event_id == e.id).order_by(Notification.id)))


def dispatch(db, now: datetime = NOON):
    return dispatch_events(db, now=now, batch_size=BIG)


# ==========================================
# 当てはまるルールの通知と配信
# ==========================================
def test_a_matching_rule_makes_one_notification_with_a_delivery_per_destination(db, client):
    a, b = destination(db), destination(db, client["code"], address="team@example.com")
    r = rule(db, client["code"], [a, b])
    e = event(db, client)

    result = dispatch(db)
    assert result.notifications >= 1 and e.id not in result.failed_events
    db.refresh(e)
    assert e.processed_at == NOON and e.attempts == 0
    [n] = notifications_of(db, e)
    assert (n.rule_id, n.status, n.suppressed_reason, n.client_code, n.dedupe_key) == (
        r.id, NotificationStatus.PENDING, None, client["code"], e.dedupe_key,
    )
    assert [(d.destination_id, d.status, d.next_attempt_at, d.attempts) for d in n.deliveries] == [
        (a.id, DeliveryStatus.PENDING, NOON, 0), (b.id, DeliveryStatus.PENDING, NOON, 0),
    ]
    assert n.subject.startswith(f"【遅れ】{client['code']} 通知の業務1 / 毎日の取込 11/12 締切 11:55")
    assert "締切（11/12 11:55）を過ぎても完了していないオーダーがあります。" in n.body
    assert "状態（11/12 11:55 時点）: 作業中" in n.body and f"ルール「{r.name}」" in n.body

    # もう一度回しても、処理済みの出来事は読まない。処理済みの印を外しても、同じ事実から 2 通目は出ない
    dispatch(db)
    e.processed_at = None
    db.flush()
    dispatch(db)
    assert len(notifications_of(db, e)) == 1


def test_rules_that_do_not_match(db, client):
    d = destination(db)
    other_code = new_client_code("N")
    client_repo.create(db, obj_in=ClientCreate(code=other_code, name="ほかの顧客"))
    rule(db, other_code, [d])                                                  # ほかの顧客
    rule(db, client["code"], [d], biz_master_id=client["biz"][1])              # ほかの業務
    rule(db, client["code"], [d], event_types=[EventType.ORDER_DUE_SOON])      # ほかの種類
    off = rule(db, client["code"], [d])
    off.enabled = False
    gone = rule(db, client["code"], [d])
    gone.is_deleted = True
    db.flush()
    e = event(db, client, biz=0)
    dispatch(db)
    db.refresh(e)
    assert e.processed_at is not None and notifications_of(db, e) == []

    # 業務で絞るルールは、写しの業務が同じなら当てはまる
    biz_rule = rule(db, client["code"], [d], biz_master_id=client["biz"][0])
    e2 = event(db, client, biz=0)
    dispatch(db)
    assert [n.rule_id for n in notifications_of(db, e2)] == [biz_rule.id]


def test_a_new_rule_does_not_react_to_events_already_processed(db, client):
    d = destination(db)
    e = event(db, client)
    dispatch(db)
    rule(db, client["code"], [d])
    dispatch(db)
    assert notifications_of(db, e) == []


# ==========================================
# 知らせない（理由を残す）
# ==========================================
def test_dedupe_window_is_per_rule_order_and_type(db, client):
    d = destination(db)
    r = rule(db, client["code"], [d], dedupe_window_minutes=60)
    order = 424242
    due = event(db, client, order_id=order, event_type=EventType.ORDER_DUE_SOON)
    dispatch(db, NOON)
    late = event(db, client, order_id=order)  # 同じオーダーの遅れ: まもなくには消されない
    dispatch(db, NOON + timedelta(minutes=10))
    moved = event(db, client, order_id=order, deadline=NOON + timedelta(minutes=20))  # 締切が動いた後の、もう一度の遅れ
    dispatch(db, NOON + timedelta(minutes=30))
    again = event(db, client, order_id=order, deadline=NOON + timedelta(minutes=50))
    dispatch(db, NOON + timedelta(minutes=71))  # 遅れの通知（+10 分）から 61 分後: 期間の外

    status = lambda e: [(n.rule_id, n.status, n.suppressed_reason) for n in notifications_of(db, e)]  # noqa: E731
    assert status(due) == [(r.id, NotificationStatus.PENDING, None)]
    assert status(late) == [(r.id, NotificationStatus.PENDING, None)]
    assert status(moved) == [(r.id, NotificationStatus.SUPPRESSED, dispatcher.REASON_DEDUPE_WINDOW)]
    assert status(again) == [(r.id, NotificationStatus.PENDING, None)]
    [suppressed] = notifications_of(db, moved)
    assert suppressed.deliveries == [] and suppressed.completed_at == NOON + timedelta(minutes=30)


def test_quiet_hours_drop_or_hold(db, client):
    d = destination(db)
    drop = rule(db, client["code"], [d], quiet_start=time(22, 0), quiet_end=time(7, 0), quiet_mode=QuietMode.DROP)
    hold = rule(db, client["code"], [d], quiet_start=time(22, 0), quiet_end=time(7, 0), quiet_mode=QuietMode.HOLD)
    night = datetime(2026, 11, 12, 23, 0, tzinfo=JST)
    e = event(db, client)
    result = dispatch(db, night)
    by_rule = {n.rule_id: n for n in notifications_of(db, e)}
    assert (by_rule[drop.id].status, by_rule[drop.id].suppressed_reason) == (NotificationStatus.SUPPRESSED, dispatcher.REASON_QUIET_HOURS)
    held = by_rule[hold.id]
    assert held.status == NotificationStatus.PENDING
    assert [x.next_attempt_at for x in held.deliveries] == [datetime(2026, 11, 13, 7, 0, tzinfo=JST)]
    assert result.held >= 1

    # 時間帯の終わりの日時（日をまたぐ・またがない）
    assert quiet_until(hold, datetime(2026, 11, 13, 6, 59, tzinfo=JST)) == datetime(2026, 11, 13, 7, 0, tzinfo=JST)
    assert quiet_until(hold, datetime(2026, 11, 13, 7, 0, tzinfo=JST)) is None
    assert quiet_until(hold, datetime(2026, 11, 13, 21, 59, tzinfo=JST)) is None
    day = rule(db, client["code"], [d], quiet_start=time(12, 0), quiet_end=time(13, 0), quiet_mode=QuietMode.HOLD)
    assert quiet_until(day, NOON) == datetime(2026, 11, 12, 13, 0, tzinfo=JST)
    assert quiet_until(day, NOON - timedelta(minutes=1)) is None


def test_no_usable_destination_and_missing_template(db, client):
    other_code = new_client_code("N")
    client_repo.create(db, obj_in=ClientCreate(code=other_code, name="ほかの顧客"))
    disabled = destination(db)
    disabled.enabled = False
    foreign = destination(db, other_code)
    no_dest = rule(db, None, [disabled, foreign])  # 全顧客のルール。宛先は無効か、ほかの顧客用
    no_template = rule(db, client["code"], [destination(db)])
    no_template.template_code = "removed_template"  # コードから文面がなくなった（設定の誤り）
    db.flush()
    e = event(db, client)
    dispatch(db)
    reasons = {n.rule_id: n.suppressed_reason for n in notifications_of(db, e)}
    assert reasons[no_dest.id] == dispatcher.REASON_NO_DESTINATION
    assert reasons[no_template.id] == dispatcher.REASON_TEMPLATE_MISSING
    db.refresh(e)
    assert e.processed_at is not None  # 設定の誤りで出来事を止めない


# ==========================================
# 出来事の処理の失敗
# ==========================================
def test_a_failing_event_is_rolled_back_alone_and_given_up_after_five_attempts(db, client):
    d = destination(db)
    rule(db, client["code"], [d])
    broken = DomainEvent(
        event_type=EventType.ORDER_LATE, occurred_at=NOON, client_code=client["code"], subject_type="order",
        subject_id=1, payload={"order_id": 1}, dedupe_key="broken:" + uuid.uuid4().hex,  # 版 1 の形ではない
    )
    db.add(broken)
    db.flush()
    good = event(db, client)

    first = dispatch(db)
    assert broken.id in first.failed_events and good.id not in first.failed_events
    db.refresh(broken)
    assert broken.processed_at is None and broken.attempts == 1 and "ValidationError" in (broken.last_error or "")
    assert notifications_of(db, broken) == [] and len(notifications_of(db, good)) == 1

    for _ in range(4):
        dispatch(db)
    db.refresh(broken)
    assert broken.attempts == 5
    assert broken.id not in dispatch(db).failed_events  # もう取らない
    db.refresh(broken)
    assert broken.attempts == 5


# ==========================================
# 文面のリンク
# ==========================================
def test_link_only_with_app_base_url(db, client, monkeypatch):
    d = destination(db)
    rule(db, client["code"], [d])
    e1 = event(db, client)
    dispatch(db)
    assert notifications_of(db, e1)[0].link is None

    monkeypatch.setattr(settings, "app_base_url", "http://ops.example/")
    e2 = event(db, client, event_type=EventType.ORDER_DUE_SOON)
    dispatch(db)
    [n] = notifications_of(db, e2)
    assert n.link == "http://ops.example/daily?date=2026-11-12&tab=attention"
    assert n.subject.startswith("【まもなく】") and f"画面: {n.link}" in n.body


# ==========================================
# ルール・宛先を作るときの確かめ
# ==========================================
def test_settings_are_checked_when_created(db, client):
    other_code = new_client_code("N")
    client_repo.create(db, obj_in=ClientCreate(code=other_code, name="ほかの顧客"))
    d = destination(db)
    with pytest.raises(BadRequestError):
        destination(db, address="not-an-address")
    with pytest.raises(BadRequestError):
        destination(db, channel=NotificationChannel.TEAMS, address="https://teams.example/hook")
    with pytest.raises(DuplicatedRecordError):
        services.create_destination(db, name=d.name, address="x@example.com")  # 同じ名前（409）
    with pytest.raises(NotFoundError):
        destination(db, "ZZZ0")

    for bad in (
        dict(event_types=["OrderExploded"]),
        dict(event_types=[]),
        dict(template_code="nope"),
        dict(destination_ids=[]),
        dict(dedupe_window_minutes=0),
        dict(quiet_start=time(22, 0)),
        dict(quiet_start=time(9, 0), quiet_end=time(9, 0), quiet_mode=QuietMode.HOLD),
    ):
        with pytest.raises(BadRequestError):
            rule(db, client["code"], [d], **bad)
    with pytest.raises(BadRequestError):
        rule(db, other_code, [d], biz_master_id=client["biz"][0])  # 業務がほかの顧客のもの
    with pytest.raises(BadRequestError):
        rule(db, client["code"], [destination(db, other_code)])  # ほかの顧客の宛先
    with pytest.raises(NotFoundError):
        services.create_rule(db, name="x", event_types=[EventType.ORDER_LATE], destination_ids=[987654321], template_code=ORDER_DEADLINE)

    r = rule(db, client["code"], [d], event_types=[EventType.ORDER_LATE, EventType.ORDER_LATE])
    assert r.event_types == [EventType.ORDER_LATE] and [x.id for x in r.destinations] == [d.id]


def test_notification_models_resolve_their_foreign_keys_in_a_fresh_process():
    """ワーカーのようにルーターを読まないプロセスでも、職員の表への外部キーを解決できる（N.3a で見つけた不具合）"""
    import os
    import subprocess
    import sys

    code = (
        "import src.domain_notification.dispatcher, src.domain_notification.services\n"
        "from src.infrastructure.database import Base\n"
        "print(len(Base.metadata.sorted_tables))\n"  # 外部キーの先の表がなければ NoReferencedTableError
    )
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    run = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, env={**os.environ, "ENVIRONMENT": "test"})
    assert run.returncode == 0, run.stderr[-2000:]


# ==========================================
# 1 回の数え方と、その回の中で覚えること（R.6 / C901 で dispatch_events を分ける前に固定する）
# ==========================================
def test_one_pass_counts_what_it_made_and_remembers_it_for_the_next_event(db, client):
    dispatch(db)  # 先に、ほかの未処理の出来事を片づける（この回が数える出来事を、この 4 件にする）
    d = destination(db)
    windowed = rule(db, client["code"], [d], dedupe_window_minutes=60, event_types=[EventType.ORDER_LATE])
    held_rule = rule(db, client["code"], [d], quiet_start=time(12, 0), quiet_end=time(13, 0), quiet_mode=QuietMode.HOLD,
                     event_types=[EventType.ORDER_DUE_SOON])
    order_id = int(uuid.uuid4().int % 10**12)
    first = event(db, client, order_id=order_id)
    second = event(db, client, order_id=order_id, deadline=NOON - timedelta(minutes=3))  # 同じオーダー・種類・別の事実
    soon = event(db, client, event_type=EventType.ORDER_DUE_SOON)
    broken = DomainEvent(
        event_type=EventType.ORDER_LATE, occurred_at=NOON, client_code=client["code"], subject_type="order",
        subject_id=order_id, payload={"order_id": order_id}, dedupe_key="broken:" + uuid.uuid4().hex,
    )
    db.add(broken)
    db.flush()

    result = dispatch(db)

    assert result.events == 4 and result.failed_events == [broken.id]
    ours = [n for e in (first, second, soon) for n in notifications_of(db, e)]
    made = [n for n in ours if n.suppressed_reason is None]
    assert result.notifications == len(made)
    reasons: dict[str, int] = {}
    for n in ours:
        if n.suppressed_reason:
            reasons[n.suppressed_reason] = reasons.get(n.suppressed_reason, 0) + 1
    assert result.suppressed == reasons
    assert result.held == sum(1 for n in made if n.deliveries and (n.deliveries[0].next_attempt_at or NOON) > NOON)
    # この回の中で覚える: 先の出来事の通知のすぐ後なので、同じオーダー・種類の 2 件目は重複の期間で知らせない
    by_rule = {(n.event_id, n.rule_id): n for n in ours}
    assert by_rule[(first.id, windowed.id)].suppressed_reason is None
    assert by_rule[(second.id, windowed.id)].suppressed_reason == dispatcher.REASON_DEDUPE_WINDOW
    held_one = by_rule[(soon.id, held_rule.id)]
    assert held_one.suppressed_reason is None and held_one.deliveries[0].next_attempt_at == datetime(2026, 11, 12, 13, 0, tzinfo=JST)
    assert notifications_of(db, broken) == []
    # 失敗した出来事は次の回にもう一度取る（直っていなければまた失敗し、通知は出ない。処理済みの出来事は取らない）
    again = dispatch(db)
    assert again.notifications == 0 and broken.id in again.failed_events
