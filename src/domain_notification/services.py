"""
通知の設定（宛先・ルール）のサービス: 作成・編集・削除・元に戻す・テスト送信と、その権限。

- 呼び出し側のトランザクションの中で書く（コミットしない。API がコミットする）
- 作れないもの（知らない出来事の種類・文面、文面が扱えない種類、別の顧客の宛先 など）は 400 で断る
- 顧客の範囲: 担当顧客が限られた業務管理者は、自分の顧客の宛先・ルールだけを作り・変える。
  顧客なし（全顧客）の宛先・ルールを変えられるのは、全顧客を担当する業務管理者だけ（限られた職員は 403）。
  全顧客の宛先は、限られた業務管理者も自分のルールで使える（見えるが、変えられない）
- 変更は、変えた後に処理する出来事から効く（作った通知は、そのときのルールのまま）
- 削除は論理削除（元に戻せる）。ルールが使っている宛先は削除できない（使っているルールを details で返す）
- 宛先のアドレスを直すと、まだ再試行中の配信も新しいアドレスに送る（配信は送るときの宛先を読む）
- テスト送信: 出来事 DestinationTestRequested を記録するだけ。ワーカーが本物のアラームと同じ道で 1 分以内に送る
"""
import uuid
from datetime import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.error_codes import ErrorCode
from src.core.exceptions import BadRequestError, DuplicatedRecordError, ForbiddenError, NotFoundError, RecordReferencedException
from src.domain_biz.models import BizMaster
from src.domain_client.models import ClientMaster
from src.domain_events.formats import CURRENT_FORMATS, SYSTEM_EVENT_TYPES, DestinationTestV1, EventType
from src.domain_events.publish import publish
from src.domain_notification.models import (
    NotificationChannel, NotificationDestination, NotificationRule, QuietMode, rule_destination,
)
from src.domain_notification.templates import TEMPLATES
from src.domain_staff.models import StaffUser
from src.domain_staff.scope import ALL_CLIENTS, ClientScope
from src.core.clock import utc_now as _now

# 今作れる届け方（teams・webhook は場所だけ。足すときにチャネルのアダプターを 1 つ足す）
IMPLEMENTED_CHANNELS = (NotificationChannel.EMAIL,)


# ==========================================
# 顧客の範囲
# ==========================================
def require_editable(scope: ClientScope, client_code: str | None, not_found: str) -> None:
    """この職員が、その顧客（空 = 全顧客）の設定を変えられるか。範囲の外は 404、全顧客の設定は限られた職員には 403"""
    if not scope.limited:
        return
    if client_code is None:
        raise ForbiddenError("Settings for all clients can be changed only by admins who handle all clients.")
    scope.require(client_code, not_found)


def _visible(scope: ClientScope, client_code: str | None) -> bool:
    """見えるか（全顧客の設定は、限られた職員にも見える）"""
    return client_code is None or scope.allows(client_code)


def _require_client(db: Session, client_code: str | None) -> str | None:
    if client_code is None:
        return None
    code = client_code.strip().upper()
    if db.scalar(select(ClientMaster.id).where(ClientMaster.code == code)) is None:
        raise NotFoundError(f"Client '{code}' not found.")
    return code


# ==========================================
# 宛先
# ==========================================
def _check_destination_fields(
    db: Session, *, name: str, address: str, channel: NotificationChannel, exclude_id: int | None = None,
) -> tuple[str, str]:
    name, address = name.strip(), address.strip()
    if not name:
        raise BadRequestError("A destination needs a name.")
    if channel not in IMPLEMENTED_CHANNELS:
        raise BadRequestError(f"The channel '{channel.value}' is not available yet.")
    if channel == NotificationChannel.EMAIL and ("@" not in address or address.startswith("@") or address.endswith("@")):
        raise BadRequestError(f"'{address}' is not an email address.")
    stmt = select(NotificationDestination.id).where(NotificationDestination.name == name)
    if exclude_id is not None:
        stmt = stmt.where(NotificationDestination.id != exclude_id)
    if db.scalar(stmt) is not None:
        raise DuplicatedRecordError(f"A destination named '{name}' already exists.")
    return name, address


