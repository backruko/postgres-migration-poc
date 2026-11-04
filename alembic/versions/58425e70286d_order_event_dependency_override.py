"""order event dependency override

Revision ID: 58425e70286d
Revises: cb0ae371f402
Create Date: 2026-11-04 15:58:06.793663

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '58425e70286d'
down_revision: Union[str, Sequence[str], None] = 'cb0ae371f402'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # オーダーの記録の種類に DEPENDENCY_OVERRIDE_APPROVED を追加（PostgreSQL 12 以降はトランザクション内で追加できる）
    op.execute("ALTER TYPE ope_order_event_type_enum ADD VALUE IF NOT EXISTS 'DEPENDENCY_OVERRIDE_APPROVED'")


OLD_VALUES = (
    "'CREATED', 'STATUS_CHANGED', 'RESCHEDULED', 'PINNED', 'UNPINNED', 'EARLY_START_APPROVED', "
    "'CONFLICT_RAISED', 'CONFLICT_RESOLVED'"
)


def downgrade() -> None:
    """Downgrade schema."""
    # ENUM の値は削除できないため型を作り直す（DEPENDENCY_OVERRIDE_APPROVED の記録が残っていると失敗する: 記録は追記のみ）。
    # event_type を参照する CHECK 制約は古い型に結び付いているため、外してから作り直す
    op.drop_constraint('ck_ope_order_event_status_change', 'ope_order_event', type_='check')
    op.drop_constraint('ck_ope_order_event_created', 'ope_order_event', type_='check')
    op.execute("ALTER TYPE ope_order_event_type_enum RENAME TO ope_order_event_type_enum_old")
    op.execute(f"CREATE TYPE ope_order_event_type_enum AS ENUM ({OLD_VALUES})")
    op.execute(
        "ALTER TABLE ope_order_event ALTER COLUMN event_type TYPE ope_order_event_type_enum "
        "USING event_type::text::ope_order_event_type_enum"
    )
    op.execute("DROP TYPE ope_order_event_type_enum_old")
    op.create_check_constraint(
        'ck_ope_order_event_status_change', 'ope_order_event',
        "event_type <> 'STATUS_CHANGED' OR (from_status IS NOT NULL AND to_status IS NOT NULL)",
    )
    op.create_check_constraint('ck_ope_order_event_created', 'ope_order_event', "event_type <> 'CREATED' OR to_status IS NOT NULL")
