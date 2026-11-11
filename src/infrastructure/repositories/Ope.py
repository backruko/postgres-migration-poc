# src/infrastructure/repositories/Ope.py
"""domain_ope 用のリポジトリ群。commit は呼び出し側（Service 層・plan のコミット）で行います。"""
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from src.domain_biz.models import BizMaster, DependencyMatchRule, ReleaseMode
from src.domain_ope.models import (
    EventSource, OpeDependency, OpeOrder, OpeOrderEvent, OpeStep, OpeStepEvent, OrderEventType, OrderStatus,
    StepEventType, StepState,
)


# ==========================================
#  OpeOrder Repository
# ==========================================
class OpeOrderRepository:

    def get_active_for_slot(
        self,
        db: Session,
        *,
        template_id: int,
        occurrence_date: date,
        sheet_event_id: int | None,
        one_off_instance_id: int | None,
    ) -> OpeOrder | None:
        """発生枠の、取消されていないオーダー（1つの枠に最大1件）"""
        stmt = select(OpeOrder).where(OpeOrder.template_id == template_id, OpeOrder.status != OrderStatus.CANCELED)
        if sheet_event_id is not None:
            stmt = stmt.where(OpeOrder.source_sheet_event_id == sheet_event_id)
        elif one_off_instance_id is not None:
            stmt = stmt.where(OpeOrder.one_off_instance_id == one_off_instance_id)
        else:
            stmt = stmt.where(
                OpeOrder.occurrence_date == occurrence_date,
                OpeOrder.source_sheet_event_id.is_(None),
                OpeOrder.one_off_instance_id.is_(None),
            )
        return db.scalars(stmt.with_for_update()).first()

    def get_active_for_slots(
        self,
        db: Session,
        *,
        template_id: int,
        occurrence_dates: set[date],
        sheet_event_ids: set[int],
        one_off_instance_ids: set[int],
    ) -> list[OpeOrder]:
        """
        複数の発生枠の、取消されていないオーダー（get_active_for_slot と同じ規則を 1 回の問い合わせで。行ロック付き）。
        ロックは id の順に取る（同時に動く処理どうしで、取る順がそろう）。
        """
        conditions = []
        if sheet_event_ids:
            conditions.append(OpeOrder.source_sheet_event_id.in_(sheet_event_ids))
        if one_off_instance_ids:
            conditions.append(OpeOrder.one_off_instance_id.in_(one_off_instance_ids))
        if occurrence_dates:
            conditions.append(
                OpeOrder.occurrence_date.in_(occurrence_dates)
                & OpeOrder.source_sheet_event_id.is_(None)
                & OpeOrder.one_off_instance_id.is_(None)
            )
        if not conditions:
            return []
        stmt = (
            select(OpeOrder)
            .where(OpeOrder.template_id == template_id, OpeOrder.status != OrderStatus.CANCELED, or_(*conditions))
            .order_by(OpeOrder.id)
            .with_for_update()
        )
        return list(db.scalars(stmt))

    def get_active_by_instances(self, db: Session, *, instance_ids: list[int]) -> dict[int, OpeOrder]:
        """plan インスタンスごとの、取消されていないオーダー（ダッシュボードの一覧用。1回のクエリ）"""
        if not instance_ids:
            return {}
        stmt = select(OpeOrder).where(
            OpeOrder.current_instance_id.in_(instance_ids), OpeOrder.status != OrderStatus.CANCELED
        )
        return {o.current_instance_id: o for o in db.scalars(stmt)}

    def get_locked(self, db: Session, *, id: int) -> OpeOrder | None:
        """状態を変えるときは行ロックしてから読む（同時に操作されても状態の遷移が食い違わないよう）"""
        return db.scalars(select(OpeOrder).where(OpeOrder.id == id).with_for_update()).first()

    def get_pending_automatic(self, db: Session, *, starting_before: datetime) -> list[tuple[OpeOrder, BizMaster]]:
        """
        自動リリースの業務の、矛盾の無い pending オーダー（starting_before より前に開始するもの）を業務付きで返します。
        行ロックを取り、他の処理（手動リリース・別の実行）がロック中の行は飛ばします（SKIP LOCKED）。
        """
        stmt = (
            select(OpeOrder, BizMaster)
            .join(BizMaster, BizMaster.id == OpeOrder.biz_master_id)
            .where(
                BizMaster.release_mode == ReleaseMode.AUTOMATIC,
                OpeOrder.status == OrderStatus.PENDING,
                OpeOrder.conflict.is_(False),
                OpeOrder.scheduled_start < starting_before,
            )
            .order_by(OpeOrder.scheduled_start, OpeOrder.id)
            .with_for_update(of=OpeOrder, skip_locked=True)
        )
        return [(row[0], row[1]) for row in db.execute(stmt)]

    def get_released_unstarted(self, db: Session, *, template_id: int) -> list[OpeOrder]:
        """
        テンプレートの、リリース済みで未開始のオーダー（ready、または ready から保留したもの）を行ロックして返します。
        ステップの条件の再確認用（予定が動いて、固定したステップと条件の結果が食い違っていないか）。
        """
        stmt = (
            select(OpeOrder)
            .where(
                OpeOrder.template_id == template_id,
                OpeOrder.released_at.is_not(None),
                (OpeOrder.status == OrderStatus.READY)
                | ((OpeOrder.status == OrderStatus.ON_HOLD) & (OpeOrder.status_before_hold == OrderStatus.READY)),
            )
            .order_by(OpeOrder.scheduled_start, OpeOrder.id)
            .with_for_update()
        )
        return list(db.scalars(stmt).all())

    def has_event(self, db: Session, *, order_id: int, event_type: OrderEventType) -> bool:
        stmt = select(OpeOrderEvent.id).where(OpeOrderEvent.order_id == order_id, OpeOrderEvent.event_type == event_type)
        return db.scalars(stmt.limit(1)).first() is not None

    def get_events(self, db: Session, *, order_id: int) -> list[OpeOrderEvent]:
        stmt = select(OpeOrderEvent).where(OpeOrderEvent.order_id == order_id).order_by(OpeOrderEvent.id)
        return list(db.scalars(stmt).all())

    def get_events_of_type(self, db: Session, *, order_ids: list[int], event_type: OrderEventType) -> list[OpeOrderEvent]:
        """複数のオーダーの、ある種類の記録（ダッシュボード用。1回のクエリ）"""
        if not order_ids:
            return []
        stmt = select(OpeOrderEvent).where(OpeOrderEvent.order_id.in_(order_ids), OpeOrderEvent.event_type == event_type)
        return list(db.scalars(stmt).all())

    def add_event(
        self,
        db: Session,
        *,
        order: OpeOrder,
        event_type: OrderEventType,
        source: EventSource,
        actor_staff_id: int | None,
        from_status: OrderStatus | None = None,
        to_status: OrderStatus | None = None,
        reason: str | None = None,
        detail: dict[str, Any] | None = None,
        occurred_at: datetime | None = None,
    ) -> OpeOrderEvent:
        event = OpeOrderEvent(
            order_id=order.id, event_type=event_type, source=source, actor_staff_id=actor_staff_id,
            from_status=from_status, to_status=to_status, reason=reason, detail=detail,
        )
        if to_status == OrderStatus.FINISHED:
            # 完了（受領）日時をオーダーにも保存する（SLA の正。完了の経路はすべてこの記録を通る）。記録と同じ時刻にそろえる
            occurred_at = occurred_at or datetime.now(timezone.utc)
            order.finished_at = occurred_at
        if occurred_at is not None:
            event.occurred_at = occurred_at
        db.add(event)
        return event

    def get_unreleased_of_template(self, db: Session, *, template_id: int) -> list[OpeOrder]:
        """テンプレートのリリース前のオーダー（未リリース・それから保留。取消済みは除く）。締切の写し直し用（FOR UPDATE）"""
        stmt = select(OpeOrder).where(
            OpeOrder.template_id == template_id, OpeOrder.released_at.is_(None), OpeOrder.status != OrderStatus.CANCELED,
        )
        return list(db.scalars(stmt.order_by(OpeOrder.id).with_for_update()).all())

    def get_needing_attention(self, db: Session, *, now: datetime, limit: int) -> list[OpeOrder]:
        """
        完了・取消以外で、締切を過ぎた（遅れ）か「まもなく」の時刻を過ぎたオーダー。日付によらず（昨日の遅れも残る）、締切の早い順。
        部分インデックス ix_ope_order_open_deadline / ix_ope_order_open_warn を使う。
        """
        stmt = (
            select(OpeOrder)
            .where(
                OpeOrder.status.not_in((OrderStatus.FINISHED, OrderStatus.CANCELED)),
                (OpeOrder.deadline_at <= now) | (OpeOrder.warn_at <= now),
            )
            .order_by(OpeOrder.deadline_at, OpeOrder.id)
            .limit(limit)
        )
        return list(db.scalars(stmt).all())

    def count_using_sop_version(self, db: Session, *, sop_version_id: int) -> int:
        """その SOP バージョンを指定（pin）している・リリースで固定したオーダーの数（取消は除く。承認の取消の確認用）"""
        stmt = select(func.count(OpeOrder.id)).where(
            (OpeOrder.pinned_sop_version_id == sop_version_id) | (OpeOrder.frozen_sop_version_id == sop_version_id),
            OpeOrder.status != OrderStatus.CANCELED,
        )
        return int(db.scalar(stmt) or 0)

    def count_unfinished_of_biz(self, db: Session, *, biz_id: int) -> int:
        """業務の、完了・取消以外のオーダーの数（業務の削除の確認用。過去の日付のものも数える）"""
        stmt = select(func.count(OpeOrder.id)).where(
            OpeOrder.biz_master_id == biz_id, OpeOrder.status.not_in((OrderStatus.FINISHED, OrderStatus.CANCELED))
        )
        return int(db.scalar(stmt) or 0)