def get_destination(db: Session, destination_id: int, *, scope: ClientScope = ALL_CLIENTS, include_deleted: bool = False) -> NotificationDestination:
    destination = db.scalar(
        select(NotificationDestination).where(NotificationDestination.id == destination_id)
        .execution_options(include_deleted=include_deleted)
    )
    not_found = f"NotificationDestination(id={destination_id}) not found."
    if destination is None or not _visible(scope, destination.client_code):
        raise NotFoundError(not_found)
    return destination


def list_destinations(db: Session, *, scope: ClientScope = ALL_CLIENTS, include_deleted: bool = False) -> list[NotificationDestination]:
    stmt = select(NotificationDestination).order_by(NotificationDestination.name, NotificationDestination.id)
    if scope.limited:
        stmt = stmt.where(NotificationDestination.client_code.is_(None) | scope.clause(NotificationDestination.client_code))
    return list(db.scalars(stmt.execution_options(include_deleted=include_deleted)))


def create_destination(
    db: Session,
    *,
    name: str,
    address: str,
    channel: NotificationChannel = NotificationChannel.EMAIL,
    client_code: str | None = None,
    owner: str | None = None,
    secret_ref: str | None = None,
    actor_id: int | None = None,
    scope: ClientScope = ALL_CLIENTS,
) -> NotificationDestination:
    """宛先を作る（client_code が空なら、どの顧客の通知にも使える）"""
    code = _require_client(db, client_code)
    require_editable(scope, code, f"Client '{code}' not found.")
    name, address = _check_destination_fields(db, name=name, address=address, channel=channel)
    destination = NotificationDestination(
        name=name, channel=channel, address=address, client_code=code, owner=owner,
        secret_ref=secret_ref, created_by_id=actor_id, updated_by_id=actor_id,
    )
    db.add(destination)
    db.flush()
    return destination


def _rules_using(db: Session, destination_id: int, *, include_deleted: bool = False) -> list[NotificationRule]:
    stmt = (
        select(NotificationRule)
        .join(rule_destination, rule_destination.c.rule_id == NotificationRule.id)
        .where(rule_destination.c.destination_id == destination_id)
        .order_by(NotificationRule.id)
        .execution_options(include_deleted=include_deleted)
    )
    return list(db.scalars(stmt))


def update_destination(
    db: Session, destination_id: int, *, name: str, address: str, client_code: str | None, owner: str | None, enabled: bool,
    actor_id: int | None, scope: ClientScope = ALL_CLIENTS,
) -> NotificationDestination:
    """宛先を直す（アドレスを直すと、再試行中の配信も新しいアドレスへ）。顧客を変えるのは、使っているルールと合うときだけ"""
    destination = get_destination(db, destination_id, scope=scope)
    not_found = f"NotificationDestination(id={destination_id}) not found."
    require_editable(scope, destination.client_code, not_found)
    code = _require_client(db, client_code)
    require_editable(scope, code, not_found)
    name, address = _check_destination_fields(db, name=name, address=address, channel=destination.channel, exclude_id=destination.id)
    if code != destination.client_code and code is not None:
        clashing = [r for r in _rules_using(db, destination.id) if r.client_code != code]
        if clashing:
            raise BadRequestError(
                "The destination is used by rules of other clients or of all clients.",
                details={"rules": [{"id": r.id, "name": r.name} for r in clashing]},
            )
    destination.name, destination.address, destination.client_code = name, address, code
    destination.owner, destination.enabled, destination.updated_by_id = owner, enabled, actor_id
    db.flush()
    return destination


