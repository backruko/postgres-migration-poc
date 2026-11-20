# tests/test_notification_settings.py
"""
通知の設定（宛先・ルール）のテスト。
- 顧客の範囲: 範囲のある業務管理者は自分の顧客のものだけ作り・変える。全顧客のものは見えるが変えられない（403）。ほかの顧客は 404
- 削除・元に戻す: ルールが使う宛先は削除できない（details にルール）。宛先が削除されたルールは元に戻せない（details に宛先）
- 宛先のアドレスを直すと、再試行中の配信も新しいアドレスへ。顧客を変えるのは使っているルールと合うときだけ
- テスト送信: 出来事 → ルールなしの通知 → 配信（1 回だけ）。結果は宛先の last_test。届かなかったアラームの帯には出ない
- ルールの変更は、変えた後の出来事から。システムの出来事はルールで選べない
"""
import re
import uuid
from dataclasses import replace
from datetime import time, timedelta

import pytest
from sqlalchemy import select

from tests.client_codes import new_client_code
from tests.test_notification_delivery import MIN, NOON, db, notification_for, run  # noqa: F401  同じ準備（外側のトランザクション）
from src.core.exceptions import (
    BadRequestError, DuplicatedRecordError, ForbiddenError, NotFoundError, RecordReferencedException,
)
from src.domain_biz.schemas import BizMasterCreate
from src.domain_client.schemas import ClientCreate
from src.domain_events.formats import EventType
from src.domain_events.models import DomainEvent
from src.domain_notification import services
from src.domain_notification.dispatcher import dispatch_events
from src.domain_notification.failures import get_failures
from src.domain_notification.models import DeliveryStatus, Notification, NotificationDelivery, NotificationStatus, QuietMode
from src.domain_notification.settings_views import describe_destinations, describe_rules
from src.domain_notification.templates import ORDER_DEADLINE, TEMPLATES
from src.domain_staff import services as staff_services
from src.domain_staff.models import RoleCode, StaffStatus
from src.domain_staff.schemas import StaffCreate
from src.domain_staff.scope import ClientScope
from src.infrastructure.repositories import biz_master_repo, client_repo


def new_client(db) -> dict:
    code = new_client_code("O")
    client_repo.create(db, obj_in=ClientCreate(code=code, name="設定の顧客"))
    biz = biz_master_repo.create(db, obj_in=BizMasterCreate(client_code=code, name="設定の業務", business_seq=1, manager_email=None, description=None))
    return {"code": code, "biz": biz.id, "biz_code": biz.business_code}


def admin(db, name: str = "設定する人"):
    return staff_services.create_staff(
        db, StaffCreate(login_id="ns-" + uuid.uuid4().hex[:10], name=name, roles=[RoleCode.ADMIN]), actor=None, status=StaffStatus.ACTIVE,
    )


def dest(db, client_code=None, address="ops@example.com", scope=ClientScope(), **kw):
    return services.create_destination(db, name="宛先 " + uuid.uuid4().hex[:8], address=address, client_code=client_code, scope=scope, **kw)


def rule(db, client_code, destinations, scope=ClientScope(), **kw):
    data = dict(name="ルール " + uuid.uuid4().hex[:6], event_types=[EventType.ORDER_LATE], destination_ids=[d.id for d in destinations],
                template_code=ORDER_DEADLINE, client_code=client_code, scope=scope)
    data.update(kw)
    return services.create_rule(db, **data)  # pyright: ignore[reportArgumentType]


# ==========================================
# 顧客の範囲
# ==========================================
def test_a_limited_admin_changes_only_their_clients_settings(db):
    a, b = new_client(db), new_client(db)
    scope = ClientScope.of([a["code"]])
    shared = dest(db)  # 全顧客の宛先（全顧客の業務管理者が作った）
    other = dest(db, b["code"])

    mine = dest(db, a["code"], scope=scope)
    with pytest.raises(NotFoundError):
        dest(db, b["code"], scope=scope)                      # ほかの顧客
    with pytest.raises(ForbiddenError):
        dest(db, None, scope=scope)                           # 全顧客の宛先は作れない
    r = rule(db, a["code"], [mine, shared], scope=scope)      # 全顧客の宛先は使える
    with pytest.raises(ForbiddenError):
        rule(db, None, [shared], scope=scope)
    with pytest.raises(ForbiddenError):
        services.update_destination(db, shared.id, name=shared.name, address="x@example.com", client_code=None, owner=None,
                                    enabled=True, actor_id=None, scope=scope)
    with pytest.raises(NotFoundError):
        services.delete_destination(db, other.id, actor_id=None, scope=scope)
    # 業務を選ぶと、ルールはその業務の顧客のもの（範囲の確かめもそれで）
    with pytest.raises(NotFoundError):
        rule(db, None, [shared], scope=scope, biz_master_id=b["biz"])
    by_biz = rule(db, None, [shared], biz_master_id=a["biz"])
    assert by_biz.client_code == a["code"]

    visible = {d.id for d in services.list_destinations(db, scope=scope)}
    assert {mine.id, shared.id} <= visible and other.id not in visible
    rules = {x.id for x in services.list_rules(db, scope=scope)}
    assert r.id in rules
    with pytest.raises(NotFoundError):
        services.get_destination(db, other.id, scope=scope)


