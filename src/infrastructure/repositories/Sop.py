# src/infrastructure/repositories/sop.py
from datetime import datetime

from sqlalchemy.orm import Session
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from src.infrastructure.repositories.base import CRUDBase  
from src.domain_biz.models import SOPVersion, SOPStepTemplate, SOPStatus
from src.domain_biz.schemas import (
    SOPVersionCreate, SOPVersionUpdate,
    SOPStepTemplateCreate, SOPStepTemplateUpdate
)
from src.core.exceptions import DuplicatedRecordError  # 自作例外のインポート


# ==========================================
#  SOPVersion Repository
# ==========================================
class SOPVersionRepository(CRUDBase[SOPVersion, SOPVersionCreate, SOPVersionUpdate]):
    
    def create_with_biz(self, db: Session, *, obj_in: SOPVersionCreate, biz_master_id: int) -> SOPVersion:
        """親の biz_master_id を外から注入して作成するメソッド"""
        obj_in_data = obj_in.model_dump()
        obj_in_data["biz_master_id"] = biz_master_id
        
        #  初期作成時は、自動的に内部連番(version_number)を割り当てるため、
        # Service側から連番が渡されていない場合は、ここで最大値+1を計算して安全に流し込むことも可能です。
        db_obj = self.model(**obj_in_data)
        db.add(db_obj)
        db.commit()
        db.refresh(db_obj)
        return db_obj

    def get_effective_at(self, db: Session, *, biz_master_id: int, at: datetime) -> SOPVersion | None:
        """
        指定日時に有効な承認済みバージョン（草稿以外で effective_at ≤ at < expired_at。expired_at が無ければ無期限）。
        オーダーの予定日時に使うバージョンの解決に使います（現在日時ではなく、予定日時で判定する）。
        """
        stmt = (
            select(SOPVersion)
            .where(
                SOPVersion.biz_master_id == biz_master_id,
                SOPVersion.status != SOPStatus.DRAFT,
                SOPVersion.effective_at <= at,
                or_(SOPVersion.expired_at.is_(None), SOPVersion.expired_at > at),
            )
            .order_by(SOPVersion.version_number.desc())
            .limit(1)
        )
        return db.scalars(stmt).first()


# ==========================================
#  SOPStepTemplate Repository
# ==========================================
class SOPStepTemplateRepository(CRUDBase[SOPStepTemplate, SOPStepTemplateCreate, SOPStepTemplateUpdate]):
    
    def create_with_version(self, db: Session, *, obj_in: SOPStepTemplateCreate, sop_version_id: int) -> SOPStepTemplate:
        """親の sop_version_id を外から注入して作成するメソッド"""
        obj_in_data = obj_in.model_dump()
        obj_in_data["sop_version_id"] = sop_version_id
        
        db_obj = self.model(**obj_in_data)
        db.add(db_obj)
        db.commit()
        db.refresh(db_obj)
        return db_obj


# ==========================================
#  インスタンス化とエクスポート
# ==========================================
sop_version_repo = SOPVersionRepository(SOPVersion)
sop_step_repo = SOPStepTemplateRepository(SOPStepTemplate)