def delete_destination(db: Session, destination_id: int, *, actor_id: int | None, scope: ClientScope = ALL_CLIENTS) -> NotificationDestination:
    """宛先を論理削除する。削除していないルールが使っていれば断る（そのルールを details で返す）"""
    destination = get_destination(db, destination_id, scope=scope)
    require_editable(scope, destination.client_code, f"NotificationDestination(id={destination_id}) not found.")
    using = _rules_using(db, destination.id)
    if using:
        raise RecordReferencedException(
            f"The destination '{destination.name}' is used by {len(using)} rule(s).",
            error_code=ErrorCode.DELETE_FAILED_RECORD_IN_USE,
            details={"rules": [{"id": r.id, "name": r.name} for r in using]},
        )
    destination.soft_delete()
    destination.updated_by_id = actor_id
    db.flush()
    return destination


def restore_destination(db: Session, destination_id: int, *, actor_id: int | None, scope: ClientScope = ALL_CLIENTS) -> NotificationDestination:
    destination = get_destination(db, destination_id, scope=scope, include_deleted=True)
    require_editable(scope, destination.client_code, f"NotificationDestination(id={destination_id}) not found.")
    if not destination.is_deleted:
        return destination
    _check_destination_fields(db, name=destination.name, address=destination.address, channel=destination.channel, exclude_id=destination.id)
    destination.restore()
    destination.updated_by_id = actor_id
    db.flush()
    return destination


def request_destination_test(db: Session, destination_id: int, *, actor: StaffUser, scope: ClientScope = ALL_CLIENTS) -> int:
    """
    宛先のテスト送信を頼む（出来事を記録するだけ。ワーカーが 1 分以内に送る）。有効な宛先だけ。
    範囲のある業務管理者も、自分のルールで使える全顧客の宛先は試せる（変えるのではないため）
    """
    destination = get_destination(db, destination_id, scope=scope)
    if not destination.enabled:
        raise BadRequestError("Turn the destination on before sending a test.")
    now = _now()
    payload = DestinationTestV1(
        destination_id=destination.id, destination_name=destination.name, address=destination.address,
        requested_by_id=actor.id, requested_by_name=actor.name,
    )
    event_id = publish(
        db, event_type=EventType.DESTINATION_TEST, payload=payload, occurred_at=now, client_code=destination.client_code,
        subject_type="destination", subject_id=destination.id,
        dedupe_key=f"destination:{destination.id}:test:{uuid.uuid4()}",
    )
    assert event_id is not None
    return event_id


# ==========================================
# ルール
# ==========================================
def _validated_rule(
    db: Session, *, name: str, event_types: list[str], destination_ids: list[int], template_code: str,
    client_code: str | None, biz_master_id: int | None, dedupe_window_minutes: int | None,
    quiet_start: time | None, quiet_end: time | None, quiet_mode: QuietMode | None,
) -> dict:
    """
    ルールの項目を確かめ、保存する値を返す（作る・直すで同じ決まり）。
    確かめる順: 名前 → 出来事の種類と文面 → 重複の間隔・夜間 → 顧客・業務 → 宛先。最初に見つかった誤りだけを返す
    """
    name = _rule_name(name)
    types = _rule_event_types(event_types, template_code)
    _check_rule_timing(dedupe_window_minutes, quiet_start, quiet_end, quiet_mode)
    code = _rule_client_code(db, client_code, biz_master_id)
    destinations = _rule_destinations(db, destination_ids, code)
    return dict(
        name=name, event_types=types, client_code=code, biz_master_id=biz_master_id, template_code=template_code,
        dedupe_window_minutes=dedupe_window_minutes, quiet_start=quiet_start, quiet_end=quiet_end, quiet_mode=quiet_mode,
        destinations=destinations,
    )


def _rule_name(name: str) -> str:
    """前後の空白を外した名前（空なら断る）"""
    name = name.strip()
    if not name:
        raise BadRequestError("A rule needs a name.")
    return name


