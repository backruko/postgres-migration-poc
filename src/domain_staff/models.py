import enum
from datetime import datetime

from sqlalchemy import DDL, DateTime, Enum, ForeignKey, String, event
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from src.infrastructure.database import Base


class StaffStatus(str, enum.Enum):
    """
    職員アカウントの状態（ロールとは別）。active 以外の職員は、ロールを持っていても全ての操作を拒否されます。
    停止してもロールは残るため、再開（active に戻す）すれば元の権限で使えます。
    """
    PENDING = "pending"      # 登録済み・利用開始前（システム管理者が有効化するまで操作できない）
    ACTIVE = "active"
    SUSPENDED = "suspended"  # 利用停止


class RoleCode(str, enum.Enum):
    """ロールのコード（role テーブルの code と同じ。権限の判定はこのコードで行う）"""
    OPERATOR = "operator"          # 作業者: オーダーの開始・ステップの提出
    CHECKER = "checker"            # 確認者: ステップの承認・差し戻し（提出者とは別の人）
    RELEASER = "releaser"          # リリース担当: オーダーのリリース（手動承認の業務）
    ADMIN = "admin"                # 業務管理者: 業務の設定・判断が必要な操作
    VIEWER = "viewer"              # 閲覧者: 参照のみ
    AUDITOR = "auditor"            # 監査担当: 参照のみ（履歴・監査記録を含む）
    SYSTEM_ADMIN = "system_admin"  # システム管理者: 職員アカウントの管理（業務の操作は含まない）


# role テーブルの初期データ（マイグレーションにも同じ内容があります。ここは create_all（テスト等）用です）
ROLE_SEED: list[dict[str, str]] = [
    {"code": RoleCode.OPERATOR.value, "name": "作業者", "description": "オーダーの開始・ステップの提出"},
    {"code": RoleCode.CHECKER.value, "name": "確認者", "description": "ステップの承認・差し戻し（提出者とは別の人）"},
    {"code": RoleCode.RELEASER.value, "name": "リリース担当", "description": "オーダーのリリース（手動承認の業務）"},
    {"code": RoleCode.ADMIN.value, "name": "業務管理者", "description": "業務の設定・判断が必要な操作"},
    {"code": RoleCode.VIEWER.value, "name": "閲覧者", "description": "参照のみ"},
    {"code": RoleCode.AUDITOR.value, "name": "監査担当", "description": "参照のみ（履歴・監査記録を含む）"},
    {"code": RoleCode.SYSTEM_ADMIN.value, "name": "システム管理者", "description": "職員アカウントの管理（業務の操作は含まない）"},
]


class Role(Base):
    __tablename__ = "role"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    code: Mapped[str] = mapped_column(String(30), unique=True, comment="ロールコード（RoleCode）")
    name: Mapped[str] = mapped_column(String(50), comment="表示名")
    description: Mapped[str | None] = mapped_column(String(200), comment="できること")


class StaffUser(Base):
    __tablename__ = "staff_user"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    login_id: Mapped[str] = mapped_column(String(100), unique=True, comment="ログインID（社員番号・メールアドレス等）")
    name: Mapped[str] = mapped_column(String(100), comment="氏名")
    email: Mapped[str | None] = mapped_column(String(255), comment="メールアドレス")
    status: Mapped[StaffStatus] = mapped_column(
        Enum(StaffStatus, name="staff_status_enum"), nullable=False, default=StaffStatus.PENDING, comment="アカウントの状態"
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # 職員は削除しない（オーダー・ステップの記録が職員を参照するため）。使わなくなったら停止する
    # staff_role は granted_by_id でも staff_user を参照するため、結合条件を明示する
    roles: Mapped[list[Role]] = relationship(
        secondary="staff_role",
        primaryjoin="StaffUser.id == StaffRole.staff_user_id",
        secondaryjoin="Role.id == StaffRole.role_id",
        order_by="Role.id",
        viewonly=True,
    )

    @property
    def role_codes(self) -> list[RoleCode]:
        return [RoleCode(r.code) for r in self.roles]


class StaffRole(Base):
    """職員とロールの割り当て（1人が複数のロールを持てる。全顧客に共通）"""
    __tablename__ = "staff_role"

    staff_user_id: Mapped[int] = mapped_column(ForeignKey("staff_user.id", ondelete="CASCADE"), primary_key=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("role.id", ondelete="RESTRICT"), primary_key=True)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    granted_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("staff_user.id", ondelete="SET NULL"), comment="割り当てた職員（初期登録スクリプトでは NULL）"
    )


# create_all（テスト等）でロールの初期データを入れる（本番はマイグレーションで入れる）
event.listen(
    Role.__table__,
    "after_create",
    DDL(
        "INSERT INTO role (code, name, description) VALUES "
        + ", ".join(f"('{r['code']}', '{r['name']}', '{r['description']}')" for r in ROLE_SEED)
    ),
)
