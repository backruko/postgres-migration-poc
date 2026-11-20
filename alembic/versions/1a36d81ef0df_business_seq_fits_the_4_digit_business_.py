"""business_seq fits the 4-digit business code

業務の連番を 9999 までに限る CHECK 制約を DB にも置く。業務コードは 顧客コード + LPAD(連番, 4) で、LPAD は 4 桁を
超える値を切り捨てる（10000 → '1000'）。画面と API はすでに 9999 までしか受けないが、DB に直接書くと黙って
間違ったコードができていた。既存の行は API からしか作られていないので、すべて 9999 以下（違反があれば移行が止まる）。

Revision ID: 1a36d81ef0df
Revises: 828ce0fa14df
Create Date: 2026-11-20 10:43:40.405727

"""
from collections.abc import Sequence

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '1a36d81ef0df'
down_revision: str | Sequence[str] | None = '828ce0fa14df'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_check_constraint('chk_business_seq_fits_code', 'biz_master', 'business_seq <= 9999')


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('chk_business_seq_fits_code', 'biz_master', type_='check')
