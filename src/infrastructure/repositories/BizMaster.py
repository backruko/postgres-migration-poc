from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from src.infrastructure.repositories.base import SoftCRUDBase
from src.domain_biz.models import BizDependency, BizMaster
from src.domain_biz.schemas import BizMasterCreate, BizMasterUpdate

class BizMasterRepository(SoftCRUDBase[BizMaster, BizMasterCreate, BizMasterUpdate]):
    def get_list(
        self, db: Session, *, client_code: str | None = None, include_deleted: bool = False, skip: int = 0, limit: int = 100,
    ) -> list[BizMaster]:
        """業務の一覧（業務コード順）。顧客で絞れる。include_deleted=True で論理削除された業務も含める"""
        stmt = select(BizMaster).order_by(BizMaster.business_code, BizMaster.id).offset(skip).limit(limit)
        if client_code is not None:
            stmt = stmt.where(BizMaster.client_code == client_code)
        if include_deleted:
            stmt = stmt.execution_options(include_deleted=True)
        return list(db.scalars(stmt).all())

    def max_business_seq(self, db: Session, *, client_code: str) -> int:
        """顧客の業務連番の最大値（論理削除された業務も含む。業務コードは使い回さない）。業務が無ければ 0"""
        stmt = (
            select(func.coalesce(func.max(BizMaster.business_seq), 0))
            .where(BizMaster.client_code == client_code)
            .execution_options(include_deleted=True)
        )
        return int(db.scalar(stmt) or 0)

    def count_alive_of_client(self, db: Session, *, client_code: str) -> int:
        """顧客の（論理削除されていない）業務の数"""
        stmt = select(func.count(BizMaster.id)).where(BizMaster.client_code == client_code, BizMaster.deleted_at.is_(None))
        return int(db.scalar(stmt) or 0)



class BizDependencyRepository:
    """業務間の依存関係の定義。commit は Service 層で行います。"""

    def get_for_biz(self, db: Session, *, biz_id: int) -> list[BizDependency]:
        """その業務が上流または下流になっている定義"""
        stmt = (
            select(BizDependency)
            .where(or_(BizDependency.upstream_biz_id == biz_id, BizDependency.downstream_biz_id == biz_id))
            .order_by(BizDependency.id)
        )
        return list(db.scalars(stmt).all())

    def get_for_client(self, db: Session, *, client_code: str) -> list[BizDependency]:
        """顧客の業務どうしの定義（定義は同じ顧客の業務のみを結ぶ。論理削除された業務のものも含む）"""
        stmt = (
            select(BizDependency)
            .join(BizMaster, BizMaster.id == BizDependency.downstream_biz_id)
            .where(BizMaster.client_code == client_code)
            .order_by(BizDependency.id)
            .execution_options(include_deleted=True)
        )
        return list(db.scalars(stmt).all())

    def get_pair(self, db: Session, *, upstream_biz_id: int, downstream_biz_id: int) -> BizDependency | None:
        stmt = select(BizDependency).where(
            BizDependency.upstream_biz_id == upstream_biz_id, BizDependency.downstream_biz_id == downstream_biz_id
        )
        return db.scalars(stmt).first()

    def get_by_downstream(self, db: Session, *, biz_id: int) -> list[BizDependency]:
        """その業務が下流（待つ側）になっている定義"""
        stmt = select(BizDependency).where(BizDependency.downstream_biz_id == biz_id).order_by(BizDependency.id)
        return list(db.scalars(stmt).all())

    def get_by_upstream(self, db: Session, *, biz_id: int) -> list[BizDependency]:
        """その業務が上流（待たれる側）になっている定義"""
        stmt = select(BizDependency).where(BizDependency.upstream_biz_id == biz_id).order_by(BizDependency.id)
        return list(db.scalars(stmt).all())

    def get_edges(self, db: Session) -> list[tuple[int, int]]:
        """すべての定義の (上流, 下流)（循環の確認用）"""
        return [(row[0], row[1]) for row in db.execute(select(BizDependency.upstream_biz_id, BizDependency.downstream_biz_id))]


biz_master_repo = BizMasterRepository(BizMaster)
biz_dependency_repo = BizDependencyRepository()