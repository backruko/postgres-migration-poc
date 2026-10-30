# src/infrastructure/repositories/Plan.py
"""
domain_plan 用のリポジトリ群。

※ 生成・無効化・キャッシュ更新は「1つのトランザクションで全部成功 or 全部失敗」が必要なため、
  ここに追加したメソッドは commit せず flush のみ行います。commit は Service 層で1回だけ行います。
"""
from datetime import date, datetime
from typing import Any, Iterable, Sequence

from pydantic import BaseModel
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session, aliased

from src.core.error_codes import ErrorCode
from src.domain_biz.models import BizMaster
from src.core.exceptions import BadRequestError
from src.domain_plan import slot_events
from src.domain_plan.engine import Occurrence
from src.domain_plan.models import (
    BusinessCalendar,
    BusinessCalendarHoliday,
    InstanceOrigin,
    PlanExplicitDate,
    PlanInstance,
    PlanInstanceVoid,
    PlanOccurrenceCache,
    PlanSlotRelease,
    PlanTemplate,
    ReferenceSheet,
    ReferenceSheetEvent,
    ReferenceSheetMarker,
    VoidReason,
    merge_ui_metadata,
)
from src.domain_plan.schemas import PlanTemplateUpdate
from src.infrastructure.repositories.base import CRUDBase, SoftCRUDBase


# ==========================================
#  BusinessCalendar Repository
# ==========================================
class BusinessCalendarRepository(CRUDBase[BusinessCalendar, BaseModel, BaseModel]):

    def get_by_code(self, db: Session, *, code: str) -> BusinessCalendar | None:
        return db.scalars(select(self.model).where(self.model.code == code)).first()

    def get_all(self, db: Session) -> list[BusinessCalendar]:
        return list(db.scalars(select(self.model).order_by(self.model.code)).all())

    def get_holidays(
        self, db: Session, *, calendar_id: int, start: date | None = None, end: date | None = None
    ) -> list[BusinessCalendarHoliday]:
        stmt = select(BusinessCalendarHoliday).where(BusinessCalendarHoliday.calendar_id == calendar_id)
        if start is not None:
            stmt = stmt.where(BusinessCalendarHoliday.holiday_date >= start)
        if end is not None:
            stmt = stmt.where(BusinessCalendarHoliday.holiday_date <= end)
        return list(db.scalars(stmt.order_by(BusinessCalendarHoliday.holiday_date)).all())

    def get_holiday_dates(self, db: Session, *, calendar_id: int) -> set[date]:
        stmt = select(BusinessCalendarHoliday.holiday_date).where(BusinessCalendarHoliday.calendar_id == calendar_id)
        return set(db.scalars(stmt).all())


