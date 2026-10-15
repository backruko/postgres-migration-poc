# src/domain_biz/repository.py または crud.py
from sqlalchemy.orm import Session
from src.domain_biz.models import BizMaster
from src.domain_biz.schemas import BizMasterCreate, BizMasterUpdate

def create_biz_master(db: Session, biz: BizMasterCreate):
    db_biz = BizMaster(**biz.model_dump())
    db.add(db_biz)
    db.commit()
    db.refresh(db_biz)
    return db_biz

def get_biz_master_by_code(db: Session, business_code: str):
    return db.query(BizMaster).filter(
        BizMaster.business_code == business_code,
        BizMaster.deleted_at.is_(None) # 未削除のみ取得！
    ).first()

def update_biz_master(db: Session, biz_id: int, biz_update: BizMasterUpdate):
    # 1. まずDBから既存のデータを取ってくる
    db_biz = db.query(BizMaster).filter(BizMaster.id == biz_id).first()
    if not db_biz:
        return None

    # 2. クライアントから送られてきたデータ（Noneじゃないもの）だけを上書き
    update_data = biz_update.model_dump(exclude_unset=True)# 送られていない項目は無視！
    for key, value in update_data.items():
        setattr(db_biz, key, value)

    # 3. 保存
    db.commit()
    db.refresh(db_biz)
    return db_biz