def _rule_event_types(event_types: list[str], template_code: str) -> list[str]:
    """重ねずに並べた出来事の種類。どれも選べる種類で、文面がすべてを書けること"""
    types = sorted(set(event_types))
    if not types:
        raise BadRequestError("A rule needs at least one event type.")
    unknown = [t for t in types if t not in CURRENT_FORMATS or t in SYSTEM_EVENT_TYPES]  # システムの出来事は選べない
    if unknown:
        raise BadRequestError(f"Unknown event types: {', '.join(unknown)}.")
    template = TEMPLATES.get(template_code)
    if template is None:
        raise BadRequestError(f"Unknown template '{template_code}'.")
    unsupported = [t for t in types if t not in template.event_types]
    if unsupported:
        raise BadRequestError(f"The template '{template_code}' cannot describe: {', '.join(unsupported)}.")
    return types


def _check_rule_timing(
    dedupe_window_minutes: int | None, quiet_start: time | None, quiet_end: time | None, quiet_mode: QuietMode | None,
) -> None:
    """重複の間隔は正の分。夜間は 開始・終了・扱い の 3 つがそろうか、どれも無いか。開始と終了は同じにしない"""
    if dedupe_window_minutes is not None and dedupe_window_minutes <= 0:
        raise BadRequestError("The dedupe window must be a positive number of minutes.")
    quiet = (quiet_start, quiet_end, quiet_mode)
    if any(v is None for v in quiet) and any(v is not None for v in quiet):
        raise BadRequestError("Quiet hours need a start, an end and a mode.")
    if quiet_start is not None and quiet_start == quiet_end:
        raise BadRequestError("Quiet hours must not start and end at the same time.")


def _rule_client_code(db: Session, client_code: str | None, biz_master_id: int | None) -> str | None:
    """
    ルールの顧客（無ければ全顧客）。業務で絞るなら、その業務は同じ顧客のもの。
    顧客を決めずに業務で絞るルールは、その業務の顧客のものになる（範囲の確かめのため）
    """
    code = _require_client(db, client_code)
    if biz_master_id is None:
        return code
    biz = db.get(BizMaster, biz_master_id)
    if biz is None:
        raise NotFoundError(f"BizMaster(id={biz_master_id}) not found.")
    if code is not None and biz.client_code != code:
        raise BadRequestError("The business belongs to another client.")
    return code if code is not None else biz.client_code


def _rule_destinations(db: Session, destination_ids: list[int], code: str | None) -> list[NotificationDestination]:
    """
    id の順の宛先。1 つ以上、どれもあって、ほかの顧客の宛先ではないこと。
    全顧客のルールは、顧客ごとの宛先も使える（その顧客の出来事のときだけ届く。振り分けが宛先の顧客を見る）
    """
    if not destination_ids:
        raise BadRequestError("A rule needs at least one destination.")
    destinations = list(db.scalars(select(NotificationDestination).where(NotificationDestination.id.in_(destination_ids))))
    missing = sorted(set(destination_ids) - {d.id for d in destinations})
    if missing:
        raise NotFoundError(f"Destinations not found: {missing}.")
    if code is not None:
        foreign = [d.name for d in destinations if d.client_code not in (None, code)]
        if foreign:
            raise BadRequestError(f"Destinations of another client cannot be used: {', '.join(foreign)}.")
    return sorted(destinations, key=lambda d: d.id)


def get_rule(db: Session, rule_id: int, *, scope: ClientScope = ALL_CLIENTS, include_deleted: bool = False) -> NotificationRule:
    rule = db.scalar(select(NotificationRule).where(NotificationRule.id == rule_id).execution_options(include_deleted=include_deleted))
    if rule is None or not _visible(scope, rule.client_code):
        raise NotFoundError(f"NotificationRule(id={rule_id}) not found.")
    return rule


def list_rules(db: Session, *, scope: ClientScope = ALL_CLIENTS, include_deleted: bool = False) -> list[NotificationRule]:
    stmt = select(NotificationRule).order_by(NotificationRule.name, NotificationRule.id)
    if scope.limited:
        stmt = stmt.where(NotificationRule.client_code.is_(None) | scope.clause(NotificationRule.client_code))
    return list(db.scalars(stmt.execution_options(include_deleted=include_deleted)))


