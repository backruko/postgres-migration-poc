"""
SOP バージョンの影響（M2.3）: ある日の 0:00（日本時間）から業務の版を切り替えると、どのオーダーが新しい版を使うか。

版の決まり方（4.6）: 指定（pin）された版 ?? 予定日時に有効な版。リリースで固定され、その後は変わらない。
したがって、その日以降に予定されているオーダーは次の 3 つに分かれる（取消・完了は対象外）:
- will_switch: 未リリース・指定なし → 新しい版を使う（承認前に見せる「影響」の本体）
- pinned:      未リリース・指定あり → 指定した版のまま
- released:    リリース済み（作業中・保留を含む） → リリースで固定した版のまま
承認の画面（M2.6）が、承認する前にこの一覧を見せる。
"""
from datetime import date, datetime, time

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased
from sqlalchemy.sql.elements import ColumnElement

from src.core.exceptions import NotFoundError
from src.domain_biz.models import BizMaster, SOPVersion
from src.domain_ope.models import OpeOrder, OrderStatus
from src.domain_ope.schemas import SopImpactGroup, SopImpactOrder, SopImpactResponse
from src.domain_ope.steps import LOCAL_TIMEZONE
from src.domain_plan.models import PlanTemplate

DEFAULT_LIMIT = 50


def sop_impact(db: Session, biz_id: int, effective_date: date, limit: int = DEFAULT_LIMIT) -> SopImpactResponse:
    if db.get(BizMaster, biz_id) is None:
        raise NotFoundError(f"BizMaster(id={biz_id}) not found.")
    effective_at = datetime.combine(effective_date, time(0, 0), tzinfo=LOCAL_TIMEZONE)

    base = (
        OpeOrder.biz_master_id == biz_id,
        OpeOrder.scheduled_start >= effective_at,
        OpeOrder.status.not_in((OrderStatus.FINISHED, OrderStatus.CANCELED)),
    )
    unreleased = OpeOrder.released_at.is_(None)
    groups: dict[str, tuple[ColumnElement[bool], ...]] = {
        "will_switch": (*base, unreleased, OpeOrder.pinned_sop_version_id.is_(None)),
        "pinned": (*base, unreleased, OpeOrder.pinned_sop_version_id.is_not(None)),
        "released": (*base, OpeOrder.released_at.is_not(None)),
    }
    return SopImpactResponse(
        biz_id=biz_id, effective_date=effective_date, effective_at=effective_at,
        **{name: _group(db, conditions, limit) for name, conditions in groups.items()},
    )


def _group(db: Session, conditions: tuple[ColumnElement[bool], ...], limit: int) -> SopImpactGroup:
    total = int(db.scalar(select(func.count(OpeOrder.id)).where(*conditions)) or 0)
    pinned = aliased(SOPVersion)
    frozen = aliased(SOPVersion)
    stmt = (
        select(OpeOrder, PlanTemplate.name, pinned.version_tag, frozen.version_tag)
        .join(PlanTemplate, PlanTemplate.id == OpeOrder.template_id)
        .outerjoin(pinned, pinned.id == OpeOrder.pinned_sop_version_id)
        .outerjoin(frozen, frozen.id == OpeOrder.frozen_sop_version_id)
        .where(*conditions)
        .order_by(OpeOrder.scheduled_start, OpeOrder.id)
        .limit(limit)
        .execution_options(include_deleted=True)  # 削除されたテンプレートのオーダーも名前を出す
    )
    orders = [
        SopImpactOrder(
            id=order.id, template_id=order.template_id, template_name=name, scheduled_start=order.scheduled_start,
            status=order.status, pinned_sop_version_tag=pinned_tag, frozen_sop_version_tag=frozen_tag,
        )
        for order, name, pinned_tag, frozen_tag in db.execute(stmt)
    ]
    return SopImpactGroup(total=total, orders=orders)
