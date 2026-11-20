"""
通知の振り分け（ディスパッチャー。通知の仕組み）。ワーカーが毎分、締切の監視のすぐ後に呼びます。

未処理の出来事（domain_event）を読み、当てはまるルールごとに通知（notification）を 1 件作り、宛先ごとの配信
（notification_delivery）を「待ち」で作る。送るのは配信の処理。出来事には処理済みの印を付ける。

- 当てはまる: 種類がルールの種類に入っている・ルールの顧客が空か同じ・ルールの業務が空か出来事の写しの業務と同じ。
  有効で削除されていないルールだけ。ルールは処理するときにあるものだけ（新しいルールは古い出来事に反応しない）
- 同じルールで同じ事実（dedupe_key）は 1 回だけ（データベースの一意キーでも守る）
- 知らせない（suppressed。理由を残す）:
  - dedupe_window: ルールの重複の期間内に、同じルール・同じオーダー・同じ種類の通知をもう出した（まもなくは遅れを消さない）
  - quiet_hours: 知らせない時間帯で、扱いが「知らせない」
  - no_destination: 使える宛先がない（有効・削除されていない・顧客が空か同じ）
  - template_missing: ルールの文面がコードにない・その種類を扱えない（設定の誤りを、出来事を止めずに見えるようにする）
- 知らせない時間帯で扱いが「後で知らせる」なら、配信の次の時刻を時間帯の終わりにする（送るときも文面はその時刻の事実）
- 1 回に最大 200 件。FOR UPDATE SKIP LOCKED で、ワーカーが 2 つ動いても同じ出来事を取らない。
  既にある通知・最後に知らせた日時は回の初めにまとめて読む（出来事ごとに問い合わせない）
- 宛先のテスト送信（DestinationTestRequested）はルールを通さず、頼まれた宛先だけに送る通知を作る
- 1 件の出来事の処理が失敗したら、その出来事の分だけ取り消し（セーブポイント）、試みた回数と理由を残して次の回にもう一度。
  5 回失敗したら、もう取らない（出来事の画面で「失敗 5 回」と見える）
- コミットは呼び出し側（ワーカー）
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, select, tuple_
from sqlalchemy.orm import Session, selectinload

from src.domain_events.formats import SYSTEM_EVENT_TYPES, DestinationTestV1
from src.domain_events.models import DomainEvent
from src.domain_notification.models import (
    DeliveryStatus, Notification, NotificationDelivery, NotificationDestination, NotificationRule, NotificationStatus,
    QuietMode,
)
from src.domain_notification.templates import DESTINATION_TEST as TEST_TEMPLATE, SYSTEM_TEMPLATES, TEMPLATES
from src.core.clock import LOCAL_TIMEZONE, utc_now

BATCH_SIZE = 200
MAX_EVENT_ATTEMPTS = 5

# 知らせなかった理由（画面で日本語にする。API・記録は英語のまま）
REASON_DEDUPE_WINDOW = "dedupe_window"
REASON_QUIET_HOURS = "quiet_hours"
REASON_NO_DESTINATION = "no_destination"
REASON_TEMPLATE_MISSING = "template_missing"


@dataclass
class DispatchResult:
    events: int = 0                                     # 処理した出来事
    notifications: int = 0                              # 配信を待つ通知を作った数
    held: int = 0                                       # そのうち、知らせない時間帯の終わりまで待つもの
    suppressed: dict[str, int] = field(default_factory=dict)  # 知らせなかった通知の数（理由ごと）
    failed_events: list[int] = field(default_factory=list)    # 処理に失敗した出来事（次の回にもう一度）


def quiet_until(rule: NotificationRule, now: datetime) -> datetime | None:
    """いまがルールの知らせない時間帯なら、その終わりの日時。時間帯の外なら None（日をまたぐ時間帯も扱う）"""
    if rule.quiet_start is None or rule.quiet_end is None:
        return None
    local = now.astimezone(LOCAL_TIMEZONE)
    start, end, t = rule.quiet_start, rule.quiet_end, local.time()
    if start < end:
        inside, end_day = start <= t < end, local.date()
    else:  # 例: 22:00〜07:00
        inside = t >= start or t < end
        end_day = local.date() + timedelta(days=1) if t >= start else local.date()
    return datetime.combine(end_day, end, tzinfo=LOCAL_TIMEZONE) if inside else None


def matches(rule: NotificationRule, event: DomainEvent) -> bool:
    """ルールが出来事に当てはまるか（業務は出来事の写しの biz_master_id で見る。写しに業務がなければ、業務のルールは当てはまらない）"""
    if event.event_type not in rule.event_types:
        return False
    if rule.client_code is not None and rule.client_code != event.client_code:
        return False
    if rule.biz_master_id is not None and rule.biz_master_id != event.payload.get("biz_master_id"):  # noqa: SIM103  (条件ごとに 1 つ)
        return False
    return True


# 重複の期間で見る「同じもの」: ルール・出来事の種類・対象の種類・対象の ID
_WindowKey = tuple[int, str, str | None, int | None]


@dataclass
class _PassState:
    """
    1 回の振り分けで、出来事ごとに問い合わせないための先読み（計測: 出来事ごとの問い合わせで 200 件に約 1.8 秒かかり、
    通知が増えるほど遅くなった）。回の初めに 2 回だけ読み、この回で作った通知はここに足していく
    """
    existing: set[tuple[int, str]]          # もうある通知の（ルール, dedupe_key）
    last_sent: dict[_WindowKey, datetime]  # 知らせた（抑止でない）通知の、最後の日時


def _window_key(rule: NotificationRule, event: DomainEvent) -> _WindowKey:
    return (rule.id, event.event_type, event.subject_type, event.subject_id)


def _prefetch(db: Session, events: list[DomainEvent], rules: list[NotificationRule], now: datetime) -> _PassState:
    rule_ids = [r.id for r in rules]
    keys = list({e.dedupe_key for e in events})
    # 一意キー (rule_id, dedupe_key) のインデックスで引く（dedupe_key だけでは、記録全体を読むことになる）
    existing: set[tuple[int, str]] = {
        (rule_id, key) for rule_id, key in db.execute(
            select(Notification.rule_id, Notification.dedupe_key)
            .where(Notification.rule_id.in_(rule_ids), Notification.dedupe_key.in_(keys))
        ) if rule_id is not None  # IN で空は来ない（型のため）
    } if rule_ids else set()
    windows = [r.dedupe_window_minutes for r in rules if r.dedupe_window_minutes]
    subjects = list({(e.subject_type, e.subject_id) for e in events if e.subject_id is not None})
    last_sent: dict[_WindowKey, datetime] = {}
    if windows and subjects:
        # 対象（オーダー）の出来事から引く（ix_domain_event_subject → notification の event_id）
        rows = db.execute(
            select(Notification.rule_id, DomainEvent.event_type, DomainEvent.subject_type, DomainEvent.subject_id,
                   func.max(Notification.created_at))
            .join(DomainEvent, DomainEvent.id == Notification.event_id)
            .where(
                tuple_(DomainEvent.subject_type, DomainEvent.subject_id).in_(subjects),
                Notification.rule_id.in_(rule_ids),
                Notification.status != NotificationStatus.SUPPRESSED,
                Notification.created_at >= now - timedelta(minutes=max(windows)),
            )
            .group_by(Notification.rule_id, DomainEvent.event_type, DomainEvent.subject_type, DomainEvent.subject_id)
        ).all()
        last_sent = {(rule_id, et, st, sid): at for rule_id, et, st, sid, at in rows if rule_id is not None}
    return _PassState(existing=existing, last_sent=last_sent)


def dispatch_events(db: Session, *, now: datetime | None = None, batch_size: int = BATCH_SIZE) -> DispatchResult:
    """未処理の出来事を、当てはまるルールの通知にする（何度呼んでも同じ出来事から 2 通目は出ない。コミットしない）"""
    now = now or utc_now()
    result = DispatchResult()
    events = _claim_events(db, batch_size)
    if not events:
        return result
    rules = _active_rules(db)
    state = _prefetch(db, events, rules, now)
    for event in events:
        result.events += 1
        savepoint = db.begin_nested()
        try:
            made = _notifications_for(db, event, rules, now, state)
            savepoint.commit()
        except Exception as error:  # この出来事の分だけ取り消し、次の回にもう一度（先読みにも足さない）
            savepoint.rollback()
            event.attempts += 1
            event.last_error = f"{type(error).__name__}: {error}"[:2000]
            result.failed_events.append(event.id)
            continue
        event.processed_at = now
        _remember_and_count(event, made, now, state, result)
    db.flush()
    return result


# 出来事 1 件から作った通知 1 件: (通知, ルール（テスト送信は None）, 知らせない時間帯の終わりまで待つなら、その日時)
_Made = tuple[Notification, NotificationRule | None, datetime | None]


def _claim_events(db: Session, batch_size: int) -> list[DomainEvent]:
    """未処理で、まだあきらめていない出来事を古い順に取る（ほかのワーカーが取っている行は飛ばす）"""
    return list(db.scalars(
        select(DomainEvent)
        .where(DomainEvent.processed_at.is_(None), DomainEvent.attempts < MAX_EVENT_ATTEMPTS)
        .order_by(DomainEvent.id)
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    ))


def _active_rules(db: Session) -> list[NotificationRule]:
    """有効で削除していないルール（宛先も一緒に読む）"""
    return list(db.scalars(
        select(NotificationRule)
        .where(NotificationRule.enabled.is_(True), NotificationRule.is_deleted.is_(False))
        .options(selectinload(NotificationRule.destinations))
        .order_by(NotificationRule.id)
    ))


def _notifications_for(
    db: Session, event: DomainEvent, rules: list[NotificationRule], now: datetime, state: _PassState,
) -> list[_Made]:
    """出来事 1 件の通知を作る: テスト送信はルールを通さず、ほかは当てはまるルールごとに 1 件（もうあるものは作らない）"""
    if event.event_type in SYSTEM_EVENT_TYPES:  # ルールを通さない（宛先のテスト送信）
        return [m for m in [_notify_test(db, event, now)] if m is not None]
    made: list[_Made] = []
    for rule in rules:
        if matches(rule, event):
            made += [m for m in [_notify(db, rule, event, now, state)] if m is not None]
    return made


def _remember_and_count(event: DomainEvent, made: list[_Made], now: datetime, state: _PassState, result: DispatchResult) -> None:
    """
    作った通知をこの回の記憶に足し（同じ事実の 2 通目・重複の期間を、この回の後の出来事でも見る）、結果を数える。
    重複の期間に数えるのは、知らせた通知だけ
    """
    for notification, rule, hold_until in made:
        reason = notification.suppressed_reason
        if rule is not None:
            state.existing.add((rule.id, event.dedupe_key))
        if reason:
            result.suppressed[reason] = result.suppressed.get(reason, 0) + 1
        else:
            if rule is not None:
                state.last_sent[_window_key(rule, event)] = now
            result.notifications += 1
            result.held += hold_until is not None


def _notify(
    db: Session, rule: NotificationRule, event: DomainEvent, now: datetime, state: _PassState,
) -> tuple[Notification, NotificationRule, datetime | None] | None:
    """1 件の通知を作る（もうあれば None）。書くのは出来事ごとのセーブポイントのコミットのとき"""
    if (rule.id, event.dedupe_key) in state.existing:
        return None
    reason: str | None = None
    hold_until: datetime | None = None
    subject, body, link = f"{event.event_type} #{event.subject_id}", "", None

    template = TEMPLATES.get(rule.template_code)
    if template is None or event.event_type not in template.event_types:
        reason = REASON_TEMPLATE_MISSING
    else:
        rendered = template.render(event, rule.name)  # 中身の形が合わなければ例外 → この出来事は失敗として次の回に
        subject, body, link = rendered.subject, rendered.body, rendered.link
        last = state.last_sent.get(_window_key(rule, event)) if event.subject_id is not None else None
        if rule.dedupe_window_minutes and last is not None and last >= now - timedelta(minutes=rule.dedupe_window_minutes):
            reason = REASON_DEDUPE_WINDOW
        else:
            hold_until = quiet_until(rule, now)
            if hold_until is not None and rule.quiet_mode == QuietMode.DROP:
                reason, hold_until = REASON_QUIET_HOURS, None

    destinations = [d for d in rule.destinations if _usable(d, event)]
    if reason is None and not destinations:
        reason = REASON_NO_DESTINATION

    notification = Notification(
        event_id=event.id, rule_id=rule.id, dedupe_key=event.dedupe_key, client_code=event.client_code,
        status=NotificationStatus.SUPPRESSED if reason else NotificationStatus.PENDING, suppressed_reason=reason,
        subject=subject, body=body, link=link, created_at=now, completed_at=now if reason else None,
    )
    if reason is None:
        notification.deliveries = [
            NotificationDelivery(destination_id=d.id, status=DeliveryStatus.PENDING, next_attempt_at=hold_until or now)
            for d in destinations
        ]
    db.add(notification)
    return notification, rule, hold_until


def _notify_test(db: Session, event: DomainEvent, now: datetime) -> tuple[Notification, None, None] | None:
    """
    宛先のテスト送信: ルールなしの通知を、頼まれた宛先 1 つに作る。宛先が削除・無効になっていれば、送らずに
    「使える宛先がない」で残す。送り先のアドレスは送るときの宛先のもの（配信が読む）
    """
    if db.scalar(select(Notification.id).where(Notification.event_id == event.id)) is not None:
        return None
    test = DestinationTestV1.model_validate(event.payload)
    destination = db.scalar(
        select(NotificationDestination).where(NotificationDestination.id == test.destination_id)
        .execution_options(include_deleted=True)
    )
    rendered = SYSTEM_TEMPLATES[TEST_TEMPLATE].render(event, "")
    usable = destination is not None and destination.enabled and not destination.is_deleted
    notification = Notification(
        event_id=event.id, rule_id=None, dedupe_key=event.dedupe_key, client_code=event.client_code,
        status=NotificationStatus.PENDING if usable else NotificationStatus.SUPPRESSED,
        suppressed_reason=None if usable else REASON_NO_DESTINATION,
        subject=rendered.subject, body=rendered.body, link=None, created_at=now, completed_at=None if usable else now,
    )
    if usable:
        notification.deliveries = [NotificationDelivery(destination_id=test.destination_id, status=DeliveryStatus.PENDING, next_attempt_at=now)]
    db.add(notification)
    return notification, None, None


def _usable(destination: NotificationDestination, event: DomainEvent) -> bool:
    return destination.enabled and not destination.is_deleted and destination.client_code in (None, event.client_code)
