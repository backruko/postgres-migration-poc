"""business calendar holiday policy

Revision ID: 9b2ec314368a
Revises: 9ee3f3e0624e
Create Date: 2026-10-21 16:40:00.562518

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '9b2ec314368a'
down_revision: Union[str, Sequence[str], None] = '9ee3f3e0624e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

day_unit_enum = postgresql.ENUM('CALENDAR', 'BUSINESS', name='plan_offset_day_unit_enum', create_type=False)
holiday_policy_enum = postgresql.ENUM(
    'AS_IS', 'SKIP', 'FOLLOWING', 'PRECEDING', 'MODIFIED_FOLLOWING', 'MODIFIED_PRECEDING',
    name='plan_holiday_policy_enum', create_type=False,
)

OLD_SHEET_CHECK = (
    "template_type <> 'REFERENCE_SHEET_BASED' OR "
    "(reference_sheet_id IS NOT NULL AND reference_event_marker IS NOT NULL AND offset_minutes IS NOT NULL)"
)
NEW_SHEET_CHECK = (
    "template_type <> 'REFERENCE_SHEET_BASED' OR "
    "(reference_sheet_id IS NOT NULL AND reference_event_marker IS NOT NULL AND offset_minutes IS NOT NULL "
    "AND offset_days IS NOT NULL AND offset_day_unit IS NOT NULL)"
)


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()

    # --- 1. 営業日カレンダー ---
    op.create_table('business_calendar',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('code', sa.String(length=50), nullable=False, comment='カレンダーコード（例: JP_BANK）'),
    sa.Column('name', sa.String(length=100), nullable=False, comment='カレンダー名'),
    sa.Column('weekend_days', postgresql.ARRAY(sa.Integer()), server_default=sa.text("'{6,7}'"), nullable=False, comment='週末とする ISO 曜日（1=月 ... 7=日）'),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('weekend_days <@ ARRAY[1,2,3,4,5,6,7] AND cardinality(weekend_days) < 7', name='ck_business_calendar_weekend_days'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code')
    )
    op.create_index(op.f('ix_business_calendar_id'), 'business_calendar', ['id'], unique=False)
    op.create_table('business_calendar_holiday',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('calendar_id', sa.Integer(), nullable=False),
    sa.Column('holiday_date', sa.Date(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=True, comment='休日名（例: 元日）'),
    sa.ForeignKeyConstraint(['calendar_id'], ['business_calendar.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('calendar_id', 'holiday_date', name='uq_business_calendar_holiday')
    )
    op.create_index(op.f('ix_business_calendar_holiday_calendar_id'), 'business_calendar_holiday', ['calendar_id'], unique=False)
    op.create_index(op.f('ix_business_calendar_holiday_id'), 'business_calendar_holiday', ['id'], unique=False)

    # --- 2. テンプレート: 休日ポリシー・営業日オフセット ---
    day_unit_enum.create(bind, checkfirst=True)
    holiday_policy_enum.create(bind, checkfirst=True)
    op.add_column('plan_template', sa.Column('offset_days', sa.Integer(), nullable=True, comment='基準日のずれ（日数。単位は offset_day_unit）'))
    op.add_column('plan_template', sa.Column('offset_day_unit', day_unit_enum, nullable=True, comment='offset_days の単位（暦日 / 営業日）'))
    op.add_column('plan_template', sa.Column('business_calendar_id', sa.Integer(), nullable=True, comment='営業日カレンダー'))
    op.add_column('plan_template', sa.Column('holiday_policy', holiday_policy_enum, server_default='AS_IS', nullable=False, comment='非営業日に当たった場合の扱い'))
    op.alter_column('plan_template', 'offset_minutes',
               existing_type=sa.INTEGER(),
               comment='(基準日 + start_time) からの時刻のずれ',
               existing_comment='(event_date + start_time) からのずれ',
               existing_nullable=True)
    op.create_index(op.f('ix_plan_template_business_calendar_id'), 'plan_template', ['business_calendar_id'], unique=False)
    op.create_foreign_key(
        'fk_plan_template_business_calendar_id', 'plan_template', 'business_calendar',
        ['business_calendar_id'], ['id'], ondelete='RESTRICT',
    )

    # 既存のシート参照テンプレートは「暦日0日 + offset_minutes」として従来と同じ結果になるようバックフィル
    op.execute("""
        UPDATE plan_template SET offset_days = 0, offset_day_unit = 'CALENDAR'
        WHERE template_type = 'REFERENCE_SHEET_BASED'
    """)
    op.drop_constraint('ck_plan_template_sheet_fields', 'plan_template', type_='check')
    op.create_check_constraint('ck_plan_template_sheet_fields', 'plan_template', NEW_SHEET_CHECK)
    op.create_check_constraint(
        'ck_plan_template_calendar_required', 'plan_template',
        "(holiday_policy = 'AS_IS' AND offset_day_unit IS DISTINCT FROM 'BUSINESS') OR business_calendar_id IS NOT NULL",
    )

    # --- 3. 無効化理由に CALENDAR_CHANGED を追加 ---
    op.execute("ALTER TYPE plan_void_reason_enum ADD VALUE IF NOT EXISTS 'CALENDAR_CHANGED'")


def downgrade() -> None:
    """Downgrade schema."""
    # ※ ENUM値 CALENDAR_CHANGED は PostgreSQL の制約上削除できないため残します
    # ※ 営業日オフセット・休日ポリシーの設定は失われます（シート参照テンプレートは offset_minutes のみに戻る）
    op.drop_constraint('ck_plan_template_calendar_required', 'plan_template', type_='check')
    op.drop_constraint('ck_plan_template_sheet_fields', 'plan_template', type_='check')
    op.create_check_constraint('ck_plan_template_sheet_fields', 'plan_template', OLD_SHEET_CHECK)

    op.drop_constraint('fk_plan_template_business_calendar_id', 'plan_template', type_='foreignkey')
    op.drop_index(op.f('ix_plan_template_business_calendar_id'), table_name='plan_template')
    op.alter_column('plan_template', 'offset_minutes',
               existing_type=sa.INTEGER(),
               comment='(event_date + start_time) からのずれ',
               existing_comment='(基準日 + start_time) からの時刻のずれ',
               existing_nullable=True)
    op.drop_column('plan_template', 'holiday_policy')
    op.drop_column('plan_template', 'business_calendar_id')
    op.drop_column('plan_template', 'offset_day_unit')
    op.drop_column('plan_template', 'offset_days')
    holiday_policy_enum.drop(op.get_bind(), checkfirst=True)
    day_unit_enum.drop(op.get_bind(), checkfirst=True)

    op.drop_index(op.f('ix_business_calendar_holiday_id'), table_name='business_calendar_holiday')
    op.drop_index(op.f('ix_business_calendar_holiday_calendar_id'), table_name='business_calendar_holiday')
    op.drop_table('business_calendar_holiday')
    op.drop_index(op.f('ix_business_calendar_id'), table_name='business_calendar')
    op.drop_table('business_calendar')
