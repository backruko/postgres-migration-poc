"""plan revert one-off ui metadata

Revision ID: 34bdbd214739
Revises: ceee1634d6a7
Create Date: 2026-10-21 11:50:01.122640

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '34bdbd214739'
down_revision: Union[str, Sequence[str], None] = 'ceee1634d6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

IMMUTABLE_TABLES = ('plan_instance', 'plan_instance_void', 'plan_slot_release')

origin_enum = postgresql.ENUM('GENERATED', 'OVERRIDE', 'ONE_OFF', name='plan_instance_origin_enum', create_type=False)


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()

    # --- 1. 無効化理由に REVERTED を追加 ---
    op.execute("ALTER TYPE plan_void_reason_enum ADD VALUE IF NOT EXISTS 'REVERTED'")

    # --- 2. インスタンスの生成元（既存行は overrides_instance_id から判定してバックフィル） ---
    origin_enum.create(bind, checkfirst=True)
    op.add_column('plan_instance', sa.Column('origin', origin_enum, nullable=True))
    # 不変トリガーが存在する環境でもバックフィルできるよう、一時的にユーザートリガーを無効化
    op.execute("ALTER TABLE plan_instance DISABLE TRIGGER USER")
    op.execute("""
        UPDATE plan_instance
        SET origin = CASE WHEN overrides_instance_id IS NULL THEN 'GENERATED' ELSE 'OVERRIDE' END::plan_instance_origin_enum
    """)
    op.execute("ALTER TABLE plan_instance ENABLE TRIGGER USER")
    op.alter_column('plan_instance', 'origin', nullable=False)
    op.create_check_constraint(
        'ck_plan_instance_override_link', 'plan_instance',
        "(origin = 'OVERRIDE') = (overrides_instance_id IS NOT NULL)",
    )

    # 一意制約の対象を「ルール生成分のみ」に変更（単発追加もオーバーライド同様に対象外）
    op.drop_index('uq_plan_instance_occurrence', table_name='plan_instance', postgresql_where=sa.text('overrides_instance_id IS NULL'))
    op.create_index(
        'uq_plan_instance_occurrence', 'plan_instance', ['template_id', 'template_revision', 'scheduled_start'],
        unique=True, postgresql_where=sa.text("origin = 'GENERATED'"),
    )

    op.add_column('plan_occurrence_cache', sa.Column('origin', origin_enum, nullable=True))
    op.execute("""
        UPDATE plan_occurrence_cache
        SET origin = CASE WHEN is_override THEN 'OVERRIDE' ELSE 'GENERATED' END::plan_instance_origin_enum
    """)
    op.alter_column('plan_occurrence_cache', 'origin', nullable=False)

    # --- 3. 表示用メタデータ ---
    op.add_column('plan_template', sa.Column('ui_metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='カレンダー表示用スタイル（インスタンス側で上書き可）'))
    op.add_column('plan_instance', sa.Column('ui_metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='表示用スタイルの上書き'))
    op.add_column('plan_occurrence_cache', sa.Column('ui_metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='表示用スタイルの実効値（テンプレート+インスタンス）'))

    # --- 4. 手動判断の解除記録 ---
    op.create_table('plan_slot_release',
    sa.Column('root_instance_id', sa.Integer(), nullable=False, comment='手動判断の起点インスタンス'),
    sa.Column('template_id', sa.Integer(), nullable=False),
    sa.Column('note', sa.Text(), nullable=True, comment='解除の理由'),
    sa.Column('released_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['root_instance_id'], ['plan_instance.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['template_id'], ['plan_template.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('root_instance_id')
    )
    op.create_index(op.f('ix_plan_slot_release_template_id'), 'plan_slot_release', ['template_id'], unique=False)

    # --- 5. 不変性の強制（初期化マイグレーションに含まれていないため、ここで作成・置き換え） ---
    op.execute("""
        CREATE OR REPLACE FUNCTION plan_reject_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION USING MESSAGE = TG_TABLE_NAME || ' is immutable';
        END;
        $$ LANGUAGE plpgsql
    """)
    for table in IMMUTABLE_TABLES:
        op.execute(
            f"CREATE OR REPLACE TRIGGER trg_{table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION plan_reject_mutation()"
        )


def downgrade() -> None:
    """Downgrade schema."""
    # ※ ENUM値 REVERTED は PostgreSQL の制約上削除できないため残します
    # ※ 単発追加（ONE_OFF）のインスタンスが存在すると、元の一意制約の復元に失敗する場合があります
    for table in IMMUTABLE_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_immutable ON {table}")
    op.execute("DROP FUNCTION IF EXISTS plan_reject_mutation()")

    op.drop_index(op.f('ix_plan_slot_release_template_id'), table_name='plan_slot_release')
    op.drop_table('plan_slot_release')

    op.drop_column('plan_occurrence_cache', 'ui_metadata')
    op.drop_column('plan_instance', 'ui_metadata')
    op.drop_column('plan_template', 'ui_metadata')

    op.drop_column('plan_occurrence_cache', 'origin')
    op.drop_index('uq_plan_instance_occurrence', table_name='plan_instance', postgresql_where=sa.text("origin = 'GENERATED'"))
    op.create_index(
        'uq_plan_instance_occurrence', 'plan_instance', ['template_id', 'template_revision', 'scheduled_start'],
        unique=True, postgresql_where=sa.text('overrides_instance_id IS NULL'),
    )
    op.drop_constraint('ck_plan_instance_override_link', 'plan_instance', type_='check')
    op.drop_column('plan_instance', 'origin')
    origin_enum.drop(op.get_bind(), checkfirst=True)
