"""sop version states from dates (M2.1)

保存する状態を DRAFT / APPROVED の 2 つにし、予定 / 現行 / 過去は日付から決める。
これまでの FUTURE / ACTIVE / ARCHIVED は APPROVED になる（FUTURE が ACTIVE にならず、2 つ目の承認ができなかったため）。

データの整え方:
- 草稿は日付を持たない（effective_at / expired_at を NULL に）
- 発効日時のない承認済みは、今発効したものとする
- 承認済みの終了日時が無く、後に発効する承認済みの版があれば、その版の発効日時で終える（版をつなぐ）

Revision ID: 7a3c5e9b1d24
Revises: 51d8d829a2d2
Create Date: 2026-11-06 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '7a3c5e9b1d24'
down_revision: Union[str, Sequence[str], None] = '51d8d829a2d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. データを整える（新しい CHECK を満たすように）
    op.execute("UPDATE sop_version SET effective_at = NULL, expired_at = NULL WHERE status = 'DRAFT'")
    op.execute(
        "UPDATE sop_version SET effective_at = now() - interval '1 second' "
        "WHERE status <> 'DRAFT' AND effective_at IS NULL"
    )
    op.execute(
        "UPDATE sop_version SET expired_at = NULL "
        "WHERE status <> 'DRAFT' AND expired_at IS NOT NULL AND expired_at <= effective_at"
    )
    op.execute(
        """
        UPDATE sop_version v SET expired_at = n.next_effective
        FROM (
            SELECT id, LEAD(effective_at) OVER (PARTITION BY biz_master_id ORDER BY effective_at, version_number) AS next_effective
            FROM sop_version WHERE status <> 'DRAFT'
        ) n
        WHERE v.id = n.id AND v.expired_at IS NULL AND n.next_effective IS NOT NULL AND n.next_effective > v.effective_at
        """
    )

    # 2. ENUM を作り直す（DRAFT / APPROVED）
    op.execute("ALTER TYPE sop_status_enum RENAME TO sop_status_enum_old")
    op.execute("CREATE TYPE sop_status_enum AS ENUM ('DRAFT', 'APPROVED')")
    op.execute(
        "ALTER TABLE sop_version ALTER COLUMN status TYPE sop_status_enum USING "
        "(CASE WHEN status::text = 'DRAFT' THEN 'DRAFT' ELSE 'APPROVED' END)::sop_status_enum"
    )
    op.execute("DROP TYPE sop_status_enum_old")

    # 3. 草稿は日付なし・承認済みは発効日時あり、終了日時は発効日時より後
    op.create_check_constraint(
        'ck_sop_version_effective_iff_approved', 'sop_version', "(status = 'DRAFT') = (effective_at IS NULL)"
    )
    op.create_check_constraint(
        'ck_sop_version_expired_after', 'sop_version',
        "expired_at IS NULL OR (effective_at IS NOT NULL AND expired_at > effective_at)",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('ck_sop_version_expired_after', 'sop_version', type_='check')
    op.drop_constraint('ck_sop_version_effective_iff_approved', 'sop_version', type_='check')
    op.execute("ALTER TYPE sop_status_enum RENAME TO sop_status_enum_new")
    op.execute("CREATE TYPE sop_status_enum AS ENUM ('DRAFT', 'FUTURE', 'ACTIVE', 'ARCHIVED')")
    op.execute(
        "ALTER TABLE sop_version ALTER COLUMN status TYPE sop_status_enum USING (CASE "
        "WHEN status::text = 'DRAFT' THEN 'DRAFT' "
        "WHEN effective_at > now() THEN 'FUTURE' "
        "WHEN expired_at IS NOT NULL AND expired_at <= now() THEN 'ARCHIVED' "
        "ELSE 'ACTIVE' END)::sop_status_enum"
    )
    op.execute("DROP TYPE sop_status_enum_new")
