"""notification delivery attempts (N.5b)

配信の 1 回ごとの試み（notification_delivery_attempt）: 何回目・いつ・どのアドレスへ・結果・相手のサーバーの返事。
宛先のアドレスを直すと再試行は新しいアドレスへ送る（2026-11-12 の決定）ので、どの回がどこへ送ったかを残す。追記だけ。
通知の記録を新しい順に読むためのインデックス（ix_notification_created）も足す。
この前に送った配信には試みの行はない（配信の行の回数・最後の失敗はある）。

Revision ID: 7a32fe73fa80
Revises: db448a125377
Create Date: 2026-11-12

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7a32fe73fa80'
down_revision: str | Sequence[str] | None = 'db448a125377'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('notification_delivery_attempt',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('delivery_id', sa.Integer(), nullable=False),
    sa.Column('attempt_no', sa.Integer(), nullable=False, comment='何回目か（1 から）'),
    sa.Column('attempted_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('address', sa.String(length=2000), nullable=False, comment='この回に送ったアドレス（そのときの宛先のもの）'),
    sa.Column('ok', sa.Boolean(), nullable=False, comment='届いたか'),
    sa.Column('permanent', sa.Boolean(), server_default=sa.text('false'), nullable=False, comment='失敗が永続的か（もう試さない）'),
    sa.Column('error', sa.Text(), nullable=True, comment='失敗の理由（相手のサーバーの返事のまま）'),
    sa.Column('provider_message_id', sa.String(length=200), nullable=True),
    sa.CheckConstraint('attempt_no >= 1', name='ck_notification_delivery_attempt_no'),
    sa.CheckConstraint('ok = (error IS NULL)', name='ck_notification_delivery_attempt_error'),
    sa.ForeignKeyConstraint(['delivery_id'], ['notification_delivery.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('delivery_id', 'attempt_no', name='uq_notification_delivery_attempt_no')
    )
    op.create_index('ix_notification_created', 'notification', ['created_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_notification_created', table_name='notification')
    op.drop_table('notification_delivery_attempt')
