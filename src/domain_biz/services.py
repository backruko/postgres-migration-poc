from sqlalchemy.orm import Session
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.infrastructure.repositories.BizMaster import biz_master_repo
from src.domain_biz.models import BizMaster
from src.domain_biz.schemas import BizMasterCreate, BizMasterUpdate
from src.infrastructure.exceptions import NotFoundError, DuplicatedRecordError


def create_biz(db: Session, biz_in: BizMasterCreate) -> BizMaster:
    try:
        return biz_master_repo.create(db, obj_in=biz_in)
    except IntegrityError:
        # 重複エラーが発生した場合、データベースのロールバックはリポジトリまたは共通処理に任せる前提
        raise DuplicatedRecordError(f"BizMaster with unique key already exists.")

def get_biz(db: Session, biz_id: int) -> BizMaster:  
    db_obj = biz_master_repo.get(db, id=biz_id)
    if not db_obj:
        raise NotFoundError(f"BizMaster(id={biz_id}) not found.") 
    return db_obj

def get_bizes(db: Session, skip: int = 0, limit: int = 100) -> list[BizMaster]:
    return biz_master_repo.get_multi(db, skip=skip, limit=limit)

def update_biz(db: Session, biz_id: int, biz_in: BizMasterUpdate) -> BizMaster:
    db_obj = biz_master_repo.get(db, id=biz_id)
    if not db_obj:
        raise NotFoundError(f"BizMaster(id={biz_id}) not found for update.")  
        
    try:
        return biz_master_repo.update(db, db_obj=db_obj, obj_in=biz_in)
    except IntegrityError:
        raise DuplicatedRecordError(f"Update failed: Unique constraint violation.")

def delete_biz(db: Session, biz_id: int) -> BizMaster:
    deleted_obj = biz_master_repo.remove(db, id=biz_id)
    if not deleted_obj:
        # 見つからない(None)なら、ビジネスエラーに変換して raise する役割
        raise NotFoundError(f"BizMaster(id={biz_id}) not found for deletion.")
    return deleted_obj

def restore_biz(db: Session, biz_id: int) -> BizMaster:
    """
    論理削除された BizMaster レコードを復元します。
    """
    # 1. Repositoryのrestoreを呼び出す
    # （内部でスコープ解除、復元、重複チェック、DuplicatedRecordErrorの送出まで完結しています）
    restored_obj = biz_master_repo.restore(db, id=biz_id)
    
    # 2. そもそもデータが存在しない、または既に有効なデータだった場合は None が返ってくるので、
    # 適切なビジネスエラー（NotFoundError）に変換して raise します
    if not restored_obj:
        raise NotFoundError(f"BizMaster(id={biz_id}) not found or already active.")
        
    return restored_obj

# stmt = (
#     select(SOPVersion)
#     .where(SOPVersion.biz_master_id == 1, SOPVersion.status == "active")
#     .order_by(SOPVersion.version_number.desc())  # 整数なので確実に最新が上にくる
#     .limit(1)
# )
# latest_version = db.scalars(stmt).first()

# print(latest_version.version_tag)  # 画面には綺麗に "v2.1.0" と表示できる