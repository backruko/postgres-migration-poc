"""
通知のドメイン: だれに（宛先）、何を（ルール）、知らせたか（通知と、宛先ごとの配信）。

- NotificationDestination: 知らせる先（チャネル + 宛先）。最初はメールだけ。秘密（パスワードなど）は持たず、その在りかの名前だけ
- NotificationRule: どの出来事を、どの顧客・業務について、どの宛先に、どの文面で知らせるか（rule_destination で宛先を選ぶ）
- Notification: 1 件のアラート（出来事 × ルール）。同じルールで同じ事実（dedupe_key）は 1 回だけ
- NotificationDelivery: 宛先ごとの配信（試みた回数・次の試み・最後の失敗）。同じ宛先には 1 回だけ届ける
ルール・宛先は、だれがいつ作り・変えたかを持ち、論理削除する（通知の記録がルール・宛先を指し続けるため）。
文面は当面コードにある（template_code で選ぶ）。出来事は src/domain_events の domain_event（アウトボックス）。
"""
import enum
from datetime import datetime, time

from sqlalchemy import (
    CHAR, Boolean, CheckConstraint, DateTime, Enum, ForeignKey, Index, Integer, String, Table, Column, Text, Time,
    UniqueConstraint, text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from src.core.mixin import SoftDeleteMixin
# 作った・変えた職員（staff_user）への外部キーを解決するため、職員の表を必ず読み込む。ワーカーやスクリプトのように
# ルーターを読まないプロセスでも、ルール・宛先を書けるように（読み込まないと書くときに失敗する）
import src.domain_staff.models  # noqa: F401
from src.infrastructure.database import Base


class NotificationChannel(str, enum.Enum):
    """届け方。最初はメールだけを作る（teams・webhook は場所だけ取っておく: 足すときにチャネルのアダプターを 1 つ足す）"""
    EMAIL = "email"
    TEAMS = "teams"
    WEBHOOK = "webhook"


class QuietMode(str, enum.Enum):
    """知らせない時間帯（quiet_start〜quiet_end）に起きた出来事を、どうするか"""
    HOLD = "hold"  # 時間帯が終わってから知らせる
    DROP = "drop"  # 知らせない（記録には「抑止」で残る）


class NotificationStatus(str, enum.Enum):
    PENDING = "pending"              # 配信待ち・配信中
    SENT = "sent"                    # すべての宛先に届けた
    PARTLY_FAILED = "partly_failed"  # 届かなかった宛先がある
    FAILED = "failed"                # どの宛先にも届かなかった
    SUPPRESSED = "suppressed"        # 知らせなかった（重複の期間内・知らせない時間帯など。理由を残す）


class DeliveryStatus(str, enum.Enum):
    PENDING = "pending"    # まだ送っていない
    RETRYING = "retrying"  # 一時的に失敗した（next_attempt_at にもう一度）
    SENT = "sent"          # 届けた
    FAILED = "failed"      # あきらめた（宛先の誤り・回数の上限）


class _WhoAndWhen:
    """設定（ルール・宛先）を、だれがいつ作り・変えたか（変更前後の履歴はまだ持たない）"""
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("staff_user.id", ondelete="SET NULL"), comment="作った職員")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("staff_user.id", ondelete="SET NULL"), comment="最後に変えた職員")


# ルール × 宛先（ルールが使う宛先）。宛先は論理削除するので、行は残っても記録を壊さない
rule_destination = Table(
    "notification_rule_destination",
    Base.metadata,
    Column("rule_id", ForeignKey("notification_rule.id", ondelete="CASCADE"), primary_key=True),
    Column("destination_id", ForeignKey("notification_destination.id", ondelete="RESTRICT"), primary_key=True, index=True),
)


class NotificationDestination(_WhoAndWhen, SoftDeleteMixin, Base):
    __tablename__ = "notification_destination"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="表示名（例: 運用チームのメールボックス）")
    channel: Mapped[NotificationChannel] = mapped_column(
        Enum(NotificationChannel, name="notification_channel_enum"), nullable=False, comment="届け方"
    )
    address: Mapped[str] = mapped_column(String(2000), nullable=False, comment="宛先（メールアドレス。後で Teams・webhook の URL）")
    secret_ref: Mapped[str | None] = mapped_column(
        String(100), comment="秘密の在りか（環境変数の名前など）。秘密そのものは持たない"
    )
    client_code: Mapped[str | None] = mapped_column(
        CHAR(4), ForeignKey("client_master.code", ondelete="RESTRICT"), comment="この顧客用（空 = 全顧客。顧客の範囲）"
    )
    owner: Mapped[str | None] = mapped_column(String(100), comment="持ち主のチーム")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    __table_args__ = (
        Index("uq_notification_destination_name", "name", unique=True, postgresql_where=text("NOT is_deleted")),
        Index("ix_notification_destination_client", "client_code"),
    )


