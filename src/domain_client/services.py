from sqlalchemy.orm import Session

from src.domain_client import deactivation_checks
from src.domain_client.models import ClientMaster
from src.domain_client.schemas import ClientCreate, ClientUpdate
from src.infrastructure.repositories import client_repo
from src.core.exceptions import BadRequestError, DuplicatedRecordError, NotFoundError

# 顧客は削除しない（業務・シート・オーダーの履歴が参照する）。使わなくなった顧客は無効にする（M1.1）

def create_client(db: Session, client_in: ClientCreate) -> ClientMaster:
    code = client_in.code.upper()
    if client_repo.get_by_code(db, code=code):
        raise DuplicatedRecordError(f"Client code '{code}' is already used.")
    # 同時の登録はユニーク制約（uq_client_id）で 409 になる
    return client_repo.create(db=db, obj_in=client_in)

def get_clients(db: Session, include_inactive: bool = False) -> list[ClientMaster]:
    return client_repo.get_list(db, include_inactive=include_inactive)

def update_client(db: Session, client_id: int, client_in: ClientUpdate) -> ClientMaster:
    db_client = client_repo.get(db=db, id=client_id)
    if not db_client:
        raise NotFoundError(f"Client(id={client_id}) not found")

    return client_repo.update(db=db, db_obj=db_client, obj_in=client_in)

def deactivate_client(db: Session, client_id: int) -> ClientMaster:
    """顧客を無効にします。有効な業務が残っている間は断ります（理由は登録された確認が返す）。"""
    client = client_repo.get_locked(db, id=client_id)
    if not client:
        raise NotFoundError(f"Client(id={client_id}) not found")
    if not client.is_active:
        raise BadRequestError(f"Client {client.code} is already inactive.")
    reasons = deactivation_checks.reasons_not_to_deactivate(db, client)
    if reasons:
        raise BadRequestError(f"Client {client.code} cannot be deactivated: " + "; ".join(reasons))
    client.is_active = False
    db.commit()
    db.refresh(client)
    return client

def activate_client(db: Session, client_id: int) -> ClientMaster:
    """無効にした顧客を、再び有効にします。"""
    client = client_repo.get_locked(db, id=client_id)
    if not client:
        raise NotFoundError(f"Client(id={client_id}) not found")
    if client.is_active:
        raise BadRequestError(f"Client {client.code} is already active.")
    client.is_active = True
    db.commit()
    db.refresh(client)
    return client

def require_active_client(db: Session, client_code: str, *, lock: bool = False) -> ClientMaster:
    """
    新しいデータ（業務・シート）を作る前の確認。顧客が無ければ 404、無効なら 400。
    lock=True は顧客の行をロックする（業務の連番の採番や復元を、無効化と同時に走らせないため）。
    """
    code = client_code.strip().upper()
    client = client_repo.get_locked_by_code(db, code=code) if lock else client_repo.get_by_code(db, code=code)
    if not client:
        raise NotFoundError(f"Client with code '{code}' does not exist.")
    if not client.is_active:
        raise BadRequestError(f"Client {code} is inactive; activate it first.")
    return client
