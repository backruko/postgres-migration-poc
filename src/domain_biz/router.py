from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from src.infrastructure.database import get_db

#  必要なスキーマを直接インポート
from src.domain_biz.schemas import BizMasterCreate, BizMasterResponse, BizMasterUpdate
#  実行する関数も直接インポート
from src.domain_biz.CRUD import create_biz_master, update_biz_master

router = APIRouter(prefix="/biz", tags=["biz"])

@router.post("/biz-masters/", response_model=BizMasterResponse)
def create_biz(biz: BizMasterCreate, db: Session = Depends(get_db)):
    return create_biz_master(db=db, biz=biz)

@router.patch("/{biz_id}", response_model=BizMasterResponse)
def update_biz(biz_id: int, biz_update: BizMasterUpdate, db: Session = Depends(get_db)):
    # crud.pyの関数を呼び出す
    updated_biz = update_biz_master(db, biz_id=biz_id, biz_update=biz_update)
    
    if not updated_biz:
        raise HTTPException(status_code=404, detail="データが見つかりません")
        
    return updated_biz