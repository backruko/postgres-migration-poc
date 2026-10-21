from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from src.infrastructure.repositories import (
    biz_master_repo,
    client_repo,
    plan_cache_repo,
    plan_instance_repo,
    plan_instance_void_repo,
    plan_slot_release_repo,
    plan_template_repo,
    reference_sheet_repo,
)
from src.domain_biz.models import BizMaster
from src.domain_plan import engine
from src.domain_plan.models import (
    InstanceOrigin,
    PlanExplicitDate,
    PlanInstance,
    PlanInstanceVoid,
    PlanOccurrenceCache,
    PlanTemplate,
    ReferenceSheet,
    ReferenceSheetEvent,
    ReferenceSheetMarker,
    TemplateType,
    VoidReason,
)
from src.domain_plan.schemas import (
    ExplicitDatesTemplateCreate,
    PlanInstanceCancel,
    PlanInstanceOverride,
    PlanInstanceResponse,
    PlanInstanceRevert,
    PlanOneOffCreate,
    PlanRevertResponse,
    PlanTemplateCreate,
    PlanTemplateUpdate,
    ReferenceSheetCreate,
    ReferenceSheetEventResponse,
    ReferenceSheetEventsCreate,
    ReferenceSheetEventUpdate,
    ReferenceSheetMarkerCreate,
    ReferenceSheetTemplateCreate,
    ui_metadata_to_dict,
)
from src.core.error_codes import ErrorCode
from src.core.exceptions import BadRequestError, DuplicatedRecordError, NotFoundError

# ローリングホライズン: テンプレート作成時に「今から何日先まで」インスタンスを生成するか
MATERIALIZE_HORIZON = timedelta(days=90)
# 範囲検索の上限（巨大な範囲で大量生成されるのを防ぐ）
MAX_QUERY_SPAN = timedelta(days=366)
MAX_LOOKAHEAD = timedelta(days=730)
# 「無期限」の代わりに使う上限
FAR_FUTURE = datetime.max.replace(tzinfo=timezone.utc)

