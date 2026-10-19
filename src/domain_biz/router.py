from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from src.infrastructure.database import get_db
from src.domain_biz.services import create_biz, get_biz, get_bizes, update_biz, delete_biz, restore_biz
from src.domain_biz.schemas import BizMasterCreate, BizMasterUpdate, BizMasterResponse 


router = APIRouter(prefix="/biz", tags=["biz"])


@router.post("/", response_model=BizMasterResponse, status_code=status.HTTP_201_CREATED)
def create_biz_endpoint(biz_in: BizMasterCreate, db: Session = Depends(get_db)):
    return create_biz(db=db, biz_in=biz_in)

@router.get("/{biz_id}", response_model=BizMasterResponse)
def get_biz_endpoint(biz_id: int, db: Session = Depends(get_db)):
    return get_biz(db=db, biz_id=biz_id)

@router.get("/", response_model=list[BizMasterResponse])
def get_bizes_endpoint(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    """
    BizMaster レコードを一覧取得します（ペイジング対応）。
    """
    return get_bizes(db=db, skip=skip, limit=limit)

@router.patch("/{biz_id}", response_model=BizMasterResponse)
def update_biz_endpoint(biz_id: int, biz_in: BizMasterUpdate, db: Session = Depends(get_db)):
    return update_biz(db=db, biz_id=biz_id, biz_in=biz_in)

@router.delete("/{biz_id}", response_model=BizMasterResponse)
def delete_biz_endpoint(biz_id: int, db: Session = Depends(get_db)):
    return delete_biz(db=db, biz_id=biz_id)

@router.post("/{biz_id}/restore", response_model=BizMasterResponse)
def restore_biz_endpoint(biz_id: int, db: Session = Depends(get_db)):
    """
    論理削除された BizMaster レコードを復元します。
    """
    return restore_biz(db=db, biz_id=biz_id)