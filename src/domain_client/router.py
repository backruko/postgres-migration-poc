from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from src.infrastructure.database import get_db  # ★DBセッションを取得する関数(後述)
from src.domain_client import schemas, services

router = APIRouter(prefix="/clients", tags=["Clients"])

@router.post("/", response_model=schemas.ClientResponse)
def create_client_endpoint(client_in: schemas.ClientCreate, db: Session = Depends(get_db)):
    return services.create_client(db, client_in)

@router.get("/", response_model=list[schemas.ClientResponse])
def get_clients_endpoint(db: Session = Depends(get_db)):
    return services.get_clients(db)