RRULE_FIELDS = {"anchor_date", "rrule"}
SHEET_FIELDS = {"reference_sheet_id", "reference_event_marker", "offset_minutes"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ==========================================
# 生成エンジン（内部処理）
# ==========================================
def _load_holidays(db: Session, template: PlanTemplate) -> list[date]:
    """休日カレンダー（銀行休業日など）のフック。カレンダーテーブル導入時にここで読み込みます。"""
    return []


def _expand(db: Session, template: PlanTemplate, lower: datetime, upper: datetime) -> list[engine.Occurrence]:
    """テンプレートの種類に応じて入力を集め、エンジンで [lower, upper) の発生日時を算出します。"""
    holidays = _load_holidays(db, template)

    if template.template_type == TemplateType.RRULE:
        # CHECK制約により RRULE の場合は必ず値がある
        assert template.rrule is not None and template.anchor_date is not None
        return engine.expand_rrule(
            rrule=template.rrule,
            anchor_date=template.anchor_date,
            start_time=template.start_time,
            tz_name=template.timezone,
            duration_minutes=template.duration_minutes,
            window_start=lower,
            window_end=upper,
            holidays=holidays,
        )

    if template.template_type == TemplateType.EXPLICIT_DATES:
        return engine.expand_explicit_dates(
            dates=[d.local_date for d in template.explicit_dates],
            start_time=template.start_time,
            tz_name=template.timezone,
            duration_minutes=template.duration_minutes,
            window_start=lower,
            window_end=upper,
            holidays=holidays,
        )

    # REFERENCE_SHEET_BASED: (シート, マーカー) の具体的な日付を取得して算出
    assert template.reference_sheet_id is not None and template.reference_event_marker is not None
    assert template.offset_minutes is not None
    events = reference_sheet_repo.get_event_dates(
        db, sheet_id=template.reference_sheet_id, marker=template.reference_event_marker
    )
    return engine.expand_sheet_events(
        events=events,
        start_time=template.start_time,
        offset_minutes=template.offset_minutes,
        tz_name=template.timezone,
        duration_minutes=template.duration_minutes,
        window_start=lower,
        window_end=upper,
        holidays=holidays,
    )


def _slot(occurrence_date: date, source_sheet_event_id: int | None) -> tuple[str, date | int]:
    """
    発生枠の識別キー。シート由来はシート日付の行（日付が動いても同じ枠）、それ以外はルール上のローカル日付。
    """
    if source_sheet_event_id is not None:
        return ("sheet_event", source_sheet_event_id)
    return ("date", occurrence_date)


def _manual_slots(db: Session, template: PlanTemplate) -> set[tuple[str, date | int]]:
    """
    手動の判断が現在も有効な発生枠を返します。
    - 取消（MANUALで無効化され、置き換えなし）→ 有効
    - オーバーライド → 置き換えの連鎖の末端が「有効」または「手動取消」なら有効
      （末端がテンプレート削除などで無効化された場合は手動の判断も失効し、ルールから再生成される）
    - 解除（plan_slot_release）された起点 → 無効（ルールの管理下に戻っている）
    """
    history = plan_instance_repo.get_manual_history(db, template_id=template.id)
    released = plan_slot_release_repo.get_released_root_ids(db, template_id=template.id)
    void_of = {instance.id: void for instance, void in history}
    child_of = {instance.overrides_instance_id: instance for instance, _ in history if instance.overrides_instance_id}

    slots: set[tuple[str, date | int]] = set()
    for instance, void in history:
        # 起点 = ルールから生成され、手動で無効化され、まだ解除されていないインスタンス
        if (
            instance.origin != InstanceOrigin.GENERATED
            or void is None
            or void.reason != VoidReason.MANUAL
            or instance.id in released
        ):
            continue
        terminal = instance
        while terminal.id in child_of:
            terminal = child_of[terminal.id]
        terminal_void = void_of.get(terminal.id)
        if terminal_void is None or terminal_void.reason == VoidReason.MANUAL:
            slots.add(_slot(instance.occurrence_date, instance.source_sheet_event_id))
    return slots


def _reconcile(db: Session, template: PlanTemplate, lower: datetime, upper: datetime, reason: VoidReason) -> None:
    """
    [lower, upper) の範囲で「あるべきインスタンス」と「現在有効なインスタンス」を突き合わせます。
    - 変わっていないもの: そのまま残す（不変）
    - 不要になったもの: 無効化(void)し、キャッシュから除外
    - 新しく必要なもの: 現在のリビジョンで生成し、キャッシュに追加
    """
    if upper <= lower:
        return

    # 手動で変更・取消された発生枠はルールから再生成しない（手動オーバーライド自体も照合対象外）
    manual_slots = _manual_slots(db, template)
    desired = {
        o.key: o
        for o in _expand(db, template, lower, upper)
        if _slot(o.occurrence_date, o.source_sheet_event_id) not in manual_slots
    }
    existing = {
        (i.scheduled_start, i.scheduled_end, i.source_sheet_event_id): i
        for i in plan_instance_repo.get_live_in_window(
            db, template_id=template.id, lower=lower, upper=upper, generated_only=True
        )
    }

    to_void = [i.id for key, i in existing.items() if key not in desired]
    to_add = [o for key, o in desired.items() if key not in existing]

    if to_void:
        plan_instance_void_repo.void_many(db, instance_ids=to_void, reason=reason)
        plan_cache_repo.purge(db, instance_ids=to_void)
    if to_add:
        new_instances = plan_instance_repo.add_many(db, template=template, occurrences=to_add)
        plan_cache_repo.add_for_instances(db, template=template, instances=new_instances)


def _ensure_materialized(db: Session, template: PlanTemplate, until: datetime) -> None:
    """materialized_until から until までの未生成区間を生成します（生成済みなら何もしません）。"""
    lower = template.materialized_until or _now()
    if until <= lower:
        return
    _reconcile(db, template, lower, until, VoidReason.TEMPLATE_REVISED)
    template.materialized_until = until


def _regenerate(db: Session, template: PlanTemplate, reason: VoidReason) -> None:
    """
    生成に影響する変更の後に呼びます。リビジョンを上げ、未来（今以降）の生成済み区間を再照合します。
    過去のインスタンスには一切触れません。
    """
    template.revision += 1
    if template.materialized_until is not None:
        _reconcile(db, template, _now(), template.materialized_until, reason)


def _commit(db: Session, message: str) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise DuplicatedRecordError(message)


# ==========================================
# ReferenceSheet Service
# ==========================================
def create_sheet(db: Session, sheet_in: ReferenceSheetCreate) -> ReferenceSheet:
    if not client_repo.get_by_code(db, code=sheet_in.client_code):
        raise NotFoundError(f"Client with code '{sheet_in.client_code}' does not exist.")

    sheet = ReferenceSheet(**sheet_in.model_dump())
    db.add(sheet)
    _commit(db, f"Sheet '{sheet_in.name}' already exists for client '{sheet_in.client_code}'.")
    db.refresh(sheet)
    return sheet


def get_sheet(db: Session, sheet_id: int) -> ReferenceSheet:
    sheet = reference_sheet_repo.get(db, id=sheet_id)
    if not sheet:
        raise NotFoundError(f"ReferenceSheet(id={sheet_id}) not found.")
    return sheet


def get_sheets(db: Session, client_code: str) -> list[ReferenceSheet]:
    return reference_sheet_repo.get_by_client(db, client_code=client_code)


def add_marker(db: Session, sheet_id: int, marker_in: ReferenceSheetMarkerCreate) -> ReferenceSheet:
    sheet = get_sheet(db, sheet_id)
    if reference_sheet_repo.get_marker(db, sheet_id=sheet_id, marker=marker_in.marker):
        raise DuplicatedRecordError(f"Marker '{marker_in.marker}' already exists on this sheet.")

    sheet.markers.append(ReferenceSheetMarker(marker=marker_in.marker))
    _commit(db, f"Marker '{marker_in.marker}' already exists on this sheet.")
    db.refresh(sheet)
    return sheet


def _check_in_sheet_period(sheet: ReferenceSheet, dates: list[date]) -> None:
    outside = [d for d in dates if not (sheet.period_start <= d <= sheet.period_end)]
    if outside:
        raise BadRequestError(
            f"Dates {[d.isoformat() for d in outside]} are outside the sheet period "
            f"({sheet.period_start} - {sheet.period_end})."
        )


def _propagate_sheet_change(db: Session, sheet_id: int, marker: str) -> None:
    """シート日付の変更を、そのマーカーを参照している全テンプレートへ反映します（同一トランザクション内）。"""
    for template in plan_template_repo.get_by_sheet_marker(db, sheet_id=sheet_id, marker=marker):
        _regenerate(db, template, VoidReason.SOURCE_DATE_CHANGED)


def add_sheet_events(
    db: Session, sheet_id: int, marker: str, events_in: ReferenceSheetEventsCreate
) -> ReferenceSheetMarker:
    sheet = get_sheet(db, sheet_id)
    marker_obj = reference_sheet_repo.get_marker(db, sheet_id=sheet_id, marker=marker)
    if not marker_obj:
        raise NotFoundError(f"Marker '{marker}' not found on ReferenceSheet(id={sheet_id}).")
    _check_in_sheet_period(sheet, events_in.dates)

    existing = {e.event_date for e in marker_obj.events}
    new_dates = sorted(set(events_in.dates) - existing)
    if new_dates:
        for d in new_dates:
            marker_obj.events.append(ReferenceSheetEvent(event_date=d))
        db.flush()
        _propagate_sheet_change(db, sheet_id, marker)
        _commit(db, "Some dates already exist for this marker.")
        db.refresh(marker_obj)
    return marker_obj


def update_sheet_event(db: Session, event_id: int, event_in: ReferenceSheetEventUpdate) -> ReferenceSheetEvent:
    event = reference_sheet_repo.get_event(db, event_id=event_id)
    if not event:
        raise NotFoundError(f"ReferenceSheetEvent(id={event_id}) not found.")
    marker_obj = event.marker_def
    _check_in_sheet_period(marker_obj.sheet, [event_in.event_date])

    if event.event_date != event_in.event_date:
        event.event_date = event_in.event_date
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            raise DuplicatedRecordError(f"Date {event_in.event_date} already exists for this marker.")
        _propagate_sheet_change(db, marker_obj.sheet_id, marker_obj.marker)
        _commit(db, f"Date {event_in.event_date} already exists for this marker.")
        db.refresh(event)
    return event


def delete_sheet_event(db: Session, event_id: int) -> ReferenceSheetEventResponse:
    event = reference_sheet_repo.get_event(db, event_id=event_id)
    if not event:
        raise NotFoundError(f"ReferenceSheetEvent(id={event_id}) not found.")
    deleted = ReferenceSheetEventResponse.model_validate(event)
    sheet_id, marker = event.marker_def.sheet_id, event.marker_def.marker

    db.delete(event)
    db.flush()
    _propagate_sheet_change(db, sheet_id, marker)
    db.commit()
    return deleted


# ==========================================
# PlanTemplate Service
# ==========================================
def _get_biz(db: Session, biz_id: int) -> BizMaster:
    biz = biz_master_repo.get(db, id=biz_id)
    if not biz:
        raise NotFoundError(f"BizMaster(id={biz_id}) not found.")
    return biz


def _validate_sheet_reference(db: Session, biz: BizMaster, sheet_id: int, marker: str) -> None:
    """参照するシートが存在し、同じ顧客のものであり、マーカーがシート上に実在することを確認します。"""
    sheet = reference_sheet_repo.get(db, id=sheet_id)
    if not sheet:
        raise NotFoundError(f"ReferenceSheet(id={sheet_id}) not found.")
    if sheet.client_code != biz.client_code:
        raise BadRequestError(
            f"ReferenceSheet(id={sheet_id}) belongs to client '{sheet.client_code}', "
            f"but BizMaster(id={biz.id}) belongs to client '{biz.client_code}'."
        )
    if not reference_sheet_repo.get_marker(db, sheet_id=sheet_id, marker=marker):
        raise NotFoundError(f"Marker '{marker}' not found on ReferenceSheet(id={sheet_id}).")


def _get_locked_template(db: Session, template_id: int) -> PlanTemplate:
    template = plan_template_repo.get_locked(db, id=template_id)
    if not template:
        raise NotFoundError(f"PlanTemplate(id={template_id}) not found.")
    return template


def create_template(db: Session, template_in: PlanTemplateCreate) -> PlanTemplate:
    biz = _get_biz(db, template_in.biz_master_id)
    if isinstance(template_in, ReferenceSheetTemplateCreate):
        _validate_sheet_reference(db, biz, template_in.reference_sheet_id, template_in.reference_event_marker)

    template = PlanTemplate(**template_in.model_dump(exclude={"dates", "ui_metadata"}))
    template.ui_metadata = ui_metadata_to_dict(template_in.ui_metadata)
    if isinstance(template_in, ExplicitDatesTemplateCreate):
        template.explicit_dates = [PlanExplicitDate(local_date=d) for d in sorted(set(template_in.dates))]
    db.add(template)
    db.flush()

    _ensure_materialized(db, template, _now() + MATERIALIZE_HORIZON)
    _commit(db, "PlanTemplate could not be created due to a conflicting record.")
    db.refresh(template)
    return template


def get_template(db: Session, template_id: int) -> PlanTemplate:
    template = plan_template_repo.get(db, id=template_id)
    if not template:
        raise NotFoundError(f"PlanTemplate(id={template_id}) not found.")
    return template


def get_templates(db: Session, biz_id: int) -> list[PlanTemplate]:
    _get_biz(db, biz_id)
    return plan_template_repo.get_by_biz(db, biz_master_id=biz_id)


def update_template(db: Session, template_id: int, template_in: PlanTemplateUpdate) -> PlanTemplate:
    template = _get_locked_template(db, template_id)
    update_data = template_in.model_dump(exclude_unset=True, exclude={"ui_metadata"})
    ui_changed = "ui_metadata" in template_in.model_fields_set

    # 型に合わない項目・必須項目への null は受け付けない
    not_applicable = set()
    if template.template_type != TemplateType.RRULE:
        not_applicable |= update_data.keys() & RRULE_FIELDS
    if template.template_type != TemplateType.REFERENCE_SHEET_BASED:
        not_applicable |= update_data.keys() & SHEET_FIELDS
    if not_applicable:
        raise BadRequestError(
            f"Fields {sorted(not_applicable)} are not applicable to template type '{template.template_type.value}'."
        )
    null_fields = sorted(k for k, v in update_data.items() if v is None)
    if null_fields:
        raise BadRequestError(f"Fields {null_fields} must not be null.")

    if update_data.keys() & {"reference_sheet_id", "reference_event_marker"}:
        _validate_sheet_reference(
            db,
            _get_biz(db, template.biz_master_id),
            update_data.get("reference_sheet_id", template.reference_sheet_id),
            update_data.get("reference_event_marker", template.reference_event_marker),
        )

    name_changed = "name" in update_data and update_data["name"] != template.name
    generation_changed = any(getattr(template, k) != v for k, v in update_data.items() if k != "name")

    for field, value in update_data.items():
        setattr(template, field, value)
    if ui_changed:
        # 表示のみの変更のためリビジョンは上げず、キャッシュの実効値だけ更新する
        template.ui_metadata = ui_metadata_to_dict(template_in.ui_metadata)

    if generation_changed:
        _regenerate(db, template, VoidReason.TEMPLATE_REVISED)
    if name_changed:
        plan_cache_repo.rename_template(db, template_id=template.id, title=template.name)
    if ui_changed:
        plan_cache_repo.refresh_ui_metadata(db, template=template)

    _commit(db, "PlanTemplate could not be updated due to a conflicting record.")
    db.refresh(template)
    return template


def delete_template(db: Session, template_id: int) -> PlanTemplate:
    """論理削除。未来のインスタンスは無効化し、過去のインスタンスは履歴として残します。"""
    template = _get_locked_template(db, template_id)

    future = plan_instance_repo.get_live_in_window(db, template_id=template.id, lower=_now(), upper=FAR_FUTURE)
    future_ids = [i.id for i in future]
    if future_ids:
        plan_instance_void_repo.void_many(db, instance_ids=future_ids, reason=VoidReason.TEMPLATE_DELETED)
        plan_cache_repo.purge(db, instance_ids=future_ids)
    template.soft_delete()
    db.commit()

    # 全局scopeにより削除済みは通常の取得で見えないため、解除して読み直す
    deleted = plan_template_repo.get_including_deleted(db, id=template_id)
    assert deleted is not None
    return deleted


def restore_template(db: Session, template_id: int) -> PlanTemplate:
    template = plan_template_repo.get_locked(db, id=template_id, include_deleted=True)
    if not template or not template.is_deleted:
        raise NotFoundError(f"Deleted PlanTemplate(id={template_id}) not found.")

    template.restore()
    # 削除期間中は生成していないため、今からホライズンまでを新しいリビジョンで生成し直す
    template.revision += 1
    template.materialized_until = None
    _ensure_materialized(db, template, _now() + MATERIALIZE_HORIZON)

    _commit(db, "PlanTemplate could not be restored due to a conflicting record.")
    db.refresh(template)
    return template


def add_explicit_dates(db: Session, template_id: int, dates: list[date]) -> PlanTemplate:
    template = _get_locked_template(db, template_id)
    if template.template_type != TemplateType.EXPLICIT_DATES:
        raise BadRequestError("Explicit dates can only be added to EXPLICIT_DATES templates.")

    if plan_template_repo.add_explicit_dates(db, template=template, dates=dates):
        _regenerate(db, template, VoidReason.SOURCE_DATE_CHANGED)
        _commit(db, "Some dates already exist for this template.")
        db.refresh(template)
    return template


def remove_explicit_date(db: Session, template_id: int, local_date: date) -> PlanTemplate:
    template = _get_locked_template(db, template_id)
    if not plan_template_repo.remove_explicit_date(db, template=template, local_date=local_date):
        raise NotFoundError(f"Date {local_date} not found on PlanTemplate(id={template_id}).")

    _regenerate(db, template, VoidReason.SOURCE_DATE_CHANGED)
    db.commit()
    db.refresh(template)
    return template


# ==========================================
# PlanInstance / Occurrence Service
# ==========================================
def get_instances(db: Session, template_id: int, skip: int = 0, limit: int = 100) -> list[PlanInstanceResponse]:
    """無効化済みも含めた全インスタンス（監査用）。削除済みテンプレートの履歴も参照できます。"""
    if not plan_template_repo.get_including_deleted(db, id=template_id):
        raise NotFoundError(f"PlanTemplate(id={template_id}) not found.")

    return [
        _to_instance_response(instance, void)
        for instance, void in plan_instance_repo.get_with_void_status(
            db, template_id=template_id, skip=skip, limit=limit
        )
    ]


def _to_instance_response(instance: PlanInstance, void: PlanInstanceVoid | None) -> PlanInstanceResponse:
    return PlanInstanceResponse(
        id=instance.id,
        template_id=instance.template_id,
        template_revision=instance.template_revision,
        scheduled_start=instance.scheduled_start,
        scheduled_end=instance.scheduled_end,
        source_sheet_event_id=instance.source_sheet_event_id,
        occurrence_date=instance.occurrence_date,
        origin=instance.origin,
        overrides_instance_id=instance.overrides_instance_id,
        ui_metadata=instance.ui_metadata,
        generated_at=instance.generated_at,
        voided=void is not None,
        void_reason=void.reason if void else None,
        void_note=void.note if void else None,
        voided_at=void.voided_at if void else None,
    )


def _get_live_instance_for_manual_change(db: Session, instance_id: int) -> tuple[PlanInstance, PlanTemplate]:
    """
    手動変更の対象インスタンスを取得します。
    テンプレートを行ロックしてから無効化状態を確認するため、再生成や他の手動変更と競合しません。
    """
    found = plan_instance_repo.get_with_void(db, id=instance_id)
    if not found:
        raise NotFoundError(f"PlanInstance(id={instance_id}) not found.")
    instance, _ = found

    template = plan_template_repo.get_locked(db, id=instance.template_id)
    if not template:
        raise BadRequestError(f"PlanTemplate(id={instance.template_id}) of this instance has been deleted.")

    # ロック取得後に改めて確認（ロック待ちの間に無効化されている可能性があるため）
    found = plan_instance_repo.get_with_void(db, id=instance_id)
    assert found is not None
    if found[1] is not None:
        raise DuplicatedRecordError(
            f"PlanInstance(id={instance_id}) is already voided ({found[1].reason.value}).",
            error_code=ErrorCode.INSTANCE_ALREADY_VOIDED,
        )
    return instance, template


def override_instance(db: Session, instance_id: int, override_in: PlanInstanceOverride) -> PlanInstanceResponse:
    """
    特定のインスタンスだけ時刻を変更します（テンプレートのルールは変えません）。
    内部では、元インスタンスを MANUAL で無効化し、同じ発生枠を引き継いだ新しいインスタンスを追記します。
    """
    original, template = _get_live_instance_for_manual_change(db, instance_id)

    start = override_in.scheduled_start
    end = override_in.scheduled_end or start + (original.scheduled_end - original.scheduled_start)
    if start == original.scheduled_start and end == original.scheduled_end:
        raise BadRequestError("The new time is the same as the current one.")

    # ui_metadata: 省略時は元インスタンスの値を引き継ぐ / null ならクリア
    ui_metadata = (
        ui_metadata_to_dict(override_in.ui_metadata)
        if "ui_metadata" in override_in.model_fields_set
        else original.ui_metadata
    )

    plan_instance_void_repo.void_many(db, instance_ids=[original.id], reason=VoidReason.MANUAL, note=override_in.note)
    plan_cache_repo.purge(db, instance_ids=[original.id])
    replacement = plan_instance_repo.add_override(
        db, template=template, original=original, start=start, end=end, ui_metadata=ui_metadata
    )
    plan_cache_repo.add_for_instances(db, template=template, instances=[replacement])

    _commit(db, f"PlanInstance(id={instance_id}) was changed concurrently. Please retry.")
    db.refresh(replacement)
    return _to_instance_response(replacement, None)


def cancel_instance(db: Session, instance_id: int, cancel_in: PlanInstanceCancel) -> PlanInstanceResponse:
    """特定のインスタンスだけを取り消します。ルールからの再生成でも復活しません。"""
    instance, _ = _get_live_instance_for_manual_change(db, instance_id)

    plan_instance_void_repo.void_many(db, instance_ids=[instance.id], reason=VoidReason.MANUAL, note=cancel_in.note)
    plan_cache_repo.purge(db, instance_ids=[instance.id])
    _commit(db, f"PlanInstance(id={instance_id}) was changed concurrently. Please retry.")

    found = plan_instance_repo.get_with_void(db, id=instance_id)
    assert found is not None
    return _to_instance_response(*found)


def revert_instance(db: Session, instance_id: int, revert_in: PlanInstanceRevert) -> PlanRevertResponse:
    """
    手動変更（オーバーライド・取消）を取り消し、その発生枠をテンプレートのルールの管理下に戻します。
    置き換えの連鎖のどのインスタンスを指定しても、連鎖全体（=1つの発生枠）が対象になります。
    内部では、有効なオーバーライドを REVERTED で無効化し、解除記録を追記したうえで、ルールから再生成します。
    """
    found = plan_instance_repo.get_with_void(db, id=instance_id)
    if not found:
        raise NotFoundError(f"PlanInstance(id={instance_id}) not found.")
    template = plan_template_repo.get_locked(db, id=found[0].template_id)
    if not template:
        raise BadRequestError(f"PlanTemplate(id={found[0].template_id}) of this instance has been deleted.")

    # 連鎖を遡って起点（ルールから生成されたインスタンス）を探す
    root = found[0]
    while root.overrides_instance_id is not None:
        parent = plan_instance_repo.get(db, id=root.overrides_instance_id)
        assert parent is not None
        root = parent
    if root.origin == InstanceOrigin.ONE_OFF:
        raise BadRequestError("One-off instances are not tied to the rule, so they cannot be reverted. Cancel it instead.")

    root_found = plan_instance_repo.get_with_void(db, id=root.id)
    assert root_found is not None
    root_void = root_found[1]
    if root_void is None or root_void.reason != VoidReason.MANUAL:
        raise BadRequestError(f"PlanInstance(id={instance_id}) has not been manually changed or cancelled.")
    if plan_slot_release_repo.exists(db, root_instance_id=root.id):
        raise DuplicatedRecordError(
            f"The manual change of PlanInstance(id={instance_id}) has already been reverted.",
            error_code=ErrorCode.INSTANCE_ALREADY_VOIDED,
        )
    # 過去の発生枠は再生成されない（過去は不変）ため、戻すとカレンダーから消えてしまう
    if root.scheduled_start < _now():
        raise BadRequestError(
            "The original occurrence is in the past and cannot be regenerated. Cancel or override it instead."
        )

    # 連鎖の末端（現在のオーバーライド）が有効なら無効化する
    terminal = root
    while (child := plan_instance_repo.get_override_child(db, instance_id=terminal.id)) is not None:
        terminal = child
    voided: PlanInstance | None = None
    terminal_found = plan_instance_repo.get_with_void(db, id=terminal.id)
    assert terminal_found is not None
    if terminal is not root and terminal_found[1] is None:
        plan_instance_void_repo.void_many(db, instance_ids=[terminal.id], reason=VoidReason.REVERTED, note=revert_in.note)
        plan_cache_repo.purge(db, instance_ids=[terminal.id])
        voided = terminal

    plan_slot_release_repo.add(db, root_instance_id=root.id, template_id=template.id, note=revert_in.note)
    # 解除された発生枠をルールから再生成（他の発生枠は変わらないため、そのまま維持される）
    _regenerate(db, template, VoidReason.TEMPLATE_REVISED)
    _commit(db, f"PlanInstance(id={instance_id}) was changed concurrently. Please retry.")

    root_slot = _slot(root.occurrence_date, root.source_sheet_event_id)
    restored = next(
        (
            i
            for i in plan_instance_repo.get_live_in_window(
                db, template_id=template.id, lower=_now(), upper=FAR_FUTURE, generated_only=True
            )
            if _slot(i.occurrence_date, i.source_sheet_event_id) == root_slot
        ),
        None,
    )

    def response(instance: PlanInstance | None) -> PlanInstanceResponse | None:
        if instance is None:
            return None
        row = plan_instance_repo.get_with_void(db, id=instance.id)
        assert row is not None
        return _to_instance_response(*row)

    return PlanRevertResponse(voided_instance=response(voided), restored_instance=response(restored))


def add_one_off_instance(db: Session, template_id: int, one_off_in: PlanOneOffCreate) -> PlanInstanceResponse:
    """
    発生枠に紐づかない単発のインスタンスを追加します（例: 臨時の作業）。
    ルールからの再生成の対象外のため、テンプレートを変更しても維持されます（テンプレート削除時は無効化）。
    """
    template = _get_locked_template(db, template_id)

    start = one_off_in.scheduled_start
    end = one_off_in.scheduled_end or start + timedelta(minutes=template.duration_minutes)
    instance = plan_instance_repo.add_one_off(
        db,
        template=template,
        start=start,
        end=end,
        occurrence_date=start.astimezone(ZoneInfo(template.timezone)).date(),
        ui_metadata=ui_metadata_to_dict(one_off_in.ui_metadata),
    )
    plan_cache_repo.add_for_instances(db, template=template, instances=[instance])

    _commit(db, "One-off instance could not be created due to a conflicting record.")
    db.refresh(instance)
    return _to_instance_response(instance, None)


def get_occurrences(db: Session, biz_id: int, start: datetime, end: datetime) -> list[PlanOccurrenceCache]:
    """
    範囲内の予定をシャドウテーブルから取得します。
    範囲が未生成区間にかかる場合のみ、先にその区間を生成します（生成済みなら読み取りのみ）。
    """
    if start.tzinfo is None or end.tzinfo is None:
        raise BadRequestError("start and end must include a timezone offset (e.g. 2026-11-01T00:00:00+09:00).")
    if end <= start:
        raise BadRequestError("end must be after start.")
    if end - start > MAX_QUERY_SPAN:
        raise BadRequestError(f"Range must not exceed {MAX_QUERY_SPAN.days} days.")
    _get_biz(db, biz_id)

    now = _now()
    until = min(end, now + MAX_LOOKAHEAD)
    materialized = False
    for template in plan_template_repo.get_by_biz(db, biz_master_id=biz_id):
        if (template.materialized_until or now) < until:
            locked = _get_locked_template(db, template.id)
            _ensure_materialized(db, locked, until)
            materialized = True
    if materialized:
        _commit(db, "Occurrences could not be generated due to a conflicting record.")

    return plan_cache_repo.query_range(db, biz_master_id=biz_id, start=start, end=end)


def rebuild_cache(db: Session, template_id: int) -> int:
    """シャドウテーブルを plan_instance - plan_instance_void から作り直します。再構築した件数を返します。"""
    template = get_template(db, template_id)
    live = plan_instance_repo.get_live_by_template(db, template_id=template.id)

    plan_cache_repo.purge_template(db, template_id=template.id)
    plan_cache_repo.add_for_instances(db, template=template, instances=live)
    db.commit()
    return len(live)
