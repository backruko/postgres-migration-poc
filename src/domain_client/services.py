from sqlalchemy.orm import Session
from src.domain_client.models import ClientMaster
from src.domain_client.schemas import ClientCreate

def create_client(db: Session, client_in: ClientCreate):
    # PydanticのデータからSQLAlchemyのモデルを作成
    new_client = ClientMaster(
        code=client_in.code,
        name=client_in.name
    )
    db.add(new_client)
    db.commit()
    db.refresh(new_client)
    return new_client

def get_clients(db: Session):
    return db.query(ClientMaster).all()