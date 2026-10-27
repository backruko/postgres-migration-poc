"""rule generated sheet markers

Revision ID: 3493c39bdf43
Revises: 9b2ec314368a
Create Date: 2026-10-21 17:32:53.839901

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '3493c39bdf43'
down_revision: Union[str, Sequence[str], None] = '9b2ec314368a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# plan_template で作成済みの型を再利用する
holiday_policy_enum = postgresql.ENUM(
    'AS_IS', 'SKIP', 'FOLLOWING', 'PRECEDING', 'MODIFIED_FOLLOWING', 'MODIFIED_PRECEDING',
    name='plan_holiday_policy_enum', create_type=False,
)


def upgrade() -> None:
    """Upgrade schema."""
    # --- 1. マーカーの生成ルール ---
    op.add_column('reference_sheet_marker', sa.Column('rrule', sa.Text(), nullable=True, comment='日付生成ルール（例: FREQ=MONTHLY;BYMONTHDAY=27）'))
    op.add_column('reference_sheet_marker', sa.Column('anchor_date', sa.Date(), nullable=True, comment='ルールの起点日（DTSTART）'))
    op.add_column('reference_sheet_marker', sa.Column('business_calendar_id', sa.Integer(), nullable=True, comment='生成時に使う営業日カレンダー'))
    op.add_column('reference_sheet_marker', sa.Column('holiday_policy', holiday_policy_enum, server_default='AS_IS', nullable=False, comment='生成日が非営業日の場合の扱い'))
    op.create_index(op.f('ix_reference_sheet_marker_business_calendar_id'), 'reference_sheet_marker', ['business_calendar_id'], unique=False)
    op.create_foreign_key(
        'fk_reference_sheet_marker_business_calendar_id', 'reference_sheet_marker', 'business_calendar',
        ['business_calendar_id'], ['id'], ondelete='RESTRICT',
    )
    op.create_check_constraint(
        'ck_reference_sheet_marker_rule_fields', 'reference_sheet_marker', "(rrule IS NULL) = (anchor_date IS NULL)"
    )
    op.create_check_constraint(
        'ck_reference_sheet_marker_calendar_required', 'reference_sheet_marker',
        "holiday_policy = 'AS_IS' OR business_calendar_id IS NOT NULL",
    )

    # --- 2. 日付: 生成元の発生枠・人の判断 ---
    op.add_column('reference_sheet_event', sa.Column('rule_date', sa.Date(), nullable=True, comment='生成元のルール上の日付（発生枠の識別子）'))
    op.add_column('reference_sheet_event', sa.Column('is_manual', sa.Boolean(), server_default=sa.text('false'), nullable=False, comment='人が編集・追加・取消した日付（再生成で上書きしない）'))
    op.add_column('reference_sheet_event', sa.Column('is_cancelled', sa.Boolean(), server_default=sa.text('false'), nullable=False, comment='人が取り消した生成日（再生成で復活させないための記録）'))
    op.create_index(
        'uq_reference_sheet_event_rule_date', 'reference_sheet_event', ['marker_id', 'rule_date'],
        unique=True, postgresql_where=sa.text('rule_date IS NOT NULL'),
    )
    op.create_check_constraint(
        'ck_reference_sheet_event_cancel', 'reference_sheet_event',
        "NOT is_cancelled OR (is_manual AND rule_date IS NOT NULL)",
    )
    # 再生成で日付を入れ替える際の一時的な重複を許すため、一意制約をコミット時検査にする
    op.drop_constraint('uq_reference_sheet_event', 'reference_sheet_event', type_='unique')
    op.create_unique_constraint(
        'uq_reference_sheet_event', 'reference_sheet_event', ['marker_id', 'event_date'],
        deferrable=True, initially='DEFERRED',
    )


def downgrade() -> None:
    """Downgrade schema."""
    # ※ 生成ルールと人の判断の記録は失われます（取消済みの日付は削除し、残りは通常の日付として残す）
    op.execute("DELETE FROM reference_sheet_event WHERE is_cancelled")
    op.drop_constraint('uq_reference_sheet_event', 'reference_sheet_event', type_='unique')
    op.create_unique_constraint('uq_reference_sheet_event', 'reference_sheet_event', ['marker_id', 'event_date'])
    op.drop_constraint('ck_reference_sheet_event_cancel', 'reference_sheet_event', type_='check')
    op.drop_index('uq_reference_sheet_event_rule_date', table_name='reference_sheet_event', postgresql_where=sa.text('rule_date IS NOT NULL'))
    op.drop_column('reference_sheet_event', 'is_cancelled')
    op.drop_column('reference_sheet_event', 'is_manual')
    op.drop_column('reference_sheet_event', 'rule_date')

    op.drop_constraint('ck_reference_sheet_marker_calendar_required', 'reference_sheet_marker', type_='check')
    op.drop_constraint('ck_reference_sheet_marker_rule_fields', 'reference_sheet_marker', type_='check')
    op.drop_constraint('fk_reference_sheet_marker_business_calendar_id', 'reference_sheet_marker', type_='foreignkey')
    op.drop_index(op.f('ix_reference_sheet_marker_business_calendar_id'), table_name='reference_sheet_marker')
    op.drop_column('reference_sheet_marker', 'holiday_policy')
    op.drop_column('reference_sheet_marker', 'business_calendar_id')
    op.drop_column('reference_sheet_marker', 'anchor_date')
    op.drop_column('reference_sheet_marker', 'rrule')
