# src/infrastructure/repositories/sop.py
from sqlalchemy.orm import Session
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.infrastructure.repositories.base import CRUDBase  
from src.domain_biz.models import SOPVersion, SOPStepTemplate, SOPStatus
from src.domain_biz.schemas import (
    SOPVersionCreate, SOPVersionUpdate,
    SOPStepTemplateCreate, SOPStepTemplateUpdate
)
from src.infrastructure.exceptions import DuplicatedRecordError  # 自作例外のインポート


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

    def restore(self, db: Session, *, id: int) -> SOPVersion | None:
        """
        IDを指定して、論理削除されたレコードを復元します。
        """
        #  グローバルスコープに阻まれて削除済みデータが取得できないのを防ぐため、
        # .execution_options(include_deleted=True) を付与して一時的にスコープを「解除」します。
        stmt = (
            select(self.model)
            .where(self.model.id == id)
            .execution_options(include_deleted=True)
        )
        obj = db.scalars(stmt).first()

        # すでにスコープを解除して全件対象になっているため、
        # まだ削除されていない有効なデータを誤って復元しないようチェックします。
        # ※ もしSOPVersion自身が論理削除をせず、親の論理削除のみに連動する場合は、
        # 親（BizMaster）の状態をチェックするロジックになります。
        if not obj or not getattr(obj, "is_deleted", False):
            return None
        
        # 復元処理の実行（Mixin等に定義されているrestore、または直接フラグを戻す）
        if hasattr(obj, "restore"):
            obj.restore()
        else:
            obj.is_deleted = False
        
        try:
            db.commit()
            db.refresh(obj)
            return obj
        except IntegrityError:
            db.rollback()
            raise DuplicatedRecordError(
                message=f"Restore failed: Unique constraint conflict. The original value is already taken by another active record."
            )


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