class NotificationRule(_WhoAndWhen, SoftDeleteMixin, Base):
    __tablename__ = "notification_rule"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    event_types: Mapped[list[str]] = mapped_column(
        ARRAY(String(64)), nullable=False, comment="反応する出来事の種類（例: {order.due_soon, order.late}）"
    )
    client_code: Mapped[str | None] = mapped_column(
        CHAR(4), ForeignKey("client_master.code", ondelete="RESTRICT"), comment="この顧客の出来事だけ（空 = 全顧客）"
    )
    biz_master_id: Mapped[int | None] = mapped_column(
        ForeignKey("biz_master.id", ondelete="RESTRICT"), comment="この業務の出来事だけ（空 = すべての業務）"
    )
    template_code: Mapped[str] = mapped_column(String(64), nullable=False, comment="文面（コードにある。例: order.late）")
    dedupe_window_minutes: Mapped[int | None] = mapped_column(
        Integer, comment="同じ対象の通知を出さない分数（空 = 同じ事実は 1 回だけ、の決まりだけ）"
    )
    quiet_start: Mapped[time | None] = mapped_column(Time, comment="知らせない時間帯の始まり（日本時間）")
    quiet_end: Mapped[time | None] = mapped_column(Time, comment="知らせない時間帯の終わり（日本時間。日をまたいでよい）")
    quiet_mode: Mapped[QuietMode | None] = mapped_column(
        Enum(QuietMode, name="notification_quiet_mode_enum"), comment="その時間帯の出来事: 後で知らせる / 知らせない"
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    destinations: Mapped[list[NotificationDestination]] = relationship(
        secondary=rule_destination, order_by="NotificationDestination.id",
    )

    __table_args__ = (
        CheckConstraint("cardinality(event_types) > 0", name="ck_notification_rule_event_types"),
        CheckConstraint("dedupe_window_minutes IS NULL OR dedupe_window_minutes > 0", name="ck_notification_rule_dedupe_window"),
        # 知らせない時間帯は、始まり・終わり・扱いの 3 つをそろえて決める（どれか 1 つだけは不可）
        CheckConstraint(
            "(quiet_start IS NULL) = (quiet_end IS NULL) AND (quiet_start IS NULL) = (quiet_mode IS NULL)",
            name="ck_notification_rule_quiet_hours",
        ),
        Index("ix_notification_rule_event_types", "event_types", postgresql_using="gin"),
        Index("ix_notification_rule_client", "client_code"),
    )


class Notification(Base):
    __tablename__ = "notification"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("domain_event.id", ondelete="RESTRICT"), nullable=False, index=True, comment="もとの出来事"
    )
    # 空 = 宛先のテスト送信。ルールを通さず、頼まれた宛先だけに送る（届かなかったアラームの帯には出さない）
    rule_id: Mapped[int | None] = mapped_column(
        ForeignKey("notification_rule.id", ondelete="RESTRICT"), comment="当てはまったルール（空 = 宛先のテスト送信）"
    )
    dedupe_key: Mapped[str] = mapped_column(String(200), nullable=False, comment="同じ事実のキー（出来事のものを写す）")
    client_code: Mapped[str | None] = mapped_column(CHAR(4), comment="関わる顧客（出来事から写す。顧客の範囲での絞り込み用）")
    status: Mapped[NotificationStatus] = mapped_column(
        Enum(NotificationStatus, name="notification_status_enum"), nullable=False, default=NotificationStatus.PENDING,
    )
    suppressed_reason: Mapped[str | None] = mapped_column(String(200), comment="知らせなかった理由（status = suppressed のとき）")
    subject: Mapped[str] = mapped_column(String(200), nullable=False, comment="件名（日本語。チャネルによらない文面）")
    body: Mapped[str] = mapped_column(Text, nullable=False, comment="本文（日本語）")
    link: Mapped[str | None] = mapped_column(String(500), comment="画面へのリンク（例: そのオーダーの日次ダッシュボード）")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="配信が終わった日時（成功・失敗とも）")
    # 届かなかったアラームを、職員が「確認した」。確認するまで日次ダッシュボードに出続ける
    failure_acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="届かなかったことを職員が確認した日時（failed / partly_failed のときだけ）"
    )
    failure_acknowledged_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("staff_user.id", ondelete="SET NULL"), comment="確認した職員"
    )

    deliveries: Mapped[list[NotificationDelivery]] = relationship(back_populates="notification", order_by="NotificationDelivery.id")

    __table_args__ = (
        # 同じルールで同じ事実は 1 回だけ（ワーカーが止まって同じ出来事を読み直しても 2 通目を出さない）
        UniqueConstraint("rule_id", "dedupe_key", name="uq_notification_rule_dedupe"),
        CheckConstraint(
            "(status = 'SUPPRESSED') = (suppressed_reason IS NOT NULL)", name="ck_notification_suppressed_reason",
        ),
        Index("ix_notification_client_created", "client_code", "created_at"),
        Index("ix_notification_created", "created_at"),  # 通知の記録（新しい順）
        # 確認できるのは、届かなかった通知だけ
        CheckConstraint(
            "failure_acknowledged_at IS NULL OR status IN ('FAILED', 'PARTLY_FAILED')",
            name="ck_notification_failure_ack",
        ),
        # 日次ダッシュボードの「届かなかったアラーム」（まだ確認されていないもの）を、記録全体を読まずに引く
        Index(
            "ix_notification_unacknowledged_failure", "created_at",
            postgresql_where=text("status IN ('FAILED', 'PARTLY_FAILED') AND failure_acknowledged_at IS NULL"),
        ),
        Index(
            "ix_notification_failure", "completed_at", postgresql_where=text("status IN ('FAILED', 'PARTLY_FAILED')"),
        ),
    )


