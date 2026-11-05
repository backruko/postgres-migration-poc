"""biz dependency latest before

Revision ID: 2f7614f1cb02
Revises: 3242f180527a
Create Date: 2026-11-05 15:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2f7614f1cb02'
down_revision: Union[str, Sequence[str], None] = '3242f180527a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# biz_dependency と ope_dependency の両方が使う型
ENUM = "biz_dependency_match_rule_enum"
OLD_VALUES = "'SAME_SHEET_EVENT', 'SAME_OCCURRENCE_DATE'"


def upgrade() -> None:
    """Upgrade schema."""
    # 突き合わせの条件に「直前の上流」を追加（PostgreSQL 12 以降はトランザクション内で追加できる）
    op.execute(f"ALTER TYPE {ENUM} ADD VALUE IF NOT EXISTS 'LATEST_BEFORE'")
    op.add_column('biz_dependency', sa.Column('lookback_days', sa.Integer(), nullable=True))
    # 追加した値は同じトランザクションでは ENUM として使えないため、::text で比べる（モデルと同じ式）
    op.create_check_constraint(
        'ck_biz_dependency_lookback_rule', 'biz_dependency',
        "(match_rule::text = 'LATEST_BEFORE') = (lookback_days IS NOT NULL)",
    )
    op.create_check_constraint(
        'ck_biz_dependency_lookback_range', 'biz_dependency',
        "lookback_days IS NULL OR lookback_days BETWEEN 1 AND 366",
    )


def downgrade() -> None:
    """Downgrade schema."""
    # ENUM の値は削除できないため型を作り直す（LATEST_BEFORE の定義・つながりが残っていると失敗する）。
    # match_rule を参照する CHECK 制約は古い型に結び付いているため、外してから作り直す
    op.drop_constraint('ck_biz_dependency_lookback_range', 'biz_dependency', type_='check')
    op.drop_constraint('ck_biz_dependency_lookback_rule', 'biz_dependency', type_='check')
    op.drop_column('biz_dependency', 'lookback_days')
    op.drop_constraint('ck_ope_dependency_sheet_key', 'ope_dependency', type_='check')
    op.drop_constraint('ck_ope_dependency_date_key', 'ope_dependency', type_='check')
    op.execute(f"ALTER TYPE {ENUM} RENAME TO {ENUM}_old")
    op.execute(f"CREATE TYPE {ENUM} AS ENUM ({OLD_VALUES})")
    for table in ("biz_dependency", "ope_dependency"):
        op.execute(f"ALTER TABLE {table} ALTER COLUMN match_rule TYPE {ENUM} USING match_rule::text::{ENUM}")
    op.execute(f"DROP TYPE {ENUM}_old")
    op.create_check_constraint(
        'ck_ope_dependency_sheet_key', 'ope_dependency',
        "match_rule <> 'SAME_SHEET_EVENT' OR match_sheet_event_id IS NOT NULL",
    )
    op.create_check_constraint(
        'ck_ope_dependency_date_key', 'ope_dependency',
        "match_rule <> 'SAME_OCCURRENCE_DATE' OR match_date IS NOT NULL",
    )
