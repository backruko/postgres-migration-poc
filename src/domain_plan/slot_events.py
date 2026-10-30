"""
発生枠の変更の通知（plan → 登録された受け手）。

plan はインスタンスを作る・無効化するたびに、その発生枠を記録します（リポジトリの add_* / void_many から呼ばれる）。
トランザクションをコミットする直前に、記録した発生枠を発生枠ごとにまとめて、登録された受け手に渡します。
受け手は同じトランザクションの中で処理するため、受け手が例外を投げると plan の変更ごと取り消されます。
plan は受け手が何をするか（オーダーのこと）を知りません。受け手は起動時に register_slot_listener で登録します。
"""
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Literal

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from src.domain_plan.models import InstanceOrigin, PlanInstance, PlanInstanceVoid, VoidReason

SlotKind = Literal["date", "sheet_event", "one_off"]

# Session.info に記録するキー（トランザクションごと）
_CHANGES_KEY = "plan_slot_changes"
_PERSON_KEY = "plan_slot_person_change"


@dataclass(frozen=True)
class SlotKey:
    """
    発生枠の識別子（_slot() と同じ規則 + 臨時の予定）。
    - date: ルール上のローカル日付（RRULE / 指定日）
    - sheet_event: シート日付の行（シートの日付が動いても同じ枠）
    - one_off: 臨時の予定の最初のインスタンス（日時を変えても同じ枠）
    occurrence_date は枠の現在の日付（シート由来・臨時では識別子に含めない）。
    """
    template_id: int
    kind: SlotKind
    ref: date | int
    occurrence_date: date = field(compare=False)

    @property
    def sheet_event_id(self) -> int | None:
        return self.ref if self.kind == "sheet_event" else None  # type: ignore[return-value]

    @property
    def one_off_instance_id(self) -> int | None:
        return self.ref if self.kind == "one_off" else None  # type: ignore[return-value]


@dataclass
class SlotChange:
    """1つの発生枠への、1トランザクション分の変更"""
    slot: SlotKey
    void_reasons: list[VoidReason]
    by_person: bool                 # 人の手動操作（変更・取消・ルールに戻す・臨時追加・取消の復元）による変更か
    actor_staff_id: int | None      # 操作した職員（by_person でも、職員が分からない呼び出しでは None）
    note: str | None                # 人が入力した理由（取消・変更のメモ）


SlotListener = Callable[[Session, list[SlotChange]], None]
_listeners: list[SlotListener] = []


def register_slot_listener(listener: SlotListener) -> None:
    """発生枠の変更の受け手を登録します（同じ受け手を2回登録しても1回だけ呼ばれる）。"""
    if listener not in _listeners:
        _listeners.append(listener)


def _chain_root(db: Session, instance: PlanInstance) -> PlanInstance:
    root = instance
    while root.overrides_instance_id is not None:
        parent = db.get(PlanInstance, root.overrides_instance_id)
        assert parent is not None
        root = parent
    return root


def slot_key_of(db: Session, instance: PlanInstance) -> SlotKey:
    """インスタンスが属する発生枠"""
    root = _chain_root(db, instance) if instance.origin != InstanceOrigin.GENERATED else instance
    if root.origin == InstanceOrigin.ONE_OFF:
        return SlotKey(instance.template_id, "one_off", root.id, instance.occurrence_date)
    if instance.source_sheet_event_id is not None:
        return SlotKey(instance.template_id, "sheet_event", instance.source_sheet_event_id, instance.occurrence_date)
    return SlotKey(instance.template_id, "date", instance.occurrence_date, instance.occurrence_date)


def live_instance_for_slot(db: Session, slot: SlotKey) -> PlanInstance | None:
    """発生枠の現在の有効なインスタンス（無ければ None = 枠が消えた・取り消された）"""
    if slot.kind == "sheet_event":
        condition = PlanInstance.source_sheet_event_id == slot.ref
    else:
        # ルール上の日付・臨時の予定はどちらも occurrence_date を引き継ぐため、同じ日付の候補から枠で絞る
        condition = (PlanInstance.occurrence_date == slot.occurrence_date) & PlanInstance.source_sheet_event_id.is_(None)
    stmt = (
        select(PlanInstance)
        .outerjoin(PlanInstanceVoid, PlanInstanceVoid.instance_id == PlanInstance.id)
        .where(PlanInstance.template_id == slot.template_id, PlanInstanceVoid.instance_id.is_(None), condition)
    )
    return next((i for i in db.scalars(stmt) if slot_key_of(db, i) == slot), None)


# ==========================================
# 記録（リポジトリから呼ばれる）
# ==========================================
def _pending(db: Session) -> dict[SlotKey, list[VoidReason]]:
    return db.info.setdefault(_CHANGES_KEY, {})


def record_created(db: Session, instances: list[PlanInstance]) -> None:
    pending = _pending(db)
    for instance in instances:
        pending.setdefault(slot_key_of(db, instance), [])


def record_voided(db: Session, instance_ids: list[int], reason: VoidReason) -> None:
    if not instance_ids:
        return
    pending = _pending(db)
    for instance in db.scalars(select(PlanInstance).where(PlanInstance.id.in_(instance_ids))):
        pending.setdefault(slot_key_of(db, instance), []).append(reason)


def mark_person_change(db: Session, actor_staff_id: int | None, note: str | None = None) -> None:
    """このトランザクションの変更は人の手動操作によるもの（受け手が「人の変更」として扱う）"""
    db.info[_PERSON_KEY] = (actor_staff_id, note)


# ==========================================
# 通知（コミットの直前に、同じトランザクションで受け手を呼ぶ）
# ==========================================
@event.listens_for(Session, "before_commit")
def _publish(session: Session) -> None:
    pending: dict[SlotKey, list[VoidReason]] | None = session.info.pop(_CHANGES_KEY, None)
    person = session.info.pop(_PERSON_KEY, None)
    if not pending or not _listeners:
        return
    actor, note = person if person else (None, None)
    changes = [
        SlotChange(slot=slot, void_reasons=reasons, by_person=person is not None, actor_staff_id=actor, note=note)
        for slot, reasons in pending.items()
    ]
    for listener in _listeners:
        listener(session, changes)


@event.listens_for(Session, "after_rollback")
def _discard(session: Session) -> None:
    session.info.pop(_CHANGES_KEY, None)
    session.info.pop(_PERSON_KEY, None)