# ==========================================
# 削除・元に戻す
# ==========================================
def test_delete_and_restore(db):
    a = new_client(db)
    d = dest(db, a["code"])
    r = rule(db, a["code"], [d])
    with pytest.raises(RecordReferencedException) as refused:
        services.delete_destination(db, d.id, actor_id=None)
    assert refused.value.details == {"rules": [{"id": r.id, "name": r.name}]}

    services.delete_rule(db, r.id, actor_id=None)
    services.delete_destination(db, d.id, actor_id=None)  # 削除したルールだけなら消せる
    assert d.is_deleted and d.id not in {x.id for x in services.list_destinations(db)}
    assert d.id in {x.id for x in services.list_destinations(db, include_deleted=True)}

    with pytest.raises(BadRequestError) as blocked:
        services.restore_rule(db, r.id, actor_id=None)  # 宛先が削除されている
    assert blocked.value.details == {"destinations": [{"id": d.id, "name": d.name}]}
    services.restore_destination(db, d.id, actor_id=None)
    services.restore_rule(db, r.id, actor_id=None)
    assert not d.is_deleted and not r.is_deleted

    twin = dest(db)
    services.delete_destination(db, twin.id, actor_id=None)
    services.create_destination(db, name=twin.name, address="y@example.com")  # 削除した名前は使える
    with pytest.raises(DuplicatedRecordError):
        services.restore_destination(db, twin.id, actor_id=None)  # 同じ名前の宛先が生きている


def test_changing_a_destinations_client_must_fit_its_rules(db):
    a, b = new_client(db), new_client(db)
    shared = dest(db)
    rule(db, a["code"], [shared])
    with pytest.raises(BadRequestError) as clash:
        services.update_destination(db, shared.id, name=shared.name, address=shared.address, client_code=b["code"],
                                    owner=None, enabled=True, actor_id=None)
    assert [x["name"] for x in clash.value.details["rules"]]  # pyright: ignore[reportOptionalSubscript]
    services.update_destination(db, shared.id, name=shared.name, address=shared.address, client_code=a["code"],
                                owner="運用", enabled=True, actor_id=None)
    assert (shared.client_code, shared.owner) == (a["code"], "運用")


# ==========================================
# 宛先を直すと、再試行中の配信も救える
# ==========================================
def test_fixing_an_address_rescues_retrying_deliveries(db):
    a = new_client(db)
    n = notification_for(db, a, ["fail-temp@example.com"])
    run(db, NOON)
    [d] = n.deliveries
    db.refresh(d)
    assert d.status == DeliveryStatus.RETRYING
    destination = services.get_destination(db, d.destination_id)
    services.update_destination(db, destination.id, name=destination.name, address="ops@example.com", client_code=None,
                                owner=None, enabled=True, actor_id=None)
    db.commit()
    run(db, NOON + MIN)
    db.refresh(d)
    assert (d.status, d.attempts) == (DeliveryStatus.SENT, 2)


# ==========================================
# テスト送信
# ==========================================
def test_a_test_send_goes_the_real_way_once(db):
    actor = admin(db, name="試す人")
    ok, perm, temp = dest(db), dest(db, address="fail-perm@example.com"), dest(db, address="fail-temp@example.com")
    db.commit()
    events = {d.id: services.request_destination_test(db, d.id, actor=actor) for d in (ok, perm, temp)}
    db.commit()
    event = db.get(DomainEvent, events[ok.id])
    assert event is not None and event.event_type == EventType.DESTINATION_TEST
    assert event.payload["requested_by_name"] == "試す人" and event.subject_id == ok.id

    before = {x.id: x.last_test for x in describe_destinations(db, [ok])}
    waiting = before[ok.id]
    assert waiting is not None and waiting.state == "waiting"  # ワーカー待ち

    dispatch_events(db, now=NOON, batch_size=100_000)
    db.commit()
    tests = list(db.scalars(select(Notification).where(Notification.event_id.in_(events.values()))))
    assert len(tests) == 3 and all(t.rule_id is None and t.subject.startswith("【テスト】") for t in tests)
    assert describe_destinations(db, [ok])[0].last_test.state == "sending"  # pyright: ignore[reportOptionalMemberAccess]

    run(db, NOON)
    results = {x.id: x.last_test for x in describe_destinations(db, [ok, perm, temp])}
    assert results[ok.id].state == "sent" and results[ok.id].sent_at == NOON  # pyright: ignore[reportOptionalMemberAccess]
    assert results[perm.id].state == "failed" and "does not exist" in (results[perm.id].error or "")  # pyright: ignore[reportOptionalMemberAccess]
    # 一時的な失敗でも再試行しない（頼んだ人に、すぐ結果を見せる）
    assert results[temp.id].state == "failed" and "not answering" in (results[temp.id].error or "")  # pyright: ignore[reportOptionalMemberAccess]
    temp_delivery = db.scalar(select(NotificationDelivery).where(NotificationDelivery.destination_id == temp.id))
    assert temp_delivery is not None and temp_delivery.attempts == 1

    # 届かなかったテストは、届かなかったアラームの帯に出さない
    failures = get_failures(db, now=NOON)
    assert not {t.id for t in tests} & {i.id for i in failures.items}