# ==========================================
#  ReferenceSheet Repository
# ==========================================
class ReferenceSheetRepository(CRUDBase[ReferenceSheet, BaseModel, BaseModel]):

    def get_by_client(self, db: Session, *, client_code: str) -> list[ReferenceSheet]:
        stmt = select(self.model).where(self.model.client_code == client_code).order_by(self.model.period_start)
        return list(db.scalars(stmt).all())

    def get_by_client_and_name(self, db: Session, *, client_code: str, name: str) -> ReferenceSheet | None:
        stmt = select(self.model).where(self.model.client_code == client_code, self.model.name == name)
        return db.scalars(stmt).first()

    def get_marker(self, db: Session, *, sheet_id: int, marker: str) -> ReferenceSheetMarker | None:
        stmt = select(ReferenceSheetMarker).where(
            ReferenceSheetMarker.sheet_id == sheet_id,
            ReferenceSheetMarker.marker == marker,
        )
        return db.scalars(stmt).first()

    def get_generated_markers_by_calendar(self, db: Session, *, calendar_id: int) -> list[ReferenceSheetMarker]:
        stmt = select(ReferenceSheetMarker).where(
            ReferenceSheetMarker.business_calendar_id == calendar_id, ReferenceSheetMarker.rrule.is_not(None)
        )
        return list(db.scalars(stmt.order_by(ReferenceSheetMarker.id)).all())

    def get_event(self, db: Session, *, event_id: int) -> ReferenceSheetEvent | None:
        stmt = select(ReferenceSheetEvent).where(ReferenceSheetEvent.id == event_id)
        return db.scalars(stmt).first()

    def get_event_dates(self, db: Session, *, sheet_id: int, marker: str) -> list[tuple[int, date]]:
        """(sheet_id, marker) に紐づく日付を (event_id, event_date) で返します（エンジン入力用）"""
        stmt = (
            select(ReferenceSheetEvent.id, ReferenceSheetEvent.event_date)
            .join(ReferenceSheetMarker, ReferenceSheetEvent.marker_id == ReferenceSheetMarker.id)
            .where(
                ReferenceSheetMarker.sheet_id == sheet_id,
                ReferenceSheetMarker.marker == marker,
                ReferenceSheetEvent.is_cancelled.is_(False),  # 人が取り消した生成日は除外
            )
        )
        return [(row.id, row.event_date) for row in db.execute(stmt)]

    def get_events_on(
        self, db: Session, *, day: date, include_cancelled: bool
    ) -> list[tuple[ReferenceSheetEvent, ReferenceSheetMarker, ReferenceSheet]]:
        """全顧客のシートの、指定日の日付を返します（顧客コード・シート名・マーカー順）"""
        stmt = (
            select(ReferenceSheetEvent, ReferenceSheetMarker, ReferenceSheet)
            .join(ReferenceSheetMarker, ReferenceSheetMarker.id == ReferenceSheetEvent.marker_id)
            .join(ReferenceSheet, ReferenceSheet.id == ReferenceSheetMarker.sheet_id)
            .where(ReferenceSheetEvent.event_date == day)
            .order_by(ReferenceSheet.client_code, ReferenceSheet.name, ReferenceSheetMarker.marker, ReferenceSheetEvent.id)
        )
        if not include_cancelled:
            stmt = stmt.where(ReferenceSheetEvent.is_cancelled.is_(False))
        return [(row[0], row[1], row[2]) for row in db.execute(stmt)]


# ==========================================
#  PlanTemplate Repository
# ==========================================
class PlanTemplateRepository(SoftCRUDBase[PlanTemplate, BaseModel, PlanTemplateUpdate]):

    def get_locked(self, db: Session, *, id: int, include_deleted: bool = False) -> PlanTemplate | None:
        """
        行ロック付きで取得します（同時マテリアライズによる二重生成を防ぐため）。
        populate_existing により、セッション内の古い値もロック取得後の最新値で上書きします。
        """
        stmt = (
            select(self.model)
            .where(self.model.id == id)
            .with_for_update()
            .execution_options(populate_existing=True, include_deleted=include_deleted)
        )
        return db.scalars(stmt).first()

    def get_including_deleted(self, db: Session, *, id: int) -> PlanTemplate | None:
        stmt = select(self.model).where(self.model.id == id).execution_options(include_deleted=True)
        return db.scalars(stmt).first()

    def get_by_biz(self, db: Session, *, biz_master_id: int) -> list[PlanTemplate]:
        stmt = select(self.model).where(self.model.biz_master_id == biz_master_id).order_by(self.model.id)
        return list(db.scalars(stmt).all())

    def get_by_client(self, db: Session, *, client_code: str) -> list[PlanTemplate]:
        """顧客に属する全業務の有効なテンプレート（論理削除された業務・テンプレートは全局scopeで除外）"""
        stmt = (
            select(self.model)
            .join(BizMaster, BizMaster.id == self.model.biz_master_id)
            .where(BizMaster.client_code == client_code)
            .order_by(self.model.id)
        )
        return list(db.scalars(stmt).all())

    def get_by_client_with_biz(self, db: Session, *, client_code: str) -> list[tuple[PlanTemplate, str, str]]:
        """顧客に属する全業務の有効なテンプレート（業務コード・業務名付き）"""
        stmt = (
            select(self.model, BizMaster.business_code, BizMaster.name)
            .join(BizMaster, BizMaster.id == self.model.biz_master_id)
            .where(BizMaster.client_code == client_code)
            .order_by(BizMaster.business_code, self.model.id)
        )
        return [(row[0], row[1], row[2]) for row in db.execute(stmt)]

    def get_all(self, db: Session) -> list[PlanTemplate]:
        """全顧客の有効なテンプレート（論理削除された業務・テンプレートは全局scopeで除外）"""
        stmt = (
            select(self.model)
            .join(BizMaster, BizMaster.id == self.model.biz_master_id)
            .order_by(self.model.id)
        )
        return list(db.scalars(stmt).all())

    def get_by_sheet_marker(self, db: Session, *, sheet_id: int, marker: str | None = None) -> list[PlanTemplate]:
        """シート（とマーカー）を参照している有効なテンプレートを行ロック付きで取得します"""
        stmt = select(self.model).where(self.model.reference_sheet_id == sheet_id)
        if marker is not None:
            stmt = stmt.where(self.model.reference_event_marker == marker)
        return list(db.scalars(stmt.order_by(self.model.id).with_for_update()).all())

    def get_by_calendar(self, db: Session, *, calendar_id: int) -> list[PlanTemplate]:
        """営業日カレンダーを使用している有効なテンプレートを行ロック付きで取得します"""
        stmt = select(self.model).where(self.model.business_calendar_id == calendar_id)
        return list(db.scalars(stmt.order_by(self.model.id).with_for_update()).all())

    def add_explicit_dates(self, db: Session, *, template: PlanTemplate, dates: Iterable[date]) -> int:
        """未登録の日付だけを追加し、追加件数を返します"""
        existing = {d.local_date for d in template.explicit_dates}
        new_dates = sorted(set(dates) - existing)
        for d in new_dates:
            template.explicit_dates.append(PlanExplicitDate(local_date=d))
        db.flush()
        return len(new_dates)

    def remove_explicit_date(self, db: Session, *, template: PlanTemplate, local_date: date) -> bool:
        target = next((d for d in template.explicit_dates if d.local_date == local_date), None)
        if target is None:
            return False
        template.explicit_dates.remove(target)
        db.flush()
        return True


