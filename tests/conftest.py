import os
import pytest

# src を読み込む前に決める: config.py は読み込まれた時点で ENVIRONMENT を見て .env.test（テスト用 DB）を選ぶ。
# 後から設定しても、開発 DB の .env がもう読まれている
os.environ["ENVIRONMENT"] = "test"

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.infrastructure.config import settings
from src.infrastructure.database import Base

# アプリと同じく、plan の発生枠の変更をオーダーに反映する受け手を登録する（main.py と同じ）。
# これにより全テストで plan の変更がオーダーにも反映され、ope のテーブルも create_all で作られる
from src.coordination import order_sync
order_sync.register()

# 通知の仕組みの表（アウトボックス・通知）も create_all で作る（まだルーターから読み込まれないため、ここで読み込む）
import src.domain_events.models  # noqa: E402
import src.domain_notification.models  # noqa: E402
import src.worker_health.models  # noqa: E402,F401  (ワーカーの健康の記録 P.4a)

# テスト用 DB のエンジン（ENVIRONMENT=test なので .env.test の URL。CI では環境変数の DATABASE_URL が優先）
engine = create_engine(settings.database_url)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 表は実行全体で 1 回だけ作り、最後に消す（テストごとに作り直すと遅い。各テストは自分で作ったデータだけを見る）。
# 最後に消すので、次の実行はいつもモデルの今の形で作り直される
@pytest.fixture(scope="session", autouse=True)
def setup_test_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)

# テストごとのセッション。成否にかかわらず最後にロールバックし、コミットしていない変更をほかのテストに残さない
@pytest.fixture
def db_session():
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
