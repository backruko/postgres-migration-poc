import os
import pytest

# 【非常に重要：ステップ1】
# プロジェクトのコードをインポートする前に、環境変数を強制的に "test" に設定します！
# これにより、config.py が .env.test を読み込み、テスト用DBの接続情報を取得します。
os.environ["ENVIRONMENT"] = "test"

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# 以下のインポートパスは実際のプロジェクトのパスに置き換えてください
from src.infrastructure.config import settings  # 設定クラス
from src.infrastructure.database import Base    # SQLAlchemyのBaseクラス

# 【ステップ2】テスト専用のデータベースエンジンを作成
# 上記で環境変数を設定したため、settings.database_url はテスト用DBのURLになっています
engine = create_engine(settings.database_url)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 【ステップ3】グローバルなテーブル作成と削除 (scope="session" によりテスト全体で1回のみ実行)
@pytest.fixture(scope="session", autouse=True)
def setup_test_db():
    # テスト開始前：テスト用DBにすべてのテーブルを作成
    Base.metadata.create_all(bind=engine)
    
    yield  # yield を境界として、上が「テスト前」、下が「テスト後」の処理
    
    # すべてのテスト終了後：テスト用DBのすべてのテーブルを削除し、クリーンな状態を保つ
    Base.metadata.drop_all(bind=engine)

# 【ステップ4】各テスト用のデータベースセッションを提供 (デフォルトの scope="function" によりテスト関数ごとに実行)
@pytest.fixture
def db_session():
    # データベースセッションを開始
    session = TestingSessionLocal()
    try:
        yield session  # テスト関数にセッションを渡す
    finally:
        # テストの成否にかかわらず、強制的にロールバックする
        # これにより、テストAのデータがテストBに影響を与えず、テストの独立性が保証される
        session.rollback()
        session.close()