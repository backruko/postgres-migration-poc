from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List

from src.infrastructure.database import get_db
from src.domain_biz.services import create_biz, get_biz, get_bizes, update_biz, delete_biz
from src.domain_biz.schemas import BizMasterCreate, BizMasterUpdate, BizMasterResponse 


router = APIRouter(prefix="/biz", tags=["biz"])

@router.post("/", response_model=BizMasterResponse, status_code=status.HTTP_201_CREATED)
def create_biz_endpoint(biz_in: BizMasterCreate, db: Session = Depends(get_db)):
    return create_biz(db, biz_in=biz_in)

@router.patch("/{biz_id}", response_model=BizMasterResponse)
def update_biz_endpoint(biz_id: int, biz_update: BizMasterUpdate, db: Session = Depends(get_db)):
    # crud.pyの関数を呼び出す
    updated_biz = update_biz(db, biz_id=biz_id, biz_update=biz_update)
    
    if not updated_biz:
        raise HTTPException(status_code=404, detail="データが見つかりません")
        
    return updated_biz