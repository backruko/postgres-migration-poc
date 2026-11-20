# tests/test_notification_failures.py
"""
届かなかったアラームの一覧と「確認した」（通知の仕組み）のテスト。
- 一覧: failed / partly_failed（宛先ごとの結果と理由・出来事の種類・顧客名・オーダー・予定日・ルール名）。届いたものは出ない
- 件数: まだ確認されていないもの。再試行中の配信の数と、いちばん新しい失敗の理由
- 確認: だれが・いつ。2 回目は最初のまま。確認したものは 7 日で一覧から消え、確認していないものは古くても残る
- 届いた通知は確認できない（400）。範囲の外・ないものは 404
- 削除したルール・宛先も、そのときの名前で出る
"""
import uuid
from datetime import timedelta

import pytest

from tests.client_codes import new_client_code
from tests.test_notification_delivery import NOON, db, notification_for, run  # noqa: F401  同じ準備を使う
from src.domain_biz.schemas import BizMasterCreate
from src.domain_client.schemas import ClientCreate
from src.infrastructure.repositories import biz_master_repo, client_repo
from src.core.exceptions import BadRequestError, NotFoundError
from src.domain_notification.failures import acknowledge_failure, get_failures
from src.domain_notification.models import (
    DeliveryStatus, NotificationDestination, NotificationRule, NotificationStatus,
)
from src.domain_staff import services as staff_services
from src.domain_staff.models import RoleCode, StaffStatus
from src.domain_staff.schemas import StaffCreate
from src.domain_staff.scope import ClientScope


def staff(db, *roles: RoleCode, name: str = "確認する人"):
    return staff_services.create_staff(
        db, StaffCreate(login_id="nf-" + uuid.uuid4().hex[:10], name=name, roles=list(roles)), actor=None, status=StaffStatus.ACTIVE,
    )


def new_client(db) -> dict:
    code = new_client_code("O")
    client_repo.create(db, obj_in=ClientCreate(code=code, name="配信の顧客"))
    biz = biz_master_repo.create(db, obj_in=BizMasterCreate(client_code=code, name="配信の業務", business_seq=1, manager_email=None, description=None))
    return {"code": code, "biz": biz.id, "biz_code": biz.business_code}


@pytest.fixture
def failures(db):
    """
    届かなかった・一部届かなかった・届いた・再試行中の通知を 1 つずつ（すべて配信まで済ませる）。
    顧客を分ける: 同じ顧客のルールは、その顧客のどの出来事にも当てはまるため
    """
    clients = [new_client(db) for _ in range(4)]
    failed = notification_for(db, clients[0], ["fail-perm@example.com"])
    partly = notification_for(db, clients[1], ["fail-perm@example.com", "ops@example.com"])
    sent = notification_for(db, clients[2], ["ops@example.com"])
    retrying = notification_for(db, clients[3], ["fail-temp@example.com"])
    run(db, NOON)
    return {"failed": failed, "partly": partly, "sent": sent, "retrying": retrying,
            "scope": ClientScope.of([c["code"] for c in clients])}


def test_failed_alarms_are_listed_with_their_deliveries(db, failures):
    result = get_failures(db, scope=failures["scope"], now=NOON)
    by_id = {i.id: i for i in result.items}
    assert set(by_id) == {failures["failed"].id, failures["partly"].id}  # 届いた・再試行中は出ない
    assert result.unacknowledged == 2 and not result.truncated
    assert result.retrying == 1 and "not answering" in (result.last_retry_error or "") and result.last_retry_at == NOON

    failed = by_id[failures["failed"].id]
    assert (failed.status, failed.event_type, failed.client_name, failed.subject) == (
        NotificationStatus.FAILED, "OrderLate", "配信の顧客", failures["failed"].subject,
    )
    assert failed.order_id is not None and failed.occurrence_date is not None and failed.rule_name.startswith("配信のルール")
    [d] = failed.deliveries
    assert (d.address, d.status, d.attempts) == ("fail-perm@example.com", DeliveryStatus.FAILED, 1)
    assert "does not exist" in (d.last_error or "") and d.destination_name.startswith("配信先")
    assert failed.acknowledged_at is None and failed.acknowledged_by_name is None

    partly = by_id[failures["partly"].id]
    assert partly.status == NotificationStatus.PARTLY_FAILED
    assert sorted((x.address, x.status) for x in partly.deliveries) == [
        ("fail-perm@example.com", DeliveryStatus.FAILED), ("ops@example.com", DeliveryStatus.SENT),
    ]


def test_confirming_records_who_and_when_and_keeps_the_first(db, failures):
    first = staff(db, RoleCode.OPERATOR, name="山田")
    second = staff(db, RoleCode.ADMIN, name="佐藤")
    db.commit()
    done = acknowledge_failure(db, failures["failed"].id, actor=first, scope=failures["scope"])
    assert done.acknowledged_by_name == "山田" and done.acknowledged_at is not None
    again = acknowledge_failure(db, failures["failed"].id, actor=second, scope=failures["scope"])
    assert (again.acknowledged_by_id, again.acknowledged_at) == (first.id, done.acknowledged_at)

    now = done.acknowledged_at
    listed = get_failures(db, scope=failures["scope"], now=now)
    assert listed.unacknowledged == 1  # 一部届かなかったものだけ
    assert {i.id for i in listed.items} == {failures["failed"].id, failures["partly"].id}  # 確認したものも 7 日は残る

    later = get_failures(db, scope=failures["scope"], now=now + timedelta(days=8))
    assert [i.id for i in later.items] == [failures["partly"].id]  # 確認したものは消え、確認していないものは古くても残る
    assert later.unacknowledged == 1


def test_only_failed_alarms_in_scope_can_be_confirmed(db, failures):
    actor = staff(db, RoleCode.OPERATOR)
    db.commit()
    with pytest.raises(BadRequestError):
        acknowledge_failure(db, failures["sent"].id, actor=actor, scope=failures["scope"])
    with pytest.raises(BadRequestError):
        acknowledge_failure(db, failures["retrying"].id, actor=actor, scope=failures["scope"])  # まだ再試行中
    with pytest.raises(NotFoundError):
        acknowledge_failure(db, failures["failed"].id, actor=actor, scope=ClientScope.of(["ZZZ9"]))  # 範囲の外
    with pytest.raises(NotFoundError):
        acknowledge_failure(db, 987654321, actor=actor, scope=failures["scope"])
    assert get_failures(db, scope=ClientScope.of(["ZZZ9"]), now=NOON).items == []


def test_deleted_rules_and_destinations_keep_their_names(db, failures):
    item = next(i for i in get_failures(db, scope=failures["scope"], now=NOON).items if i.id == failures["failed"].id)
    rule = db.get(NotificationRule, item.rule_id)
    destination = db.get(NotificationDestination, item.deliveries[0].destination_id)
    assert rule is not None and destination is not None
    rule.is_deleted = destination.is_deleted = True
    db.commit()
    again = next(i for i in get_failures(db, scope=failures["scope"], now=NOON).items if i.id == failures["failed"].id)
    assert (again.rule_name, again.deliveries[0].destination_name) == (item.rule_name, item.deliveries[0].destination_name)