def test_a_test_needs_an_enabled_destination(db):
    actor = admin(db)
    off = dest(db)
    off.enabled = False
    db.commit()
    with pytest.raises(BadRequestError):
        services.request_destination_test(db, off.id, actor=actor)


def test_a_destination_disabled_before_the_worker_runs(db):
    actor = admin(db)
    d = dest(db)
    db.commit()
    event_id = services.request_destination_test(db, d.id, actor=actor)
    d.enabled = False
    db.commit()
    dispatch_events(db, now=NOON, batch_size=100_000)
    db.commit()
    n = db.scalar(select(Notification).where(Notification.event_id == event_id))
    assert n is not None and n.status == NotificationStatus.SUPPRESSED and n.deliveries == []
    assert describe_destinations(db, [d])[0].last_test.state == "failed"  # pyright: ignore[reportOptionalMemberAccess]


# ==========================================
# ルールの変更・選べないもの・見せ方
# ==========================================
def test_rule_changes_apply_to_later_events_only(db):
    a = new_client(db)
    first = notification_for(db, a, ["ops@example.com"])
    r = services.get_rule(db, first.rule_id)  # pyright: ignore[reportArgumentType]
    other = dest(db, address="team@example.com")
    services.update_rule(db, r.id, name=r.name, event_types=[EventType.ORDER_LATE], destination_ids=[other.id],
                         template_code=ORDER_DEADLINE, client_code=a["code"], biz_master_id=None, dedupe_window_minutes=30,
                         quiet_start=time(22, 0), quiet_end=time(6, 0), quiet_mode=QuietMode.HOLD, enabled=True, actor_id=None)
    db.commit()
    assert [d.destination_id for d in first.deliveries] != [other.id]  # 作った通知はそのまま
    [view] = describe_rules(db, [r])
    assert [x.id for x in view.destinations] == [other.id] and view.dedupe_window_minutes == 30 and view.quiet_mode == QuietMode.HOLD


def test_system_events_cannot_be_chosen_by_rules(db):
    d = dest(db)
    with pytest.raises(BadRequestError):
        rule(db, None, [d], event_types=[EventType.DESTINATION_TEST])


def test_views_carry_names(db):
    a = new_client(db)
    actor = admin(db, name="作った人")
    d = dest(db, actor_id=actor.id)
    r = rule(db, a["code"], [d], biz_master_id=a["biz"], actor_id=actor.id)
    [dv] = describe_destinations(db, [d])
    assert dv.created_by is not None and dv.created_by.name == "作った人" and [u.id for u in dv.used_by] == [r.id]
    [rv] = describe_rules(db, [r])
    assert rv.biz_name == "設定の業務" and [x.name for x in rv.destinations] == [d.name] and rv.updated_by.name == "作った人"  # pyright: ignore[reportOptionalMemberAccess]
    assert timedelta(0) <= rv.updated_at - rv.created_at


# ==========================================
# ルールの項目の確かめ（_validated_rule を分ける前に、今の決まりを固定する）
# ==========================================
NO_SUCH_ID = 999_999_999


