# src/infrastructure/repositories/Ope.py
"""domain_ope 用のリポジトリ群。commit は呼び出し側（Service 層・plan のコミット）で行います。"""
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select
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
        if occurred_at is not None:
            event.occurred_at = occurred_at
        db.add(event)
        return event


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

    def get_open_orders_of(self, db: Session, *, biz_id: int) -> list[OpeOrder]:
        """業務の開始前のオーダー（pending / ready / それらから保留）"""
        not_started = (OrderStatus.PENDING, OrderStatus.READY)
        stmt = select(OpeOrder).where(
            OpeOrder.biz_master_id == biz_id,
            OpeOrder.status.in_(not_started)
            | ((OpeOrder.status == OrderStatus.ON_HOLD) & OpeOrder.status_before_hold.in_(not_started)),
        )
        return list(db.scalars(stmt.order_by(OpeOrder.id)).all())

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


# ==========================================
#  インスタンス化とエクスポート
# ==========================================
ope_order_repo = OpeOrderRepository()
ope_step_repo = OpeStepRepository()
ope_dependency_repo = OpeDependencyRepository()
