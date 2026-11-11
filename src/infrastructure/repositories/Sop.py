# src/infrastructure/repositories/sop.py
from datetime import datetime

from sqlalchemy.orm import Session
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from src.infrastructure.repositories.base import CRUDBase  
from typing import Any

from sqlalchemy import func

from src.domain_biz.models import BizMaster, SOPVersion, SOPVersionEvent, SOPVersionEventType, SOPStepTemplate, SOPStatus
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

    def get_approved(self, db: Session, *, biz_master_id: int) -> list[SOPVersion]:
        """業務の承認済み（草稿以外）のバージョン、新しい順（オーダーに指定できるもの）"""
        stmt = (
            select(SOPVersion)
            .where(SOPVersion.biz_master_id == biz_master_id, SOPVersion.status != SOPStatus.DRAFT)
            .order_by(SOPVersion.version_number.desc())
        )
        return list(db.scalars(stmt).all())


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
#  SOPVersionEvent Repository（版の記録。追記のみ）と承認待ち
# ==========================================
class SOPVersionEventRepository:

    def add(
        self, db: Session, *, version: SOPVersion, event_type: SOPVersionEventType, actor_id: int | None,
        note: str | None = None, detail: dict[str, Any] | None = None,
    ) -> SOPVersionEvent:
        """記録を足す（書き出しは呼び出し側のコミットで）"""
        event = SOPVersionEvent(
            sop_version_id=version.id, event_type=event_type, actor_id=actor_id, note=note, detail=detail,
        )
        db.add(event)
        return event

    def list_for_version(self, db: Session, *, version_id: int) -> list[SOPVersionEvent]:
        """版の記録（古い順）"""
        stmt = select(SOPVersionEvent).where(SOPVersionEvent.sop_version_id == version_id)
        return list(db.scalars(stmt.order_by(SOPVersionEvent.occurred_at, SOPVersionEvent.id)).all())

    def list_submitted(self, db: Session, *, biz_master_id: int | None = None, client_code: str | None = None) -> list[SOPVersion]:
        """承認を依頼された草稿（依頼の古い順。削除された業務のものは除く）"""
        stmt = (
            select(SOPVersion).join(BizMaster, BizMaster.id == SOPVersion.biz_master_id)
            .where(SOPVersion.submitted_at.is_not(None))
        )
        if biz_master_id is not None:
            stmt = stmt.where(SOPVersion.biz_master_id == biz_master_id)
        if client_code is not None:
            stmt = stmt.where(BizMaster.client_code == client_code)
        return list(db.scalars(stmt.order_by(SOPVersion.submitted_at, SOPVersion.id)).all())

    def count_submitted(self, db: Session) -> int:
        """承認を依頼された草稿の数（削除された業務のものは除く。業務管理者のメニューの件数）"""
        stmt = (
            select(func.count()).select_from(SOPVersion).join(BizMaster, BizMaster.id == SOPVersion.biz_master_id)
            .where(SOPVersion.submitted_at.is_not(None))
        )
        return db.scalar(stmt) or 0


# ==========================================
#  インスタンス化とエクスポート
# ==========================================
sop_version_repo = SOPVersionRepository(SOPVersion)
sop_step_repo = SOPStepTemplateRepository(SOPStepTemplate)
sop_version_event_repo = SOPVersionEventRepository()