def test_each_rule_check_refuses_with_its_own_message(db, monkeypatch):
    a, b = new_client(db), new_client(db)
    d, other = dest(db, a["code"]), dest(db, b["code"])
    # 今の文面はすべての種類を書けるので、「書けない種類」を確かめるために狭い文面を 1 つ足す
    late_only = replace(TEMPLATES[ORDER_DEADLINE], code="late_only", event_types=frozenset({EventType.ORDER_LATE}))
    monkeypatch.setitem(TEMPLATES, "late_only", late_only)
    cases = [
        (dict(name="  "), BadRequestError, "A rule needs a name."),
        (dict(event_types=[]), BadRequestError, "A rule needs at least one event type."),
        (dict(event_types=["NoSuchEvent", EventType.ORDER_LATE]), BadRequestError, "Unknown event types: NoSuchEvent."),
        (dict(event_types=[EventType.DESTINATION_TEST]), BadRequestError,
         f"Unknown event types: {', '.join([EventType.DESTINATION_TEST])}."),
        (dict(template_code="nope"), BadRequestError, "Unknown template 'nope'."),
        (dict(event_types=[EventType.ORDER_LATE, EventType.ORDER_DUE_SOON], template_code="late_only"), BadRequestError,
         f"The template 'late_only' cannot describe: {', '.join([EventType.ORDER_DUE_SOON])}."),
        (dict(dedupe_window_minutes=0), BadRequestError, "The dedupe window must be a positive number of minutes."),
        (dict(quiet_start=time(22)), BadRequestError, "Quiet hours need a start, an end and a mode."),
        (dict(quiet_start=time(22), quiet_end=time(22), quiet_mode=QuietMode.HOLD), BadRequestError,
         "Quiet hours must not start and end at the same time."),
        (dict(client_code="zz-none"), NotFoundError, "Client 'ZZ-NONE' not found."),
        (dict(biz_master_id=NO_SUCH_ID), NotFoundError, f"BizMaster(id={NO_SUCH_ID}) not found."),
        (dict(biz_master_id=b["biz"]), BadRequestError, "The business belongs to another client."),
        (dict(destination_ids=[]), BadRequestError, "A rule needs at least one destination."),
        (dict(destination_ids=[d.id, NO_SUCH_ID]), NotFoundError, f"Destinations not found: [{NO_SUCH_ID}]."),
        (dict(destination_ids=[d.id, other.id]), BadRequestError, f"Destinations of another client cannot be used: {other.name}."),
    ]
    for overrides, error, message in cases:
        client_code = overrides.pop("client_code", a["code"])
        with pytest.raises(error, match=f"^{re.escape(message)}$"):
            rule(db, client_code, [d], **overrides)
    # 直すときも同じ決まり
    r = rule(db, a["code"], [d])
    with pytest.raises(BadRequestError, match=r"^A rule needs a name\.$"):
        services.update_rule(
            db, r.id, name=" ", event_types=[EventType.ORDER_LATE], destination_ids=[d.id], template_code=ORDER_DEADLINE,
            client_code=a["code"], biz_master_id=None, dedupe_window_minutes=None, quiet_start=None, quiet_end=None,
            quiet_mode=None, enabled=True, actor_id=None,
        )


def test_rule_checks_report_the_first_problem_in_a_fixed_order(db):
    # 名前 → 出来事の種類 → 文面 → 重複の間隔・夜間 → 顧客・業務 → 宛先 の順に確かめ、最初の 1 つだけを返す
    a = new_client(db)
    d = dest(db, a["code"])
    values = dict(name=" ", event_types=[], template_code="nope", dedupe_window_minutes=0, quiet_start=time(1),
                  client_code="zz-none", biz_master_id=NO_SUCH_ID, destination_ids=[])
    fixes = [
        ("A rule needs a name.", dict(name="順番")),
        ("A rule needs at least one event type.", dict(event_types=[EventType.ORDER_LATE])),
        ("Unknown template 'nope'.", dict(template_code=ORDER_DEADLINE)),
        ("The dedupe window must be a positive number of minutes.", dict(dedupe_window_minutes=None)),
        ("Quiet hours need a start, an end and a mode.", dict(quiet_start=None)),
        ("Client 'ZZ-NONE' not found.", dict(client_code=a["code"])),
        (f"BizMaster(id={NO_SUCH_ID}) not found.", dict(biz_master_id=None)),
        ("A rule needs at least one destination.", dict(destination_ids=[d.id])),
    ]
    for message, fix in fixes:
        with pytest.raises((BadRequestError, NotFoundError), match=f"^{re.escape(message)}$"):
            services.create_rule(db, **values)  # pyright: ignore[reportArgumentType]
        values.update(fix)
    assert services.create_rule(db, **values).name == "順番"  # pyright: ignore[reportArgumentType]


def test_rule_values_are_cleaned_up_before_saving(db):
    a = new_client(db)
    first, second = dest(db, a["code"]), dest(db)
    r = rule(db, None, [second, first], name="  夜の遅れ  ", biz_master_id=a["biz"],
             event_types=[EventType.ORDER_LATE, EventType.ORDER_DUE_SOON, EventType.ORDER_LATE])
    assert r.name == "夜の遅れ"                                              # 前後の空白を外す
    assert r.event_types == sorted({EventType.ORDER_LATE, EventType.ORDER_DUE_SOON})  # 重ねずに並べる
    assert r.client_code == a["code"]                                         # 業務で絞るルールは、その業務の顧客のもの
    assert [x.id for x in r.destinations] == sorted([first.id, second.id])    # 宛先は id の順
    lower = rule(db, a["code"].lower(), [first])
    assert lower.client_code == a["code"]                                     # 顧客コードは大文字にそろえる
