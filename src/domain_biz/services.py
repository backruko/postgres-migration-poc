from sqlalchemy.orm import Session
from sqlalchemy import select

from src.infrastructure.repositories.BizMaster import biz_master_repo
from src.domain_biz.models import BizMaster
from src.domain_biz.schemas import BizMasterCreate, BizMasterUpdate

def create_biz(db: Session, biz_in: BizMasterCreate) -> BizMaster:
    return biz_master_repo.create(db, obj_in=biz_in)

def get_biz(db: Session, biz_id: int) -> BizMaster | None:
    return biz_master_repo.get(db, id=biz_id)

def get_bizes(db: Session, skip: int = 0, limit: int = 100) -> list[BizMaster]:
    return biz_master_repo.get_multi(db, skip=skip, limit=limit)

def update_biz(db: Session, biz_id: int, biz_in: BizMasterUpdate) -> BizMaster | None:
        db_obj = biz_master_repo.get(db, id=biz_id)
        if not db_obj:
            return None
        return biz_master_repo.update(db, db_obj=db_obj, obj_in=biz_in)

def delete_biz(db: Session, biz_id: int) -> BizMaster | None:
    return biz_master_repo.remove(db, id=biz_id)

# stmt = (
#     select(SOPVersion)
#     .where(SOPVersion.biz_master_id == 1, SOPVersion.status == "active")
#     .order_by(SOPVersion.version_number.desc())  # 整数なので確実に最新が上にくる
#     .limit(1)
# )
# latest_version = db.scalars(stmt).first()

# print(latest_version.version_tag)  # 画面には綺麗に "v2.1.0" と表示できる