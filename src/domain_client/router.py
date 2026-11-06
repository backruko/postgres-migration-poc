from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from src.core.schemas.error import AUTH_ERRORS, COMMON_ERRORS
from src.infrastructure.database import get_db
from src.domain_staff.auth import require_roles
from src.domain_staff.models import RoleCode

#  必要なクラスと関数を直接インポートする
from src.domain_client.schemas import ClientCreate, ClientResponse, ClientUpdate
from src.domain_client.services import activate_client, create_client, deactivate_client, get_clients, update_client

# 参照は制限なし。登録・編集・無効 / 有効は業務管理者（admin）のみ。顧客は削除しない（M1.1）
router = APIRouter(prefix="/clients", tags=["Clients"])


@router.post("/", response_model=ClientResponse, status_code=status.HTTP_201_CREATED,
            responses={**COMMON_ERRORS, **AUTH_ERRORS}
)
@require_roles([RoleCode.ADMIN])
def create_client_endpoint(client_in: ClientCreate, db: Session = Depends(get_db)):
    return create_client(db=db, client_in=client_in)

@router.get("/", response_model=list[ClientResponse],
            responses={**COMMON_ERRORS}
)
def get_clients_endpoint(include_inactive: bool = False, db: Session = Depends(get_db)):
    """顧客の一覧（コード順）。無効にした顧客は include_inactive=true のときだけ含めます。"""
    return get_clients(db=db, include_inactive=include_inactive)

@router.patch("/{client_id}", response_model=ClientResponse,
            responses={**COMMON_ERRORS, **AUTH_ERRORS}
)
@require_roles([RoleCode.ADMIN])
def update_client_endpoint(client_id: int, client_in: ClientUpdate, db: Session = Depends(get_db)):
    """顧客名を変えます（顧客コードは変えられません）。"""
    return update_client(db=db, client_id=client_id, client_in=client_in)

@router.post("/{client_id}/deactivate", response_model=ClientResponse,
            responses={**COMMON_ERRORS, **AUTH_ERRORS}
)
@require_roles([RoleCode.ADMIN])
def deactivate_client_endpoint(client_id: int, db: Session = Depends(get_db)):
    """顧客を無効にします（有効な業務が残っている間は 400）。一覧・選択肢から消え、新しい業務・シートは作れません。"""
    return deactivate_client(db=db, client_id=client_id)

@router.post("/{client_id}/activate", response_model=ClientResponse,
            responses={**COMMON_ERRORS, **AUTH_ERRORS}
)
@require_roles([RoleCode.ADMIN])
def activate_client_endpoint(client_id: int, db: Session = Depends(get_db)):
    """無効にした顧客を、再び有効にします。"""
    return activate_client(db=db, client_id=client_id)
