# Alembic の実行環境。モデルを変えたら `alembic revision --autogenerate -m "..."` で移行を作り、生成された内容を
# 必ず読んで直す（CHECK 制約などは検出されない）。適用は `alembic upgrade head`（サーバーではアプリの起動時）。

from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

from src.infrastructure.config import settings
from src.infrastructure.database import Base
# autogenerate は Base.metadata に登録された表しか比べない。表は models.py を読み込んだときに登録されるので、
# ここに無い models.py の表は「消えた」と見なされ、DROP する移行が作られてしまう。新しいドメインはここに足す
import src.domain_biz.models
import src.domain_client.models
import src.domain_plan.models
import src.domain_staff.models
import src.domain_ope.models
import src.domain_events.models
import src.domain_notification.models
import src.worker_health.models  # noqa: F401  (表を Base に登録するための import)

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# alembic.ini の URL は使わず、アプリと同じ設定（.env か環境変数の DATABASE_URL）で上書きする。
# 移行先がいつもアプリの使う DB になり、接続先を 2 か所で持たずに済む
config.set_main_option("sqlalchemy.url", settings.database_url)

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


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
