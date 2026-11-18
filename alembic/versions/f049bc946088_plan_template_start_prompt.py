"""plan template start prompt (parallel run P.1)

テンプレートに「開始時刻に知らせる」（start_prompt）を足す。予定の開始の時刻に、人に「始めて」と知らせる作業か
（GET /ops/schedule-feed はオンの作業だけを出す。外からの合図を待つ作業はオフにする。2026-11-16 の決定）。
始めの値: 顧客側の出来事（実行のしかた = LIGHTWEIGHT: ファイルの受領など）の業務のテンプレートはオフ、ほかはオン。
合図を待つ後工程などは、並行稼働の間に業務管理者が画面でオフにする（鳴らないより、鳴りすぎのほうが安全: 2026-11-16 の決定）。

Revision ID: f049bc946088
Revises: 7a32fe73fa80
Create Date: 2026-11-16 13:58:34.580600

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f049bc946088'
down_revision: str | Sequence[str] | None = '7a32fe73fa80'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('plan_template', sa.Column('start_prompt', sa.Boolean(), server_default=sa.text('true'), nullable=False, comment='開始時刻に知らせる（外からの合図を待つ作業はオフ）'))
    # 顧客側の出来事（外からの合図を待つ）の業務のテンプレートはオフ
    op.execute(
        "UPDATE plan_template SET start_prompt = false "
        "WHERE biz_master_id IN (SELECT id FROM biz_master WHERE execution_type = 'LIGHTWEIGHT')"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('plan_template', 'start_prompt')
