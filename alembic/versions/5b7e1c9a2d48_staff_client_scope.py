"""staff client scope (client-limited staff, C.1)

職員が担当する顧客（顧客の範囲）の表 staff_client を作る。行の無い職員は今までどおり全顧客を扱える。
今の職員には行を作らないので、動きは変わらない。

Revision ID: 5b7e1c9a2d48
Revises: 8c4d2e6f1a37
Create Date: 2026-11-12 10:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '5b7e1c9a2d48'
down_revision: str | Sequence[str] | None = '8c4d2e6f1a37'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'staff_client',
        sa.Column('staff_user_id', sa.Integer(), nullable=False),
        sa.Column('client_code', sa.CHAR(length=4), nullable=False),
        sa.Column('granted_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('granted_by_id', sa.Integer(), nullable=True, comment='範囲を設定した職員'),
        sa.ForeignKeyConstraint(['staff_user_id'], ['staff_user.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['client_code'], ['client_master.code'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['granted_by_id'], ['staff_user.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('staff_user_id', 'client_code'),
    )
    op.create_index(op.f('ix_staff_client_client_code'), 'staff_client', ['client_code'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_staff_client_client_code'), table_name='staff_client')
    op.drop_table('staff_client')
