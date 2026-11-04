from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from src.infrastructure.repositories.base import SoftCRUDBase
from src.domain_biz.models import BizDependency, BizMaster
from src.domain_biz.schemas import BizMasterCreate, BizMasterUpdate

class BizMasterRepository(SoftCRUDBase[BizMaster, BizMasterCreate, BizMasterUpdate]):
    pass



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

    def get_pair(self, db: Session, *, upstream_biz_id: int, downstream_biz_id: int) -> BizDependency | None:
        stmt = select(BizDependency).where(
            BizDependency.upstream_biz_id == upstream_biz_id, BizDependency.downstream_biz_id == downstream_biz_id
        )
        return db.scalars(stmt).first()

    def get_edges(self, db: Session) -> list[tuple[int, int]]:
        """すべての定義の (上流, 下流)（循環の確認用）"""
        return [(row[0], row[1]) for row in db.execute(select(BizDependency.upstream_biz_id, BizDependency.downstream_biz_id))]


biz_master_repo = BizMasterRepository(BizMaster)
biz_dependency_repo = BizDependencyRepository()