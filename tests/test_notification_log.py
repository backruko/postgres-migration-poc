# tests/test_notification_log.py
"""
通知の記録のテスト。
- 1 回ごとの試み: 送ったアドレス・結果・返事。アドレスを直した後の再試行は新しいアドレス（記録で確かめられる）
- 絞り込み: 種類（アラーム / テスト送信）・状態・出来事（出来事 → 通知のつながり）・オーダー・ルール・顧客・期間・件数
- 顧客の範囲: ほかの顧客のもの・顧客のないテスト送信は出ない。1 件の詳細は本文も、範囲の外は 404
- 知らせなかった通知は理由つきで、配信なし
"""
import uuid
from datetime import datetime, timedelta
from typing import Any

import pytest
from tests.client_codes import new_client_code
from tests.test_notification_delivery import JST, MIN, NOON, db, notification_for, run  # noqa: F401  同じ準備（外側のトランザクション）
from src.core.exceptions import BadRequestError, NotFoundError
from src.domain_biz.schemas import BizMasterCreate
from src.domain_client.schemas import ClientCreate
from src.domain_events.models import DomainEvent
from src.domain_notification import services
from src.domain_notification.dispatcher import dispatch_events
from src.domain_notification.log import get_log_entry, list_log
from src.domain_notification.models import DeliveryStatus, NotificationStatus
from src.domain_staff import services as staff_services
from src.domain_staff.models import RoleCode, StaffStatus
from src.domain_staff.schemas import StaffCreate
from src.domain_staff.scope import ClientScope
from src.infrastructure.repositories import biz_master_repo, client_repo


def new_client(db) -> dict:
    code = new_client_code("O")
    client_repo.create(db, obj_in=ClientCreate(code=code, name="記録の顧客"))
    biz = biz_master_repo.create(db, obj_in=BizMasterCreate(client_code=code, name="記録の業務", business_seq=1, manager_email=None, description=None))
    return {"code": code, "biz": biz.id, "biz_code": biz.business_code}


def only(response, ids) -> list:
    return [i for i in response.items if i.id in ids]


def test_each_attempt_records_the_address_it_used(db):
    a = new_client(db)
    n = notification_for(db, a, ["fail-temp@example.com"])
    run(db, NOON)
    [d] = n.deliveries
    destination = services.get_destination(db, d.destination_id)
    services.update_destination(db, destination.id, name=destination.name, address="ops@example.com", client_code=None,
                                owner=None, enabled=True, actor_id=None)
    db.commit()
    run(db, NOON + MIN)

    [entry] = only(list_log(db, scope=ClientScope.of([a["code"]])), {n.id})
    [delivery] = entry.deliveries
    assert (delivery.status, delivery.attempts, delivery.address) == (DeliveryStatus.SENT, 2, "ops@example.com")
    assert [(x.attempt_no, x.address, x.ok, x.permanent) for x in delivery.attempt_log] == [
        (1, "fail-temp@example.com", False, False), (2, "ops@example.com", True, False),
    ]
    assert "not answering" in (delivery.attempt_log[0].error or "") and delivery.attempt_log[1].error is None
    assert (delivery.attempt_log[1].provider_message_id or "").startswith("fake:")
    assert (entry.status, entry.is_test, entry.event_type, entry.client_name) == (NotificationStatus.SENT, False, "OrderLate", "配信の顧客")  # 出来事の写しの名前


def test_a_permanent_failure_is_marked(db):
    a = new_client(db)
    n = notification_for(db, a, ["fail-perm@example.com"])
    run(db, NOON)
    [entry] = only(list_log(db, status=NotificationStatus.FAILED), {n.id})
    assert [(x.ok, x.permanent) for x in entry.deliveries[0].attempt_log] == [(False, True)]


