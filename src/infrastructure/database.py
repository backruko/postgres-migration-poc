from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from src.infrastructure.config import settings

# データベースエンジンの作成
engine = create_engine(settings.database_url, echo=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 全てのモデルの親となる基本クラス
class Base(DeclarativeBase):
    pass

# FastAPI用のDBセッション取得関数
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()