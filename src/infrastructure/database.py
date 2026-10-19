from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session, with_loader_criteria

from src.infrastructure.config import settings
from src.infrastructure.mixin import SoftDeleteMixin


# データベースエンジンの作成
engine = create_engine(settings.database_url, echo=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 全てのモデルの親となる基本クラス
class Base(DeclarativeBase):
    pass

# ==========================================
# 💡 【追加】全局scope（グローバルフィルター）の制御コード
# ==========================================
@event.listens_for(SessionLocal, "do_orm_execute")
def _add_soft_delete_filter(execute_state):
    """
    全てのSQL実行時に割り込み、論理削除されたデータを自動で除外するフィルターを挟みます。
    ただし、`.execution_options(include_deleted=True)` が指定されている場合はスキップします。
    """
    from src.domain_biz.models import BizMaster, SOPVersion
    
    # 復元処理（Restore）など、明示的に解除オプションが指定されていないかチェック
    if not execute_state.execution_options.get("include_deleted", False):
        execute_state.statement = execute_state.statement.options(
            with_loader_criteria(
                SoftDeleteMixin,                 # 対象となるMixin
                lambda cls: cls.is_deleted == False,  # 自動適用する条件式
                include_aliases=True
            )
        )
    # SOPVersionのクエリ時、親が消えているものを自動で除外するスコープ
        # これにより、SOPVersion単体を取得しようとしても、親が消えていればNoneになります
        execute_state.statement = execute_state.statement.options(
            with_loader_criteria(
                SOPVersion,
                lambda cls: cls.biz_master.has(BizMaster.is_deleted == False),
                include_aliases=True
            )
        )
# FastAPI用のDBセッション取得関数
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()