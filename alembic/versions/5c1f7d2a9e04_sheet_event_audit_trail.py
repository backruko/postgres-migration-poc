"""sheet event audit trail

Revision ID: 5c1f7d2a9e04
Revises: 3493c39bdf43
Create Date: 2026-10-28 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5c1f7d2a9e04'
down_revision: Union[str, Sequence[str], None] = '3493c39bdf43'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 人が動かす・取り消す前の日付（取り込んだ日付・ルールの日付の出どころを残す）
    op.add_column('reference_sheet_event', sa.Column(
        'original_date', sa.Date(), nullable=True,
        comment='人が変更・取消する前の日付（取り込み・ルール由来の日付のみ。人が追加した日付は NULL）',
    ))
    # 取消の記録を、生成マーカー以外（取り込んだ日付）にも残せるようにする
    op.drop_constraint('ck_reference_sheet_event_cancel', 'reference_sheet_event', type_='check')
    op.create_check_constraint('ck_reference_sheet_event_cancel', 'reference_sheet_event', "NOT is_cancelled OR is_manual")
    op.alter_column('reference_sheet_event', 'is_manual', existing_type=sa.Boolean(), existing_nullable=False,
                    comment='人が編集・追加・取消した日付（再生成・再取り込みで上書きしない）',
                    existing_comment='人が編集・追加・取消した日付（再生成で上書きしない）')
    op.alter_column('reference_sheet_event', 'is_cancelled', existing_type=sa.Boolean(), existing_nullable=False,
                    comment='人が取り消した日付（再生成・再取り込みで復活させないための記録）',
                    existing_comment='人が取り消した生成日（再生成で復活させないための記録）')


def downgrade() -> None:
    """Downgrade schema."""
    # ※ 取り込んだ日付の取消の記録は失われます（以前の制約はルール由来の取消のみ許すため削除する）
    op.execute("DELETE FROM reference_sheet_event WHERE is_cancelled AND rule_date IS NULL")
    op.drop_constraint('ck_reference_sheet_event_cancel', 'reference_sheet_event', type_='check')
    op.create_check_constraint(
        'ck_reference_sheet_event_cancel', 'reference_sheet_event', "NOT is_cancelled OR (is_manual AND rule_date IS NOT NULL)"
    )
    op.alter_column('reference_sheet_event', 'is_cancelled', existing_type=sa.Boolean(), existing_nullable=False,
                    comment='人が取り消した生成日（再生成で復活させないための記録）',
                    existing_comment='人が取り消した日付（再生成・再取り込みで復活させないための記録）')
    op.alter_column('reference_sheet_event', 'is_manual', existing_type=sa.Boolean(), existing_nullable=False,
                    comment='人が編集・追加・取消した日付（再生成で上書きしない）',
                    existing_comment='人が編集・追加・取消した日付（再生成・再取り込みで上書きしない）')
    op.drop_column('reference_sheet_event', 'original_date')