class NotificationDelivery(Base):
    __tablename__ = "notification_delivery"

    id: Mapped[int] = mapped_column(primary_key=True)
    notification_id: Mapped[int] = mapped_column(ForeignKey("notification.id", ondelete="RESTRICT"), nullable=False)
    destination_id: Mapped[int] = mapped_column(
        ForeignKey("notification_destination.id", ondelete="RESTRICT"), nullable=False, index=True,
    )
    status: Mapped[DeliveryStatus] = mapped_column(
        Enum(DeliveryStatus, name="notification_delivery_status_enum"), nullable=False, default=DeliveryStatus.PENDING,
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="次に送る日時（待ち・再試行のとき）")
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text, comment="最後の失敗の理由（チャネルが返したもの）")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="届けた日時")
    provider_message_id: Mapped[str | None] = mapped_column(String(200), comment="送り先の仕組みが返した ID（メールの Message-ID など）")

    notification: Mapped[Notification] = relationship(back_populates="deliveries")
    attempt_log: Mapped[list[NotificationDeliveryAttempt]] = relationship(
        back_populates="delivery", order_by="NotificationDeliveryAttempt.attempt_no",
    )

    __table_args__ = (
        # 同じ宛先には 1 回だけ届ける
        UniqueConstraint("notification_id", "destination_id", name="uq_notification_delivery_destination"),
        CheckConstraint("attempts >= 0", name="ck_notification_delivery_attempts"),
        CheckConstraint("(status = 'SENT') = (sent_at IS NOT NULL)", name="ck_notification_delivery_sent_at"),
        # 次に送るものを、記録全体を読まずに引く
        Index(
            "ix_notification_delivery_due", "next_attempt_at",
            postgresql_where=text("status IN ('PENDING', 'RETRYING')"),
        ),
    )


class NotificationDeliveryAttempt(Base):
    """
    配信の 1 回ごとの試み。送ったときのアドレスを残す: 宛先のアドレスを直すと再試行は新しいアドレスに
    送るので、どの回がどこへ送ったかを、後から確かめられるように。追記だけ
    """
    __tablename__ = "notification_delivery_attempt"

    id: Mapped[int] = mapped_column(primary_key=True)
    delivery_id: Mapped[int] = mapped_column(ForeignKey("notification_delivery.id", ondelete="RESTRICT"), nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False, comment="何回目か（1 から）")
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    address: Mapped[str] = mapped_column(String(2000), nullable=False, comment="この回に送ったアドレス（そのときの宛先のもの）")
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False, comment="届いたか")
    permanent: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"), comment="失敗が永続的か（もう試さない）")
    error: Mapped[str | None] = mapped_column(Text, comment="失敗の理由（相手のサーバーの返事のまま）")
    provider_message_id: Mapped[str | None] = mapped_column(String(200))

    delivery: Mapped[NotificationDelivery] = relationship(back_populates="attempt_log")

    __table_args__ = (
        UniqueConstraint("delivery_id", "attempt_no", name="uq_notification_delivery_attempt_no"),
        CheckConstraint("attempt_no >= 1", name="ck_notification_delivery_attempt_no"),
        CheckConstraint("ok = (error IS NULL)", name="ck_notification_delivery_attempt_error"),
    )
