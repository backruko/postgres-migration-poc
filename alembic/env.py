#Migration（マイグレーション）の実行方法
#今後、models.py にテーブル（クラス）を書いたり変更したりしたら、必ず以下の2つのコマンドをセットで実行します。

#① 変更を検知してスクリプトを自動生成する（造図面）
#alembic revision --autogenerate -m "initialize_database"

#② データベースに適用する（施工）
#alembic upgrade head

from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# --- ここを追加・修正 ---
from src.infrastructure.config import settings
from src.infrastructure.database import Base
# 注意: モデルがBaseを継承して作られていることをAlembicに知らせるために、
# ここで全てのmodels.pyをインポートしておく必要があります（後で作成した際に追加します）
import src.domain_biz.models 
# import src.domain_schedule.models 
# -------------------------

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# --- ここを追加！ almebic.iniのURLを上書きして、.envの設定を使うようにする ---
config.set_main_option("sqlalchemy.url", settings.database_url)

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
