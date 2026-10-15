from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from src.infrastructure.database import get_db

#  必要なクラスと関数を直接インポートする
from src.domain_client.schemas import ClientCreate, ClientResponse, ClientUpdate
from src.domain_client.services import create_client, get_clients, update_client, delete_client

router = APIRouter(prefix="/clients", tags=["Clients"])

#  schemas. なしで直接 ClientResponse を指定
@router.post("/", response_model=ClientResponse)
def create_client_endpoint(client_in: ClientCreate, db: Session = Depends(get_db)):
    #  services. なしで直接関数を呼び出す
    return create_client(db=db, client_in=client_in)

#  schemas. なしで直接 list[ClientResponse] を指定
@router.get("/", response_model=list[ClientResponse])
def get_clients_endpoint(db: Session = Depends(get_db)):
    #  services. なしで直接関数を呼び出す
    return get_clients(db=db)

@router.patch("/{client_id}", response_model=ClientResponse)
def update_client_endpoint(client_id: int, client_in: ClientUpdate, db: Session = Depends(get_db)):

    updated_client = update_client(db=db, client_id=client_id, client_in=client_in)
    
    if not updated_client:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail="Client not found"
        )
        
    return updated_client

@router.delete("/{client_id}", response_model=ClientResponse)
def delete_client_endpoint(client_id: int, db: Session = Depends(get_db)):

    deleted_client = delete_client(db=db, client_id=client_id)
    
    if not deleted_client:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail="Client not found"
        )
        
    return deleted_client