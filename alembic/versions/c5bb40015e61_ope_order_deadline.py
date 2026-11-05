"""ope order deadline

Revision ID: c5bb40015e61
Revises: d17d631f5ad2
Create Date: 2026-11-05 18:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c5bb40015e61'
down_revision: Union[str, Sequence[str], None] = 'd17d631f5ad2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 締切をオーダーに保存する（N.2）。いったん NULL 可で追加し、既存のオーダーを埋めてから NOT NULL にする
    op.add_column('ope_order', sa.Column(
        'deadline_mode', postgresql.ENUM(name='plan_deadline_mode_enum', create_type=False), nullable=True,
        comment='締切の決め方（テンプレートの写し）',
    ))
    op.add_column('ope_order', sa.Column('deadline_offset_minutes', sa.Integer(), nullable=True, comment='OFFSET: 予定開始から締切までの分（写し）'))
    op.add_column('ope_order', sa.Column('deadline_time', sa.Time(), nullable=True, comment='FIXED_TIME: 締切のローカル時刻（写し）'))
    op.add_column('ope_order', sa.Column('deadline_day_offset', sa.Integer(), nullable=True, comment='FIXED_TIME: 予定日の何日後か（写し）'))
    op.add_column('ope_order', sa.Column('deadline_timezone', sa.String(length=64), nullable=True, comment='締切のローカル時刻のタイムゾーン（写し）'))
    op.add_column('ope_order', sa.Column(
        'warn_before_minutes', sa.Integer(), server_default=sa.text('0'), nullable=False,
        comment='締切の何分前に「まもなく」（写し。0 = 出さない）',
    ))
    op.add_column('ope_order', sa.Column('deadline_at', sa.DateTime(timezone=True), nullable=True, comment='締切'))
    op.add_column('ope_order', sa.Column('warn_at', sa.DateTime(timezone=True), nullable=True, comment='「まもなく」を出し始める日時（知らせなしは NULL）'))
    op.add_column('ope_order', sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True, comment='完了（受領）した日時（完了のときのみ）'))

    # 既存のオーダー: テンプレートはすべて「予定の終了」（N.1 の既定）なので、締切 = 予定の終了・知らせなし
    op.execute("""
        UPDATE ope_order o SET deadline_mode = 'END', deadline_timezone = t.timezone, deadline_at = o.scheduled_end
        FROM plan_template t WHERE t.id = o.template_id
    """)
    # 完了したオーダーの完了日時 = 最後の「完了」の記録の日時
    op.execute("""
        UPDATE ope_order o SET finished_at = e.at
        FROM (SELECT order_id, max(occurred_at) AS at FROM ope_order_event WHERE to_status = 'FINISHED' GROUP BY order_id) e
        WHERE e.order_id = o.id AND o.status = 'FINISHED'
    """)
    op.alter_column('ope_order', 'deadline_mode', nullable=False)
    op.alter_column('ope_order', 'deadline_timezone', nullable=False)
    op.alter_column('ope_order', 'deadline_at', nullable=False)
    op.create_check_constraint(
        'ck_ope_order_deadline_rule', 'ope_order',
        "(deadline_mode = 'OFFSET') = (deadline_offset_minutes IS NOT NULL) "
        "AND (deadline_mode = 'FIXED_TIME') = (deadline_time IS NOT NULL) "
        "AND (deadline_mode = 'FIXED_TIME') = (deadline_day_offset IS NOT NULL)",
    )
    op.create_check_constraint('ck_ope_order_warn_at', 'ope_order', "(warn_before_minutes = 0) = (warn_at IS NULL)")
    op.create_check_constraint('ck_ope_order_finished_at', 'ope_order', "(status = 'FINISHED') = (finished_at IS NOT NULL)")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('ck_ope_order_finished_at', 'ope_order', type_='check')
    op.drop_constraint('ck_ope_order_warn_at', 'ope_order', type_='check')
    op.drop_constraint('ck_ope_order_deadline_rule', 'ope_order', type_='check')
    for column in ('finished_at', 'warn_at', 'deadline_at', 'warn_before_minutes', 'deadline_timezone',
                   'deadline_day_offset', 'deadline_time', 'deadline_offset_minutes', 'deadline_mode'):
        op.drop_column('ope_order', column)
