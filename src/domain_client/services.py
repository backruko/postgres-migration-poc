from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from src.domain_client.schemas import ClientCreate, ClientUpdate 
from src.infrastructure.repositories import client_repo
from src.infrastructure.exceptions import NotFoundError, ReferencedRecordError

def create_client(db: Session, client_in: ClientCreate):
    # ① DB操作（CRUD）は、すべて client_repo に任せる！
    # （CRUDBaseの create メソッドが、内部で add, commit, refresh を全部やってくれます）
    new_client = client_repo.create(db=db, obj_in=client_in)
    
    # ※ もし将来「作成完了したらメールを送る」などのビジネスロジックが増えたら、
    # ここに書き足します。これがService層の本来の役割です！
    
    return new_client

def get_clients(db: Session):
    # ② 取得も client_repo に任せる！
    # （CRUDBaseに get_multi のような複数取得メソッドがある前提です）
    return client_repo.get_multi(db=db)

def delete_client(db: Session, client_id: int):
    try:
        deleted_client = client_repo.remove(db=db, id=client_id)
        return deleted_client
    except IntegrityError as e:
        # DBのロールバック（これがないと以降のDB操作がすべて失敗します）
        db.rollback()
        # 外部キー制約違反などの場合
        raise ReferencedRecordError("このクライアントは他のデータ(biz_master等)で使用されているため削除できません。")

def update_client(db: Session, client_id: int, client_in: ClientUpdate):
    db_client = client_repo.get(db=db, id=client_id)
    if not db_client:
        raise NotFoundError(f"Client(id={client_id}) not found") 
    
    return client_repo.update(db=db, db_obj=db_client, obj_in=client_in)

    