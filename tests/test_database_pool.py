"""アプリの接続プール: データベースの再起動などで切れた接続を、使う前に張り直す（pool_pre_ping）。

切れた接続をそのまま渡すと、再起動のあと最初の要求が接続ごとに 500 になる。
"""
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from src.infrastructure.config import settings
from src.infrastructure.database import engine


def _backend_pid(connection) -> int:
    return connection.execute(text("select pg_backend_pid()")).scalar_one()


def test_a_connection_closed_by_the_server_is_replaced_before_use():
    engine.dispose()  # プールを空にして、次に借りる接続を 1 本に決める
    try:
        with engine.connect() as connection:
            old_pid = _backend_pid(connection)
        # 接続はプールに戻った。サーバー側から切る（再起動・管理者による切断と同じ）
        killer = create_engine(settings.database_url, poolclass=NullPool)
        try:
            with killer.connect() as admin:
                assert admin.execute(text("select pg_terminate_backend(:pid)"), {"pid": old_pid}).scalar_one()
        finally:
            killer.dispose()

        with engine.connect() as connection:
            assert connection.execute(text("select 1")).scalar_one() == 1
            assert _backend_pid(connection) != old_pid
    finally:
        engine.dispose()
