"""notification failure acknowledgement (N.4b)

届かなかったアラーム（通知の状態が failed / partly_failed）を、職員が「確認した」と記録する列を足す
（日時と職員）。確認されるまで、日次ダッシュボードの赤い帯と件数に出続ける（2026-11-12 の決定）。
- 確認できるのは届かなかった通知だけ（チェック制約）
- まだ確認されていない失敗・最近の失敗を、記録全体を読まずに引く部分インデックス
- 職員を消しても記録は残す（外部キーは SET NULL）

Revision ID: 5aac6ec73aca
Revises: 0a902c7518fe
Create Date: 2026-11-12 15:07:22.044934

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5aac6ec73aca'
down_revision: str | Sequence[str] | None = '0a902c7518fe'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FK_NAME = 'notification_failure_acknowledged_by_id_fkey'  # PostgreSQL が名前なしで付ける名前と同じ


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('notification', sa.Column('failure_acknowledged_at', sa.DateTime(timezone=True), nullable=True, comment='届かなかったことを職員が確認した日時（failed / partly_failed のときだけ）'))
    op.add_column('notification', sa.Column('failure_acknowledged_by_id', sa.Integer(), nullable=True, comment='確認した職員'))
    op.create_foreign_key(FK_NAME, 'notification', 'staff_user', ['failure_acknowledged_by_id'], ['id'], ondelete='SET NULL')
    op.create_check_constraint(
        'ck_notification_failure_ack', 'notification',
        "failure_acknowledged_at IS NULL OR status IN ('FAILED', 'PARTLY_FAILED')",
    )
    op.create_index('ix_notification_failure', 'notification', ['completed_at'], unique=False, postgresql_where=sa.text("status IN ('FAILED', 'PARTLY_FAILED')"))
    op.create_index('ix_notification_unacknowledged_failure', 'notification', ['created_at'], unique=False, postgresql_where=sa.text("status IN ('FAILED', 'PARTLY_FAILED') AND failure_acknowledged_at IS NULL"))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_notification_unacknowledged_failure', table_name='notification', postgresql_where=sa.text("status IN ('FAILED', 'PARTLY_FAILED') AND failure_acknowledged_at IS NULL"))
    op.drop_index('ix_notification_failure', table_name='notification', postgresql_where=sa.text("status IN ('FAILED', 'PARTLY_FAILED')"))
    op.drop_constraint('ck_notification_failure_ack', 'notification', type_='check')
    op.drop_constraint(FK_NAME, 'notification', type_='foreignkey')
    op.drop_column('notification', 'failure_acknowledged_by_id')
    op.drop_column('notification', 'failure_acknowledged_at')
