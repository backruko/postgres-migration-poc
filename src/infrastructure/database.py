from typing import TYPE_CHECKING

from sqlalchemy import create_engine, event, false
from sqlalchemy.orm import DeclarativeBase, Mapped, sessionmaker, with_loader_criteria

from src.infrastructure.config import settings
from src.core.mixin import SoftDeleteMixin


# データベースエンジンの作成（SQL のログは SQL_ECHO で切り替える。既定はオフ）
# pool_pre_ping: プールの接続を貸す前に確かめ、データベースの再起動などで切れていれば張り直す
# （確かめないと、再起動のあと最初の要求が接続ごとに 500 になる。代わりに貸すたびに 1 往復増える）
engine = create_engine(settings.database_url, echo=settings.sql_echo, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 全てのモデルの親となる基本クラス
class Base(DeclarativeBase):
    # 全モデルが id を持つ前提を型チェッカーに伝える（実行時にはカラムを追加しない）
    if TYPE_CHECKING:
        id: Mapped[int]

# 論理削除した行を、すべての ORM の問い合わせから自動で外す。問い合わせごとに条件を書くと、1 か所の書き忘れで
# 削除した業務や SOP が画面やオーダーの作成に出てしまうため。削除した行も要るとき（復元、使い回さない番号の確認、
# 記録や通知に削除済みのものの名前を出すとき）だけ `.execution_options(include_deleted=True)` で外す
@event.listens_for(SessionLocal, "do_orm_execute")
def _add_soft_delete_filter(execute_state):
    from src.domain_biz.models import BizMaster, SOPVersion

    if not execute_state.execution_options.get("include_deleted", False):
        execute_state.statement = execute_state.statement.options(
            with_loader_criteria(
                SoftDeleteMixin,
                # == false() は SQL の「= false」。.is_(False) の「IS false」とは別の式になる
                lambda cls: cls.is_deleted == false(),
                include_aliases=True
            )
        )
        # SOP の版は自分では削除されないが、業務が削除されたら一緒に見えなくする（版だけ取っても None になる）
        execute_state.statement = execute_state.statement.options(
            with_loader_criteria(
                SOPVersion,
                lambda cls: cls.biz_master.has(BizMaster.is_deleted == false()),
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