# ==========================================
#  PlanInstance Repository（不変）
# ==========================================
class PlanInstanceRepository(CRUDBase[PlanInstance, BaseModel, BaseModel]):

    def update(self, db: Session, *, db_obj: PlanInstance, obj_in: Any) -> PlanInstance:
        raise BadRequestError("PlanInstance is immutable. Void it instead.", error_code=ErrorCode.IMMUTABLE_RECORD)

    def remove(self, db: Session, *, id: int) -> PlanInstance | None:
        raise BadRequestError("PlanInstance is immutable. Void it instead.", error_code=ErrorCode.IMMUTABLE_RECORD)

    def _live_stmt(self, template_id: int):
        return (
            select(PlanInstance)
            .outerjoin(PlanInstanceVoid, PlanInstanceVoid.instance_id == PlanInstance.id)
            .where(PlanInstance.template_id == template_id, PlanInstanceVoid.instance_id.is_(None))
        )

    def get_live_in_window(
        self, db: Session, *, template_id: int, lower: datetime, upper: datetime, generated_only: bool = False
    ) -> list[PlanInstance]:
        """
        :param generated_only: True の場合、ルールから生成されたものだけを返します（手動変更・単発追加を除外）
        """
        stmt = self._live_stmt(template_id).where(
            PlanInstance.scheduled_start >= lower, PlanInstance.scheduled_start < upper
        )
        if generated_only:
            stmt = stmt.where(PlanInstance.origin == InstanceOrigin.GENERATED)
        return list(db.scalars(stmt).all())

    def get_with_void(self, db: Session, *, id: int) -> tuple[PlanInstance, PlanInstanceVoid | None] | None:
        stmt = (
            select(PlanInstance, PlanInstanceVoid)
            .outerjoin(PlanInstanceVoid, PlanInstanceVoid.instance_id == PlanInstance.id)
            .where(PlanInstance.id == id)
        )
        row = db.execute(stmt).first()
        return (row[0], row[1]) if row else None

    def get_manual_history(self, db: Session, *, template_id: int) -> list[tuple[PlanInstance, PlanInstanceVoid | None]]:
        """手動操作に関わるインスタンス（MANUAL で無効化されたもの・手動で作られたもの）を返します"""
        stmt = (
            select(PlanInstance, PlanInstanceVoid)
            .outerjoin(PlanInstanceVoid, PlanInstanceVoid.instance_id == PlanInstance.id)
            .where(
                PlanInstance.template_id == template_id,
                (PlanInstanceVoid.reason == VoidReason.MANUAL) | (PlanInstance.origin != InstanceOrigin.GENERATED),
            )
        )
        return [(row[0], row[1]) for row in db.execute(stmt)]

    def get_cancelled_for_client(
        self, db: Session, *, client_code: str, start: datetime, end: datetime
    ) -> list[tuple[PlanInstance, PlanInstanceVoid, PlanTemplate, str, str]]:
        """
        顧客の「取り消された予定」（MANUAL で無効化され、置き換えのオーバーライドが無いもの）を開始時刻順に返します。
        置き換えがあるもの（=日時を変更した元の予定）は取消ではないため含めません。
        論理削除された業務・テンプレートの予定は全局scopeで除外されます。
        """
        child = aliased(PlanInstance)
        stmt = (
            select(PlanInstance, PlanInstanceVoid, PlanTemplate, BizMaster.business_code, BizMaster.name)
            .join(PlanInstanceVoid, PlanInstanceVoid.instance_id == PlanInstance.id)
            .join(PlanTemplate, PlanTemplate.id == PlanInstance.template_id)
            .join(BizMaster, BizMaster.id == PlanTemplate.biz_master_id)
            .outerjoin(child, child.overrides_instance_id == PlanInstance.id)
            .where(
                BizMaster.client_code == client_code,
                PlanInstanceVoid.reason == VoidReason.MANUAL,
                child.id.is_(None),
                PlanInstance.scheduled_start >= start,
                PlanInstance.scheduled_start < end,
            )
            .order_by(PlanInstance.scheduled_start, PlanInstance.id)
        )
        return [(row[0], row[1], row[2], row[3], row[4]) for row in db.execute(stmt)]

    def get_voided_starting_in(
        self, db: Session, *, start: datetime, end: datetime
    ) -> list[tuple[PlanInstance, PlanInstanceVoid, PlanTemplate, BizMaster]]:
        """
        全顧客の、範囲内に開始する予定だった無効化済みインスタンスを開始時刻順に返します（理由付き）。
        削除されたテンプレートの予定も履歴として含めます（論理削除された業務の予定は含めません）。
        """
        stmt = (
            select(PlanInstance, PlanInstanceVoid, PlanTemplate, BizMaster)
            .join(PlanInstanceVoid, PlanInstanceVoid.instance_id == PlanInstance.id)
            .join(PlanTemplate, PlanTemplate.id == PlanInstance.template_id)
            .join(BizMaster, BizMaster.id == PlanTemplate.biz_master_id)
            .where(
                BizMaster.is_deleted.is_(False),
                PlanInstance.scheduled_start >= start,
                PlanInstance.scheduled_start < end,
            )
            .order_by(PlanInstance.scheduled_start, PlanInstance.id)
            .execution_options(include_deleted=True)
        )
        return [(row[0], row[1], row[2], row[3]) for row in db.execute(stmt)]

    def get_override_child(self, db: Session, *, instance_id: int) -> PlanInstance | None:
        """このインスタンスを置き換えたオーバーライド（あれば）"""
        stmt = select(PlanInstance).where(PlanInstance.overrides_instance_id == instance_id)
        return db.scalars(stmt).first()

    def add_override(
        self,
        db: Session,
        *,
        template: PlanTemplate,
        original: PlanInstance,
        start: datetime,
        end: datetime,
        ui_metadata: dict[str, Any] | None,
    ) -> PlanInstance:
        """元インスタンスと同じ発生枠を引き継いだ、手動オーバーライドのインスタンスを作成します"""
        instance = PlanInstance(
            template_id=template.id,
            template_revision=template.revision,
            origin=InstanceOrigin.OVERRIDE,
            scheduled_start=start,
            scheduled_end=end,
            source_sheet_event_id=original.source_sheet_event_id,
            occurrence_date=original.occurrence_date,
            overrides_instance_id=original.id,
            ui_metadata=ui_metadata,
        )
        db.add(instance)
        db.flush()
        slot_events.record_created(db, [instance])
        return instance

    def add_one_off(
        self,
        db: Session,
        *,
        template: PlanTemplate,
        start: datetime,
        end: datetime,
        occurrence_date: date,
        ui_metadata: dict[str, Any] | None,
    ) -> PlanInstance:
        """発生枠に紐づかない単発のインスタンスを作成します"""
        instance = PlanInstance(
            template_id=template.id,
            template_revision=template.revision,
            origin=InstanceOrigin.ONE_OFF,
            scheduled_start=start,
            scheduled_end=end,
            occurrence_date=occurrence_date,
            ui_metadata=ui_metadata,
        )
        db.add(instance)
        db.flush()
        slot_events.record_created(db, [instance])
        return instance

    def get_live_by_template(self, db: Session, *, template_id: int) -> list[PlanInstance]:
        return list(db.scalars(self._live_stmt(template_id)).all())

    def add_many(self, db: Session, *, template: PlanTemplate, occurrences: Sequence[Occurrence]) -> list[PlanInstance]:
        instances = [
            PlanInstance(
                template_id=template.id,
                template_revision=template.revision,
                origin=InstanceOrigin.GENERATED,
                scheduled_start=o.start,
                scheduled_end=o.end,
                source_sheet_event_id=o.source_sheet_event_id,
                occurrence_date=o.occurrence_date,
            )
            for o in occurrences
        ]
        db.add_all(instances)
        db.flush()
        slot_events.record_created(db, instances)
        return instances

    def get_with_void_status(
        self, db: Session, *, template_id: int, skip: int = 0, limit: int = 100
    ) -> list[tuple[PlanInstance, PlanInstanceVoid | None]]:
        stmt = (
            select(PlanInstance, PlanInstanceVoid)
            .outerjoin(PlanInstanceVoid, PlanInstanceVoid.instance_id == PlanInstance.id)
            .where(PlanInstance.template_id == template_id)
            .order_by(PlanInstance.scheduled_start, PlanInstance.id)
            .offset(skip)
            .limit(limit)
        )
        return [(row[0], row[1]) for row in db.execute(stmt)]


