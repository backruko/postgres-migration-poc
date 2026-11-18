"""notification test sends without a rule (N.5a)

宛先の「テスト送信」（2026-11-12 の決定: 本物のアラームと同じ道、ワーカー → チャネルで送る）のため、通知のルールを
空にできるようにする。空のルールの通知はテスト送信だけ（出来事 DestinationTestRequested から通知の仕組みが作る）で、
届かなかったアラームの帯には出さない。
戻すときは、テスト送信の通知と配信を消してから（ルールが空の行があると、空を禁止できないため）。

Revision ID: db448a125377
Revises: 5aac6ec73aca
Create Date: 2026-11-12

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'db448a125377'
down_revision: str | Sequence[str] | None = '5aac6ec73aca'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column('notification', 'rule_id',
               existing_type=sa.INTEGER(),
               nullable=True,
               comment='当てはまったルール（空 = 宛先のテスト送信）',
               existing_comment='当てはまったルール')


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DELETE FROM notification_delivery WHERE notification_id IN (SELECT id FROM notification WHERE rule_id IS NULL)")
    op.execute("DELETE FROM notification WHERE rule_id IS NULL")
    op.alter_column('notification', 'rule_id',
               existing_type=sa.INTEGER(),
               nullable=False,
               comment='当てはまったルール',
               existing_comment='当てはまったルール（空 = 宛先のテスト送信）')
