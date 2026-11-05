"""plan template deadline

Revision ID: d17d631f5ad2
Revises: 2f7614f1cb02
Create Date: 2026-11-05 17:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'd17d631f5ad2'
down_revision: Union[str, Sequence[str], None] = '2f7614f1cb02'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEADLINE_MODE = postgresql.ENUM('END', 'OFFSET', 'FIXED_TIME', name='plan_deadline_mode_enum')


def upgrade() -> None:
    """Upgrade schema."""
    # 締切の決め方（N.1）。新しい型なので、同じトランザクションの CHECK で値を使える（ADD VALUE とは違う）
    DEADLINE_MODE.create(op.get_bind(), checkfirst=True)
    op.add_column('plan_template', sa.Column(
        'deadline_mode', postgresql.ENUM(name='plan_deadline_mode_enum', create_type=False),
        server_default='END', nullable=False, comment='締切の決め方',
    ))
    op.add_column('plan_template', sa.Column('deadline_offset_minutes', sa.Integer(), nullable=True, comment='OFFSET: 予定開始から締切までの分'))
    op.add_column('plan_template', sa.Column('deadline_time', sa.Time(), nullable=True, comment='FIXED_TIME: 締切のローカル時刻'))
    op.add_column('plan_template', sa.Column('deadline_day_offset', sa.Integer(), nullable=True, comment='FIXED_TIME: 予定日の何日後か（0 = 当日）'))
    op.add_column('plan_template', sa.Column(
        'warn_before_minutes', sa.Integer(), server_default=sa.text('0'), nullable=False,
        comment='締切の何分前に「まもなく」を出すか（0 = 出さない）',
    ))
    op.create_check_constraint(
        'ck_plan_template_deadline_offset', 'plan_template',
        "(deadline_mode = 'OFFSET') = (deadline_offset_minutes IS NOT NULL)",
    )
    op.create_check_constraint(
        'ck_plan_template_deadline_fixed', 'plan_template',
        "(deadline_mode = 'FIXED_TIME') = (deadline_time IS NOT NULL) "
        "AND (deadline_mode = 'FIXED_TIME') = (deadline_day_offset IS NOT NULL)",
    )
    op.create_check_constraint(
        'ck_plan_template_deadline_range', 'plan_template',
        "(deadline_offset_minutes IS NULL OR deadline_offset_minutes BETWEEN 1 AND 10080) "
        "AND (deadline_day_offset IS NULL OR deadline_day_offset BETWEEN 0 AND 7) "
        "AND warn_before_minutes BETWEEN 0 AND 1440",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('ck_plan_template_deadline_range', 'plan_template', type_='check')
    op.drop_constraint('ck_plan_template_deadline_fixed', 'plan_template', type_='check')
    op.drop_constraint('ck_plan_template_deadline_offset', 'plan_template', type_='check')
    op.drop_column('plan_template', 'warn_before_minutes')
    op.drop_column('plan_template', 'deadline_day_offset')
    op.drop_column('plan_template', 'deadline_time')
    op.drop_column('plan_template', 'deadline_offset_minutes')
    op.drop_column('plan_template', 'deadline_mode')
    DEADLINE_MODE.drop(op.get_bind(), checkfirst=True)