# ==========================================
#  PlanInstanceVoid Repository（追記のみ）
# ==========================================
class PlanInstanceVoidRepository:

    def void_many(
        self, db: Session, *, instance_ids: Sequence[int], reason: VoidReason, note: str | None = None
    ) -> None:
        db.add_all(PlanInstanceVoid(instance_id=i, reason=reason, note=note) for i in instance_ids)
        db.flush()
        slot_events.record_voided(db, list(instance_ids), reason)


# ==========================================
#  PlanSlotRelease Repository（追記のみ）
# ==========================================
class PlanSlotReleaseRepository:

    def get_released_root_ids(self, db: Session, *, template_id: int) -> set[int]:
        stmt = select(PlanSlotRelease.root_instance_id).where(PlanSlotRelease.template_id == template_id)
        return set(db.scalars(stmt).all())

    def exists(self, db: Session, *, root_instance_id: int) -> bool:
        stmt = select(PlanSlotRelease.root_instance_id).where(PlanSlotRelease.root_instance_id == root_instance_id)
        return db.scalars(stmt).first() is not None

    def add(self, db: Session, *, root_instance_id: int, template_id: int, note: str | None) -> None:
        db.add(PlanSlotRelease(root_instance_id=root_instance_id, template_id=template_id, note=note))
        db.flush()


