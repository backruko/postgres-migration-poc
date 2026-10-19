# 1. 作成しておいた CRUDBase をインポート
from sqlalchemy.orm import Session

from src.infrastructure.repositories.base import CRUDBase

# 2. Clientドメインの Model と Schema をインポート
from src.domain_client.models import ClientMaster
from src.domain_client.schemas import ClientCreate, ClientUpdate

# 3. CRUDBase に型枠（Model, Create, Update）をはめて、専用のクラスを作る
class CRUDClient(CRUDBase[ClientMaster, ClientCreate, ClientUpdate]):
    def get_by_code(self, db: Session, *, code: str) -> ClientMaster | None:
        """
        client_codeでクライアントを取得する
        """
        return db.query(self.model).filter(self.model.code == code).first()

# 4. 今作ったクラスをインスタンス化する（ これをRouterで使います）
client_repo = CRUDClient(ClientMaster)