def test_filters_link_events_orders_rules_and_kinds(db):
    a, b = new_client(db), new_client(db)
    first = notification_for(db, a, ["ops@example.com"])
    second = notification_for(db, b, ["ops@example.com"])
    actor = staff_services.create_staff(db, StaffCreate(login_id="nl-" + uuid.uuid4().hex[:8], name="試す人", roles=[RoleCode.ADMIN]),
                                        actor=None, status=StaffStatus.ACTIVE)
    shared = services.create_destination(db, name="記録の全体 " + uuid.uuid4().hex[:6], address="ops@example.com")
    db.commit()
    services.request_destination_test(db, shared.id, actor=actor)
    db.commit()
    dispatch_events(db, now=NOON + MIN, batch_size=100_000)
    db.commit()
    run(db, NOON + MIN)
    ours = {first.id, second.id}

    by_event = list_log(db, event_id=first.event_id)
    assert [i.id for i in by_event.items] == [first.id]  # 出来事 → 通知
    event = db.get(DomainEvent, first.event_id)
    assert event is not None and [i.id for i in list_log(db, order_id=event.subject_id).items] == [first.id]
    assert [i.id for i in list_log(db, rule_id=first.rule_id).items] == [first.id]
    assert [i.id for i in only(list_log(db, client_code=b["code"].lower()), ours)] == [second.id]

    tests = list_log(db, kind="test", created_from=NOON + MIN - timedelta(seconds=1))
    assert tests.items and all(i.is_test and i.rule_id is None and i.rule_name is None for i in tests.items)
    assert not {i.id for i in tests.items} & ours
    assert not [i for i in list_log(db, kind="alarm").items if i.is_test]
    with pytest.raises(BadRequestError):
        list_log(db, kind="everything")

    # 範囲: ほかの顧客のもの・顧客のない（全顧客の宛先の）テスト送信は出ない
    scoped = list_log(db, scope=ClientScope.of([a["code"]]))
    assert {i.id for i in scoped.items} == {first.id}
    with pytest.raises(NotFoundError):
        get_log_entry(db, second.id, scope=ClientScope.of([a["code"]]))
    detail = get_log_entry(db, first.id, scope=ClientScope.of([a["code"]]))
    assert detail.body.startswith("締切（") and detail.subject == first.subject


def test_period_newest_first_and_limit(db):
    a = new_client(db)
    early = notification_for(db, a, ["ops@example.com"], now=NOON)
    late = notification_for(db, new_client(db), ["ops@example.com"], now=NOON + 10 * MIN)
    newest = list_log(db, created_from=NOON, created_to=NOON + 11 * MIN)
    mine = [i.id for i in newest.items if i.id in {early.id, late.id}]
    assert mine == [late.id, early.id]
    # 12:05 日本時間（タイムゾーンなし）以降 = 遅いほうだけ
    naive = list_log(db, created_from=datetime(2026, 11, 12, 12, 5), created_to=NOON + 11 * MIN)
    assert late.id in {i.id for i in naive.items} and early.id not in {i.id for i in naive.items}
    one = list_log(db, created_from=NOON, created_to=NOON + 11 * MIN, limit=1)
    assert len(one.items) == 1 and one.truncated
    with pytest.raises(BadRequestError):
        list_log(db, created_from=NOON, created_to=NOON)


def test_suppressed_alarms_show_their_reason(db):
    a = new_client(db)
    rule_owner = notification_for(db, a, ["ops@example.com"])  # 1 つ目のルール。その宛先を止めて、次の出来事を待つ
    for d in rule_owner.deliveries:
        dest = services.get_destination(db, d.destination_id)
        dest.enabled = False
    db.commit()
    again = notification_for(db, a, ["team@example.com"])  # 2 つ目のルール: 1 つ目のルールは宛先が止まっていて抑止
    suppressed = [
        i for i in list_log(db, status=NotificationStatus.SUPPRESSED, scope=ClientScope.of([a["code"]])).items
        if i.event_id == again.event_id
    ]
    assert suppressed and suppressed[0].suppressed_reason == "no_destination" and suppressed[0].deliveries == []
    assert suppressed[0].rule_id == rule_owner.rule_id


def test_refusals_have_exact_messages_and_the_period_is_checked_before_the_kind(db):
    # C901（複雑さの上限）で list_log を分ける前に固定する: 断るときの文面と、確かめる順番（期間 → 種類）
    with pytest.raises(BadRequestError, match=r"^'from' must be earlier than 'to'\.$"):
        list_log(db, created_from=NOON + MIN, created_to=NOON, kind="everything")
    with pytest.raises(BadRequestError, match=r"^kind must be one of alarm, test\.$"):
        list_log(db, kind="everything")
    # 片側だけの期間は断らない
    list_log(db, created_from=NOON)
    list_log(db, created_to=NOON)


def test_filters_combine_with_and(db):
    a, b = new_client(db), new_client(db)
    first = notification_for(db, a, ["ops@example.com"], now=NOON)
    second = notification_for(db, b, ["ops@example.com"], now=NOON + MIN)
    ours = {first.id, second.id}

    def ids(**filters: Any) -> list[int]:
        """この 2 件の時間帯だけを見て、絞り込んだ結果のうち、この 2 件の id（新しい順）"""
        return [i.id for i in only(list_log(db, created_from=NOON - MIN, created_to=NOON + 2 * MIN, **filters), ours)]

    assert ids(kind="alarm", client_code=f" {a['code'].lower()} ") == [first.id]
    assert ids(client_code=a["code"], rule_id=second.rule_id) == []
    assert ids(event_id=first.event_id, rule_id=second.rule_id) == []
    assert ids(kind="test", client_code=a["code"]) == []
    assert ids(status=second.status, client_code=b["code"]) == [second.id]
    assert ids(status=NotificationStatus.SUPPRESSED, client_code=b["code"]) == []
