"""sheet marker rename cascades to templates (sheet editing, E.1)

マーカーの名前を変えられるように、テンプレート → マーカーの複合 FK（シート・マーカー名）を ON UPDATE CASCADE にする。
名前を変えると、そのマーカーを参照するテンプレート（削除済みも）の reference_event_marker が同じ文で変わる。
削除は今までどおり RESTRICT（参照しているテンプレートがあるマーカーは消せない）。データは変えない。

Revision ID: 8c4d2e6f1a37
Revises: 3ff92f646c92
Create Date: 2026-11-11 18:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '8c4d2e6f1a37'
down_revision: str | Sequence[str] | None = '3ff92f646c92'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FK = 'fk_plan_template_sheet_marker'


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint(FK, 'plan_template', type_='foreignkey')
    op.create_foreign_key(
        FK, 'plan_template', 'reference_sheet_marker',
        ['reference_sheet_id', 'reference_event_marker'], ['sheet_id', 'marker'],
        ondelete='RESTRICT', onupdate='CASCADE',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(FK, 'plan_template', type_='foreignkey')
    op.create_foreign_key(
        FK, 'plan_template', 'reference_sheet_marker',
        ['reference_sheet_id', 'reference_event_marker'], ['sheet_id', 'marker'],
        ondelete='RESTRICT',
    )
