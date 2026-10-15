# 1. 作成しておいた CRUDBase をインポート
from src.infrastructure.repositories.base import CRUDBase

# 2. Clientドメインの Model と Schema をインポート
from src.domain_client.models import ClientMaster
from src.domain_client.schemas import ClientCreate, ClientUpdate

# 3. CRUDBase に型枠（Model, Create, Update）をはめて、専用のクラスを作る
class CRUDClient(CRUDBase[ClientMaster, ClientCreate, ClientUpdate]):
    # ※ 基本的な(CRUD)はすべて親クラスがやってくれます。
    # ※ もし「名前で検索する」などの特殊な処理が必要になれば、ここに書き足せばOKです。
    pass

# 4. 今作ったクラスをインスタンス化する（ これをRouterで使います）
client_repo = CRUDClient(ClientMaster)