# ==========================================
#  OpeStep Repository
# ==========================================
class OpeStepRepository:

    def get_active(self, db: Session, *, order_id: int) -> list[OpeStep]:
        """オーダーのステップ（作り直しで外れたものを除く。番号順）"""
        stmt = (
            select(OpeStep)
            .where(OpeStep.order_id == order_id, OpeStep.retired_at.is_(None))
            .order_by(OpeStep.step_num)
        )
        return list(db.scalars(stmt).all())

    def progress_by_orders(self, db: Session, *, order_ids: list[int]) -> dict[int, tuple[int, int]]:
        """オーダーごとの (ステップ数, 承認済みの数)（外れたステップを除く。ステップの無いオーダーは含めない。1回のクエリ）"""
        if not order_ids:
            return {}
        stmt = (
            select(
                OpeStep.order_id,
                func.count(OpeStep.id),
                func.count(OpeStep.id).filter(OpeStep.state == StepState.APPROVED),
            )
            .where(OpeStep.order_id.in_(order_ids), OpeStep.retired_at.is_(None))
            .group_by(OpeStep.order_id)
        )
        return {row[0]: (row[1], row[2]) for row in db.execute(stmt)}

    def get_events(self, db: Session, *, step_ids: list[int]) -> list[OpeStepEvent]:
        """ステップの記録（古い順）"""
        if not step_ids:
            return []
        stmt = select(OpeStepEvent).where(OpeStepEvent.step_id.in_(step_ids)).order_by(OpeStepEvent.id)
        return list(db.scalars(stmt).all())

    def latest_submitter(self, db: Session, *, step_id: int) -> int | None:
        """最後に提出した職員（四つの目の確認用。提出の記録が無ければ None）"""
        stmt = (
            select(OpeStepEvent.actor_staff_id)
            .where(OpeStepEvent.step_id == step_id, OpeStepEvent.to_state == StepState.SUBMITTED)
            .order_by(OpeStepEvent.id.desc())
            .limit(1)
        )
        return db.scalars(stmt).first()

    def add_event(
        self,
        db: Session,
        *,
        step: OpeStep,
        event_type: StepEventType,
        source: EventSource,
        actor_staff_id: int | None,
        from_state: StepState | None = None,
        to_state: StepState | None = None,
        reason: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> OpeStepEvent:
        event = OpeStepEvent(
            step_id=step.id, event_type=event_type, source=source, actor_staff_id=actor_staff_id,
            from_state=from_state, to_state=to_state, reason=reason, detail=detail,
        )
        db.add(event)
        return event


# ==========================================
#  OpeDependency Repository
# ==========================================
class OpeDependencyRepository:

    def get_active(self, db: Session, *, downstream_order_id: int, biz_dependency_id: int | None = None) -> list[OpeDependency]:
        """下流のオーダーの有効なつながり（定義を指定すればその定義のものだけ）"""
        stmt = select(OpeDependency).where(
            OpeDependency.downstream_order_id == downstream_order_id, OpeDependency.removed_at.is_(None)
        )
        if biz_dependency_id is not None:
            stmt = stmt.where(OpeDependency.biz_dependency_id == biz_dependency_id)
        return list(db.scalars(stmt.order_by(OpeDependency.id)).all())

    def get_active_by_upstream(self, db: Session, *, upstream_order_id: int) -> list[OpeDependency]:
        """このオーダーを上流として待っているつながり"""
        stmt = select(OpeDependency).where(
            OpeDependency.upstream_order_id == upstream_order_id, OpeDependency.removed_at.is_(None)
        )
        return list(db.scalars(stmt.order_by(OpeDependency.id)).all())

    def get_active_for_orders(self, db: Session, *, downstream_order_ids: list[int]) -> list[OpeDependency]:
        """複数の下流のオーダーの有効なつながり（ダッシュボード用。1回のクエリ）"""
        if not downstream_order_ids:
            return []
        stmt = select(OpeDependency).where(
            OpeDependency.downstream_order_id.in_(downstream_order_ids), OpeDependency.removed_at.is_(None)
        )
        return list(db.scalars(stmt).all())

    def get_active_by_definition(self, db: Session, *, biz_dependency_id: int) -> list[OpeDependency]:
        stmt = select(OpeDependency).where(
            OpeDependency.biz_dependency_id == biz_dependency_id, OpeDependency.removed_at.is_(None)
        )
        return list(db.scalars(stmt).all())

    def get_active_downstream_ids(self, db: Session, *, upstream_order_id: int) -> list[int]:
        """このオーダーを上流として待っている下流のオーダー"""
        stmt = select(OpeDependency.downstream_order_id).where(
            OpeDependency.upstream_order_id == upstream_order_id, OpeDependency.removed_at.is_(None)
        )
        return list(db.scalars(stmt).all())

    def get_open_orders_of(
        self, db: Session, *, biz_id: int, starting_from: datetime | None = None, starting_until: datetime | None = None,
    ) -> list[OpeOrder]:
        """業務の開始前のオーダー（pending / ready / それらから保留）。期間を指定すると、その間に開始するものだけ"""
        not_started = (OrderStatus.PENDING, OrderStatus.READY)
        stmt = select(OpeOrder).where(
            OpeOrder.biz_master_id == biz_id,
            OpeOrder.status.in_(not_started)
            | ((OpeOrder.status == OrderStatus.ON_HOLD) & OpeOrder.status_before_hold.in_(not_started)),
        )
        if starting_from is not None:
            stmt = stmt.where(OpeOrder.scheduled_start >= starting_from)
        if starting_until is not None:
            stmt = stmt.where(OpeOrder.scheduled_start <= starting_until)
        return list(db.scalars(stmt.order_by(OpeOrder.id)).all())

    def get_latest_starting_before(self, db: Session, *, biz_id: int, at: datetime, since: datetime) -> OpeOrder | None:
        """
        直前の上流: 業務のオーダーのうち、since ≤ 予定開始 ≤ at で最新のもの（取消済みも含む）。
        同じ時刻なら新しいオーダー（枠が戻って作り直されたものが、取消済みより優先される）。
        """
        stmt = (
            select(OpeOrder)
            .where(OpeOrder.biz_master_id == biz_id, OpeOrder.scheduled_start <= at, OpeOrder.scheduled_start >= since)
            .order_by(OpeOrder.scheduled_start.desc(), OpeOrder.id.desc())
            .limit(1)
        )
        return db.scalars(stmt).first()

    def get_orders_matching(
        self, db: Session, *, biz_id: int, rule: DependencyMatchRule, key: int | date,
    ) -> list[OpeOrder]:
        """業務のオーダーのうち、突き合わせのキー（シート日付 / ルール上の日付）が同じもの（取消済みも含む。古い順）"""
        stmt = select(OpeOrder).where(OpeOrder.biz_master_id == biz_id)
        if rule == DependencyMatchRule.SAME_SHEET_EVENT:
            stmt = stmt.where(OpeOrder.source_sheet_event_id == key)
        else:
            stmt = stmt.where(OpeOrder.occurrence_date == key)
        return list(db.scalars(stmt.order_by(OpeOrder.id)).all())

    # --- まとめて読む版（オーダーの追従で数百のオーダーを一度に直す: links.refresh_orders / resolve_many） ---
    def get_orders_matching_keys(
        self, db: Session, *, biz_id: int, rule: DependencyMatchRule, keys: set[int] | set[date] | set[int | date],
    ) -> list[OpeOrder]:
        """get_orders_matching を多くのキーでまとめて（取消済みも含む。古い順）"""
        if not keys:
            return []
        column = OpeOrder.source_sheet_event_id if rule == DependencyMatchRule.SAME_SHEET_EVENT else OpeOrder.occurrence_date
        stmt = select(OpeOrder).where(OpeOrder.biz_master_id == biz_id, column.in_(keys))
        return list(db.scalars(stmt.order_by(OpeOrder.id)).all())

    def get_orders_starting_between(self, db: Session, *, biz_id: int, since: datetime, until: datetime) -> list[OpeOrder]:
        """業務のオーダーのうち since ≤ 予定開始 ≤ until のもの（取消済みも含む。予定開始・id の順）。直前の上流をまとめて探す用"""
        stmt = select(OpeOrder).where(
            OpeOrder.biz_master_id == biz_id, OpeOrder.scheduled_start >= since, OpeOrder.scheduled_start <= until,
        )
        return list(db.scalars(stmt.order_by(OpeOrder.scheduled_start, OpeOrder.id)).all())

    def get_active_for_definition(
        self, db: Session, *, biz_dependency_id: int, downstream_order_ids: list[int],
    ) -> list[OpeDependency]:
        """定義の、複数の下流のオーダーの有効なつながり（古い順）"""
        if not downstream_order_ids:
            return []
        stmt = select(OpeDependency).where(
            OpeDependency.biz_dependency_id == biz_dependency_id,
            OpeDependency.downstream_order_id.in_(downstream_order_ids), OpeDependency.removed_at.is_(None),
        )
        return list(db.scalars(stmt.order_by(OpeDependency.id)).all())

    def get_active_downstream_orders(self, db: Session, *, upstream_order_ids: list[int]) -> list[OpeOrder]:
        """複数の上流のオーダーのどれかを待っている下流のオーダー（id の順）"""
        if not upstream_order_ids:
            return []
        waiting = select(OpeDependency.downstream_order_id).where(
            OpeDependency.upstream_order_id.in_(upstream_order_ids), OpeDependency.removed_at.is_(None),
        )
        return list(db.scalars(select(OpeOrder).where(OpeOrder.id.in_(waiting)).order_by(OpeOrder.id)).all())


# ==========================================
#  インスタンス化とエクスポート
# ==========================================
ope_order_repo = OpeOrderRepository()
ope_step_repo = OpeStepRepository()
ope_dependency_repo = OpeDependencyRepository()