# ==========================================
#  PlanOccurrenceCache Repository（シャドウテーブル）
# ==========================================
class PlanOccurrenceCacheRepository:

    def add_for_instances(self, db: Session, *, template: PlanTemplate, instances: Sequence[PlanInstance]) -> None:
        db.add_all(
            PlanOccurrenceCache(
                instance_id=i.id,
                template_id=template.id,
                biz_master_id=template.biz_master_id,
                title=template.name,
                starts_at=i.scheduled_start,
                ends_at=i.scheduled_end,
                is_override=i.origin == InstanceOrigin.OVERRIDE,
                origin=i.origin,
                ui_metadata=merge_ui_metadata(template.ui_metadata, i.ui_metadata),
            )
            for i in instances
        )
        db.flush()

    def purge(self, db: Session, *, instance_ids: Sequence[int]) -> None:
        if instance_ids:
            db.execute(delete(PlanOccurrenceCache).where(PlanOccurrenceCache.instance_id.in_(instance_ids)))

    def purge_template(self, db: Session, *, template_id: int) -> None:
        db.execute(delete(PlanOccurrenceCache).where(PlanOccurrenceCache.template_id == template_id))

    def refresh_ui_metadata(self, db: Session, *, template: PlanTemplate) -> None:
        """テンプレートの表示用メタデータ変更後、そのテンプレートのキャッシュ行の実効値を再計算します"""
        stmt = (
            select(PlanOccurrenceCache, PlanInstance.ui_metadata)
            .join(PlanInstance, PlanInstance.id == PlanOccurrenceCache.instance_id)
            .where(PlanOccurrenceCache.template_id == template.id)
        )
        for cache, instance_meta in db.execute(stmt):
            cache.ui_metadata = merge_ui_metadata(template.ui_metadata, instance_meta)
        db.flush()

    def rename_template(self, db: Session, *, template_id: int, title: str) -> None:
        db.execute(
            update(PlanOccurrenceCache).where(PlanOccurrenceCache.template_id == template_id).values(title=title)
        )

    def query_range(
        self, db: Session, *, biz_master_id: int, start: datetime, end: datetime
    ) -> list[PlanOccurrenceCache]:
        """GiSTインデックス（period &&）による重なり検索"""
        stmt = (
            select(PlanOccurrenceCache)
            .where(
                PlanOccurrenceCache.biz_master_id == biz_master_id,
                PlanOccurrenceCache.period.op("&&")(func.tstzrange(start, end, "[)")),
            )
            .order_by(PlanOccurrenceCache.starts_at, PlanOccurrenceCache.instance_id)
        )
        return list(db.scalars(stmt).all())

    def query_range_for_client(
        self, db: Session, *, client_code: str, start: datetime, end: datetime
    ) -> list[tuple[PlanOccurrenceCache, str, str]]:
        """
        顧客に属する全業務の予定を重なり検索します（業務コード・業務名付き）。
        論理削除された業務は全局scopeで除外されます。
        """
        stmt = (
            select(PlanOccurrenceCache, BizMaster.business_code, BizMaster.name)
            .join(BizMaster, BizMaster.id == PlanOccurrenceCache.biz_master_id)
            .where(
                BizMaster.client_code == client_code,
                PlanOccurrenceCache.period.op("&&")(func.tstzrange(start, end, "[)")),
            )
            .order_by(PlanOccurrenceCache.starts_at, BizMaster.business_code, PlanOccurrenceCache.instance_id)
        )
        return [(row[0], row[1], row[2]) for row in db.execute(stmt)]

    def next_starts(self, db: Session, *, template_ids: Sequence[int], after: datetime) -> dict[int, datetime]:
        """テンプレートごとの、after 以降に開始する最初の有効な予定の開始日時（無いテンプレートは含めない）"""
        if not template_ids:
            return {}
        stmt = (
            select(PlanOccurrenceCache.template_id, func.min(PlanOccurrenceCache.starts_at))
            .where(PlanOccurrenceCache.template_id.in_(template_ids), PlanOccurrenceCache.starts_at >= after)
            .group_by(PlanOccurrenceCache.template_id)
        )
        return {row[0]: row[1] for row in db.execute(stmt)}

    def query_starting_in(
        self, db: Session, *, start: datetime, end: datetime
    ) -> list[tuple[PlanOccurrenceCache, PlanInstance, BizMaster]]:
        """
        全顧客の、範囲内に開始する予定を返します（発生枠の日付・算出元のためインスタンス、業務付き）。
        論理削除された業務は全局scopeで除外されます。
        """
        stmt = (
            select(PlanOccurrenceCache, PlanInstance, BizMaster)
            .join(PlanInstance, PlanInstance.id == PlanOccurrenceCache.instance_id)
            .join(BizMaster, BizMaster.id == PlanOccurrenceCache.biz_master_id)
            .where(
                PlanOccurrenceCache.period.op("&&")(func.tstzrange(start, end, "[)")),
                PlanOccurrenceCache.starts_at >= start,
                PlanOccurrenceCache.starts_at < end,
            )
            .order_by(PlanOccurrenceCache.starts_at, PlanOccurrenceCache.instance_id)
        )
        return [(row[0], row[1], row[2]) for row in db.execute(stmt)]


# ==========================================
#  インスタンス化とエクスポート
# ==========================================
business_calendar_repo = BusinessCalendarRepository(BusinessCalendar)
reference_sheet_repo = ReferenceSheetRepository(ReferenceSheet)
plan_template_repo = PlanTemplateRepository(PlanTemplate)
plan_instance_repo = PlanInstanceRepository(PlanInstance)
plan_instance_void_repo = PlanInstanceVoidRepository()
plan_slot_release_repo = PlanSlotReleaseRepository()
plan_cache_repo = PlanOccurrenceCacheRepository()
