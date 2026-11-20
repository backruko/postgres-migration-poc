"""
通知の設定の画面向けの形。宛先・ルールに、作った・変えた職員の名前、使っているルール、最後のテスト送信の結果、
業務の名前を付ける。1 件ずつ問い合わせず、まとめて読む（一覧が大きくなっても問い合わせの数が増えない）。
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.domain_biz.models import BizMaster
from src.domain_events.formats import EventType
from src.domain_events.models import DomainEvent
from src.domain_notification.models import (
    DeliveryStatus, Notification, NotificationDestination, NotificationRule, NotificationStatus, rule_destination,
)
from src.domain_notification.schemas import (
    DestinationOut, EventTypeOption, LastTest, NamedRef, NotificationOptions, RuleOut, StaffRef, TemplateOption,
)
from src.domain_notification.templates import TEMPLATES
from src.domain_staff.models import StaffUser

# 画面に出す出来事の種類（ルールで選べるもの。システムの出来事は入れない）
EVENT_TYPE_LABELS = {EventType.ORDER_LATE: "遅れ", EventType.ORDER_DUE_SOON: "まもなく"}


def options() -> NotificationOptions:
    return NotificationOptions(
        event_types=[EventTypeOption(code=code, label=label) for code, label in EVENT_TYPE_LABELS.items()],
        templates=[TemplateOption(code=t.code, name=t.name, event_types=sorted(t.event_types)) for t in TEMPLATES.values()],
    )


def _staff(db: Session, ids: set[int | None]) -> dict[int, StaffRef]:
    wanted = {i for i in ids if i is not None}
    if not wanted:
        return {}
    return {row.id: StaffRef(id=row.id, name=row.name) for row in db.execute(select(StaffUser.id, StaffUser.name).where(StaffUser.id.in_(wanted)))}


def _last_tests(db: Session, destination_ids: list[int]) -> dict[int, LastTest]:
    """宛先ごとの、最後のテスト送信（出来事 → 通知 → 配信）"""
    if not destination_ids:
        return {}
    events = db.scalars(
        select(DomainEvent)
        .where(DomainEvent.event_type == EventType.DESTINATION_TEST, DomainEvent.subject_type == "destination",
               DomainEvent.subject_id.in_(destination_ids))
        .order_by(DomainEvent.id.desc())
    )
    latest: dict[int, DomainEvent] = {}
    for e in events:
        if e.subject_id is not None:
            latest.setdefault(e.subject_id, e)
    notifications = {n.event_id: n for n in db.scalars(select(Notification).where(Notification.event_id.in_([e.id for e in latest.values()])))}
    out: dict[int, LastTest] = {}
    for destination_id, e in latest.items():
        n = notifications.get(e.id)
        state, sent_at, error = "waiting", None, None
        if n is not None:
            if n.status == NotificationStatus.SUPPRESSED:
                state, error = "failed", "the destination was disabled or deleted"
            elif n.deliveries:
                d = n.deliveries[0]
                sent_at, error = d.sent_at, d.last_error
                state = {DeliveryStatus.SENT: "sent", DeliveryStatus.FAILED: "failed"}.get(d.status, "sending")
        out[destination_id] = LastTest(
            requested_at=e.occurred_at, requested_by_name=e.payload.get("requested_by_name"), state=state, sent_at=sent_at, error=error,
        )
    return out


def describe_destinations(db: Session, destinations: list[NotificationDestination]) -> list[DestinationOut]:
    ids = [d.id for d in destinations]
    staff = _staff(db, {d.created_by_id for d in destinations} | {d.updated_by_id for d in destinations})
    used: dict[int, list[NamedRef]] = {}
    if ids:
        rows = db.execute(
            select(rule_destination.c.destination_id, NotificationRule.id, NotificationRule.name)
            .join(NotificationRule, NotificationRule.id == rule_destination.c.rule_id)
            .where(rule_destination.c.destination_id.in_(ids), NotificationRule.is_deleted.is_(False))
            .order_by(NotificationRule.name)
        )
        for destination_id, rule_id, name in rows:
            used.setdefault(destination_id, []).append(NamedRef(id=rule_id, name=name))
    tests = _last_tests(db, ids)
    return [
        DestinationOut(
            id=d.id, name=d.name, channel=d.channel.value, address=d.address, client_code=d.client_code, owner=d.owner,
            enabled=d.enabled, is_deleted=d.is_deleted, used_by=used.get(d.id, []), last_test=tests.get(d.id),
            created_at=d.created_at, created_by=staff.get(d.created_by_id) if d.created_by_id else None,
            updated_at=d.updated_at, updated_by=staff.get(d.updated_by_id) if d.updated_by_id else None,
        )
        for d in destinations
    ]


def describe_rules(db: Session, rules: list[NotificationRule]) -> list[RuleOut]:
    staff = _staff(db, {r.created_by_id for r in rules} | {r.updated_by_id for r in rules})
    biz_ids = {r.biz_master_id for r in rules if r.biz_master_id}
    biz_names = {row.id: row.name for row in db.execute(
        select(BizMaster.id, BizMaster.name).where(BizMaster.id.in_(biz_ids)).execution_options(include_deleted=True)
    )} if biz_ids else {}
    # 宛先は削除したものも名前で出す（削除したルールの宛先など）
    links = db.execute(
        select(rule_destination.c.rule_id, NotificationDestination.id, NotificationDestination.name)
        .join(NotificationDestination, NotificationDestination.id == rule_destination.c.destination_id)
        .where(rule_destination.c.rule_id.in_([r.id for r in rules]))
        .order_by(NotificationDestination.id)
        .execution_options(include_deleted=True)
    ) if rules else []
    destinations: dict[int, list[NamedRef]] = {}
    for rule_id, destination_id, name in links:
        destinations.setdefault(rule_id, []).append(NamedRef(id=destination_id, name=name))
    return [
        RuleOut(
            id=r.id, name=r.name, event_types=list(r.event_types), client_code=r.client_code, biz_master_id=r.biz_master_id,
            biz_name=biz_names.get(r.biz_master_id) if r.biz_master_id else None, template_code=r.template_code,
            destinations=destinations.get(r.id, []), dedupe_window_minutes=r.dedupe_window_minutes,
            quiet_start=r.quiet_start, quiet_end=r.quiet_end, quiet_mode=r.quiet_mode, enabled=r.enabled, is_deleted=r.is_deleted,
            created_at=r.created_at, created_by=staff.get(r.created_by_id) if r.created_by_id else None,
            updated_at=r.updated_at, updated_by=staff.get(r.updated_by_id) if r.updated_by_id else None,
        )
        for r in rules
    ]