def create_rule(
    db: Session,
    *,
    name: str,
    event_types: list[str],
    destination_ids: list[int],
    template_code: str,
    client_code: str | None = None,
    biz_master_id: int | None = None,
    dedupe_window_minutes: int | None = None,
    quiet_start: time | None = None,
    quiet_end: time | None = None,
    quiet_mode: QuietMode | None = None,
    enabled: bool = True,
    actor_id: int | None = None,
    scope: ClientScope = ALL_CLIENTS,
) -> NotificationRule:
    """ルールを作る。出来事の種類・文面・宛先が使えるものか、ここで確かめる"""
    values = _validated_rule(
        db, name=name, event_types=event_types, destination_ids=destination_ids, template_code=template_code,
        client_code=client_code, biz_master_id=biz_master_id, dedupe_window_minutes=dedupe_window_minutes,
        quiet_start=quiet_start, quiet_end=quiet_end, quiet_mode=quiet_mode,
    )
    require_editable(scope, values["client_code"], f"Client '{values['client_code']}' not found.")
    rule = NotificationRule(**values, enabled=enabled, created_by_id=actor_id, updated_by_id=actor_id)
    db.add(rule)
    db.flush()
    return rule


def update_rule(
    db: Session, rule_id: int, *, name: str, event_types: list[str], destination_ids: list[int], template_code: str,
    client_code: str | None, biz_master_id: int | None, dedupe_window_minutes: int | None,
    quiet_start: time | None, quiet_end: time | None, quiet_mode: QuietMode | None, enabled: bool,
    actor_id: int | None, scope: ClientScope = ALL_CLIENTS,
) -> NotificationRule:
    """ルールを直す（すべての項目を送る）。変えた後に処理する出来事から効く"""
    rule = get_rule(db, rule_id, scope=scope)
    not_found = f"NotificationRule(id={rule_id}) not found."
    require_editable(scope, rule.client_code, not_found)
    values = _validated_rule(
        db, name=name, event_types=event_types, destination_ids=destination_ids, template_code=template_code,
        client_code=client_code, biz_master_id=biz_master_id, dedupe_window_minutes=dedupe_window_minutes,
        quiet_start=quiet_start, quiet_end=quiet_end, quiet_mode=quiet_mode,
    )
    require_editable(scope, values["client_code"], not_found)
    for key, value in values.items():
        setattr(rule, key, value)
    rule.enabled, rule.updated_by_id = enabled, actor_id
    db.flush()
    return rule


def delete_rule(db: Session, rule_id: int, *, actor_id: int | None, scope: ClientScope = ALL_CLIENTS) -> NotificationRule:
    """ルールを論理削除する（作った通知と、まだ送っていない配信はそのまま）"""
    rule = get_rule(db, rule_id, scope=scope)
    require_editable(scope, rule.client_code, f"NotificationRule(id={rule_id}) not found.")
    rule.soft_delete()
    rule.updated_by_id = actor_id
    db.flush()
    return rule


def restore_rule(db: Session, rule_id: int, *, actor_id: int | None, scope: ClientScope = ALL_CLIENTS) -> NotificationRule:
    """削除したルールを元に戻す。使っていた宛先が削除されていれば断る（その宛先を details で返す）"""
    rule = get_rule(db, rule_id, scope=scope, include_deleted=True)
    require_editable(scope, rule.client_code, f"NotificationRule(id={rule_id}) not found.")
    if not rule.is_deleted:
        return rule
    ids = [row.destination_id for row in db.execute(select(rule_destination.c.destination_id).where(rule_destination.c.rule_id == rule.id))]
    gone = list(db.scalars(
        select(NotificationDestination).where(NotificationDestination.id.in_(ids), NotificationDestination.is_deleted.is_(True))
        .execution_options(include_deleted=True)
    ))
    if gone:
        raise BadRequestError(
            "Some destinations of this rule have been deleted; restore them or edit the rule first.",
            details={"destinations": [{"id": d.id, "name": d.name} for d in gone]},
        )
    rule.restore()
    rule.updated_by_id = actor_id
    db.flush()
    return rule
