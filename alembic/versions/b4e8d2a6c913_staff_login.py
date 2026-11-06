"""staff login: passwords, sessions, api keys (LG.1)

staff_user にパスワード（ハッシュ）・仮パスワードの印・失敗回数・ロックを足し、
ログインのセッション（staff_session）と機械用の API キー（staff_api_key）の表を作る。
既存の職員はパスワード未設定（ログインできない）。scripts/set_password.py で設定する。

Revision ID: b4e8d2a6c913
Revises: 7a3c5e9b1d24
Create Date: 2026-11-06 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b4e8d2a6c913'
down_revision: Union[str, Sequence[str], None] = '7a3c5e9b1d24'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('staff_user', sa.Column('password_hash', sa.String(length=255), nullable=True,
                                          comment='パスワードのハッシュ（未設定ならログインできない）'))
    op.add_column('staff_user', sa.Column('must_change_password', sa.Boolean(), server_default=sa.text('false'), nullable=False,
                                          comment='システム管理者が設定した仮パスワード: 次のログインで本人が変えるまで、ほかの操作はできない'))
    op.add_column('staff_user', sa.Column('failed_logins', sa.Integer(), server_default=sa.text('0'), nullable=False,
                                          comment='続けて失敗したログインの回数（成功で 0）'))
    op.add_column('staff_user', sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True,
                                          comment='この時刻までログインできない（失敗が続いたとき）'))
    op.add_column('staff_user', sa.Column('password_changed_at', sa.DateTime(timezone=True), nullable=True,
                                          comment='パスワードを最後に設定・変更した日時'))

    op.create_table(
        'staff_session',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('staff_user_id', sa.Integer(), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False, comment='トークンの SHA-256（16 進）'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False, comment='最後に使われた日時（ここから 12 時間で切れる）'),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False, comment='最長の期限（作成から 7 日）'),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True, comment='ログアウト・停止・パスワード変更で終えた日時'),
        sa.ForeignKeyConstraint(['staff_user_id'], ['staff_user.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token_hash'),
    )
    op.create_index(op.f('ix_staff_session_staff_user_id'), 'staff_session', ['staff_user_id'], unique=False)

    op.create_table(
        'staff_api_key',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('staff_user_id', sa.Integer(), nullable=False),
        sa.Column('label', sa.String(length=100), nullable=False, comment='用途（例: SFTP 監視）'),
        sa.Column('key_prefix', sa.String(length=16), nullable=False, comment='キーの先頭（一覧で見分ける用）'),
        sa.Column('key_hash', sa.String(length=64), nullable=False, comment='キーの SHA-256（16 進）'),
        sa.Column('created_by_id', sa.Integer(), nullable=True, comment='発行した職員'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_by_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['staff_user_id'], ['staff_user.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['created_by_id'], ['staff_user.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['revoked_by_id'], ['staff_user.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('key_hash'),
    )
    op.create_index(op.f('ix_staff_api_key_staff_user_id'), 'staff_api_key', ['staff_user_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_staff_api_key_staff_user_id'), table_name='staff_api_key')
    op.drop_table('staff_api_key')
    op.drop_index(op.f('ix_staff_session_staff_user_id'), table_name='staff_session')
    op.drop_table('staff_session')
    op.drop_column('staff_user', 'password_changed_at')
    op.drop_column('staff_user', 'locked_until')
    op.drop_column('staff_user', 'failed_logins')
    op.drop_column('staff_user', 'must_change_password')
    op.drop_column('staff_user', 'password_hash')
