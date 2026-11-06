import enum
from datetime import datetime

from sqlalchemy import DDL, Boolean, DateTime, Enum, ForeignKey, Integer, String, event, text
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
    SIGNAL_SENDER = "signal_sender"  # 信号の送信: 顧客側の出来事の信号（POST /ope/signals）を送る仕組み用（Phase 7）


# role テーブルの初期データ（マイグレーションにも同じ内容があります。ここは create_all（テスト等）用です）
ROLE_SEED: list[dict[str, str]] = [
    {"code": RoleCode.OPERATOR.value, "name": "作業者", "description": "オーダーの開始・ステップの提出"},
    {"code": RoleCode.CHECKER.value, "name": "確認者", "description": "ステップの承認・差し戻し（提出者とは別の人）"},
    {"code": RoleCode.RELEASER.value, "name": "リリース担当", "description": "オーダーのリリース（手動承認の業務）"},
    {"code": RoleCode.ADMIN.value, "name": "業務管理者", "description": "業務の設定・判断が必要な操作"},
    {"code": RoleCode.VIEWER.value, "name": "閲覧者", "description": "参照のみ"},
    {"code": RoleCode.AUDITOR.value, "name": "監査担当", "description": "参照のみ（履歴・監査記録を含む）"},
    {"code": RoleCode.SYSTEM_ADMIN.value, "name": "システム管理者", "description": "職員アカウントの管理（業務の操作は含まない）"},
    {"code": RoleCode.SIGNAL_SENDER.value, "name": "信号の送信", "description": "顧客側の出来事の信号を送る（受け取り側の仕組み用）"},
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

    # --- ログイン（LG.1）。パスワードはハッシュだけを持つ（scrypt。src/domain_staff/credentials.py） ---
    password_hash: Mapped[str | None] = mapped_column(String(255), comment="パスワードのハッシュ（未設定ならログインできない）")
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false"),
        comment="システム管理者が設定した仮パスワード: 次のログインで本人が変えるまで、ほかの操作はできない",
    )
    failed_logins: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"), comment="続けて失敗したログインの回数（成功で 0）"
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="この時刻までログインできない（失敗が続いたとき）")
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="パスワードを最後に設定・変更した日時")

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


class StaffSession(Base):
    """
    ログインのセッション（LG.1）。ブラウザの Cookie には推測できない値（トークン）を入れ、ここにはその SHA-256 だけを持つ。
    最後の利用から 12 時間で切れ、最長でも作成から 7 日（expires_at）。ログアウト・利用停止・パスワード変更で revoked_at を付ける。
    """
    __tablename__ = "staff_session"

    id: Mapped[int] = mapped_column(primary_key=True)
    staff_user_id: Mapped[int] = mapped_column(ForeignKey("staff_user.id", ondelete="RESTRICT"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, comment="トークンの SHA-256（16 進）")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), comment="最後に使われた日時（ここから 12 時間で切れる）")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), comment="最長の期限（作成から 7 日）")
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="ログアウト・停止・パスワード変更で終えた日時")


class StaffApiKey(Base):
    """
    機械（信号を送るスクリプトなど）用の API キー（LG.1）。信号の送信（signal_sender）の職員にだけ発行する。
    キーは発行のときに一度だけ見せ、ここには SHA-256 と、見分けるための先頭の数文字だけを持つ。取り消すと使えない。
    """
    __tablename__ = "staff_api_key"

    id: Mapped[int] = mapped_column(primary_key=True)
    staff_user_id: Mapped[int] = mapped_column(ForeignKey("staff_user.id", ondelete="RESTRICT"), index=True)
    label: Mapped[str] = mapped_column(String(100), comment="用途（例: SFTP 監視）")
    key_prefix: Mapped[str] = mapped_column(String(16), comment="キーの先頭（一覧で見分ける用）")
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, comment="キーの SHA-256（16 進）")
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("staff_user.id", ondelete="SET NULL"), comment="発行した職員")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by_id: Mapped[int | None] = mapped_column(ForeignKey("staff_user.id", ondelete="SET NULL"))


# create_all（テスト等）でロールの初期データを入れる（本番はマイグレーションで入れる）
event.listen(
    Role.__table__,
    "after_create",
    DDL(
        "INSERT INTO role (code, name, description) VALUES "
        + ", ".join(f"('{r['code']}', '{r['name']}', '{r['description']}')" for r in ROLE_SEED)
    ),
)
