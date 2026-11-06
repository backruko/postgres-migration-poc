# 1. 作成しておいた CRUDBase をインポート
from sqlalchemy import select
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

    def get_by_codes(self, db: Session, *, codes: list[str]) -> list[ClientMaster]:
        """
        複数のclient_codeでクライアントをまとめて取得する
        """
        return db.query(self.model).filter(self.model.code.in_(codes)).all()

    def get_list(self, db: Session, *, include_inactive: bool = False) -> list[ClientMaster]:
        """顧客の一覧（コード順）。既定では有効な顧客だけ"""
        stmt = select(ClientMaster).order_by(ClientMaster.code)
        if not include_inactive:
            stmt = stmt.where(ClientMaster.is_active.is_(True))
        return list(db.scalars(stmt).all())

    def get_locked(self, db: Session, *, id: int) -> ClientMaster | None:
        """顧客を行ロック付きで取得する（有効 / 無効の切り替え用）"""
        return db.scalars(select(ClientMaster).where(ClientMaster.id == id).with_for_update()).first()

    def get_locked_by_code(self, db: Session, *, code: str) -> ClientMaster | None:
        """顧客を行ロック付きで取得する（業務の連番の採番・業務の復元用）"""
        return db.scalars(select(ClientMaster).where(ClientMaster.code == code).with_for_update()).first()

# 4. 今作ったクラスをインスタンス化する（ これをRouterで使います）
client_repo = CRUDClient(ClientMaster)
