"""ope order slot uniqueness ignores canceled

Revision ID: 5eede5808433
Revises: 9e1bcef6cbc7
Create Date: 2026-10-30 10:21:48.699555

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5eede5808433'
down_revision: Union[str, Sequence[str], None] = '9e1bcef6cbc7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 発生枠の一意インデックス: (名前, 列, 枠の種類の条件)
SLOT_INDEXES = [
    ('uq_ope_order_sheet_slot', ['template_id', 'source_sheet_event_id'], 'source_sheet_event_id IS NOT NULL'),
    ('uq_ope_order_one_off_slot', ['template_id', 'one_off_instance_id'], 'one_off_instance_id IS NOT NULL'),
    ('uq_ope_order_date_slot', ['template_id', 'occurrence_date'],
     'source_sheet_event_id IS NULL AND one_off_instance_id IS NULL'),
]


def _recreate(extra_condition: str) -> None:
    for name, columns, condition in SLOT_INDEXES:
        op.drop_index(name, table_name='ope_order')
        op.create_index(name, 'ope_order', columns, unique=True, postgresql_where=sa.text(condition + extra_condition))


def upgrade() -> None:
    """Upgrade schema."""
    # autogenerate は部分インデックスの条件の変更を検出しないため手で書く。
    # 取消されたオーダーは履歴として残し、同じ枠が戻ったら新しいオーダーを作れるよう、一意の対象から外す
    _recreate(" AND status <> 'CANCELED'")


def downgrade() -> None:
    """Downgrade schema."""
    # ※ 同じ枠に取消済みと有効なオーダーが両方ある場合、元の一意インデックスの作成に失敗します
    _recreate("")
