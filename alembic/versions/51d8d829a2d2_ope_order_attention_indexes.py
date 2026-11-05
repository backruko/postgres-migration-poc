"""ope order attention indexes

Revision ID: 51d8d829a2d2
Revises: c5bb40015e61
Create Date: 2026-11-05 19:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '51d8d829a2d2'
down_revision: Union[str, Sequence[str], None] = 'c5bb40015e61'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 遅れ・まもなくの一覧（GET /ops/attention、N.3）: 完了・取消以外のオーダーだけの部分インデックス
    op.create_index(
        'ix_ope_order_open_deadline', 'ope_order', ['deadline_at'], unique=False,
        postgresql_where=sa.text("status NOT IN ('FINISHED', 'CANCELED')"),
    )
    op.create_index(
        'ix_ope_order_open_warn', 'ope_order', ['warn_at'], unique=False,
        postgresql_where=sa.text("status NOT IN ('FINISHED', 'CANCELED') AND warn_at IS NOT NULL"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_ope_order_open_warn', table_name='ope_order')
    op.drop_index('ix_ope_order_open_deadline', table_name='ope_order')
