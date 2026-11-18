"""system_agent role (read-only API keys for agents, K.1)

ロール「システム連携（読み取り）」を加える。この職員の API キーは、日々のアラート（GET /ops/attention）・
日次の状況（GET /ops/daily）・自分の情報（GET /staff/me）だけを読める（src/domain_staff/auth.py の API_KEY_ROUTES_BY_ROLE）。
mfa のアダプターなど、無人で動く仕組みのため（セッションは 12 時間で切れるため使えない）。表は変えない。

Revision ID: 7d3a9c2e5f61
Revises: 5b7e1c9a2d48
Create Date: 2026-11-12 16:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '7d3a9c2e5f61'
down_revision: str | Sequence[str] | None = '5b7e1c9a2d48'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # src/domain_staff/models.py の ROLE_SEED と同じ内容
    op.execute(
        "INSERT INTO role (code, name, description) VALUES "
        "('system_agent', 'システム連携（読み取り）', 'API キーで日々のアラート・状況を読む（機械用。画面にはログインできない）') "
        "ON CONFLICT (code) DO NOTHING"
    )


def downgrade() -> None:
    """Downgrade schema."""
    # ロールを持つ職員がいると staff_role の外部キー（RESTRICT）で失敗する（先にロールを外す）
    op.execute("DELETE FROM role WHERE code = 'system_agent'")
