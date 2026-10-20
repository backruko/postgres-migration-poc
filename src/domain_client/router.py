from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from src.core.schemas.error import ErrorResponse, COMMON_ERRORS
from src.core.error_codes import ErrorCode
from src.infrastructure.database import get_db

#  必要なクラスと関数を直接インポートする
from src.domain_client.schemas import ClientCreate, ClientResponse, ClientUpdate
from src.domain_client.services import create_client, get_clients, update_client, delete_client

router = APIRouter(prefix="/clients", tags=["Clients"])


@router.post("/", response_model=ClientResponse, 
            responses={**COMMON_ERRORS}
)
def create_client_endpoint(client_in: ClientCreate, db: Session = Depends(get_db)):
    return create_client(db=db, client_in=client_in)

@router.get("/", response_model=list[ClientResponse], 
            responses={**COMMON_ERRORS}
)
def get_clients_endpoint(db: Session = Depends(get_db)):
    return get_clients(db=db)

@router.patch("/{client_id}", response_model=ClientResponse, 
            responses={**COMMON_ERRORS}
)
def update_client_endpoint(client_id: int, client_in: ClientUpdate, db: Session = Depends(get_db)):   
    return update_client(db=db, client_id=client_id, client_in=client_in)

@router.delete("/{client_id}", response_model=ClientResponse, 
            responses={**COMMON_ERRORS}      
)
def delete_client_endpoint(client_id: int, db: Session = Depends(get_db)):
    return delete_client(db=db, client_id=client_id)