import enum
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    CHAR,
    DDL,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Boolean,
    Integer,
    String,
    Text,
    event,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from src.infrastructure.database import Base


class OrderStatus(str, enum.Enum):
    """オーダーの状態。finished / canceled は最終状態"""
    PENDING = "pending"          # 作成済み・リリース前（plan の変更に追従する）
    READY = "ready"              # リリース済み・開始可能（SOP バージョンとステップを固定済み）
    IN_PROGRESS = "in_progress"  # 作業中（人が日程を変えることはできない）
    ON_HOLD = "on_hold"          # 保留（日付なし。再開すると保留前の状態に戻る）
    FINISHED = "finished"
    CANCELED = "canceled"


class OrderEventType(str, enum.Enum):
    """オーダーの記録の種類（追記のみ）"""
    CREATED = "created"                            # オーダーの作成（plan の発生枠から）
    STATUS_CHANGED = "status_changed"              # 状態の変更（リリース・開始・保留・再開・完了・取消）
    RESCHEDULED = "rescheduled"                    # plan の変更に追従して日時が動いた
    PINNED = "pinned"                              # SOP バージョンの指定
    UNPINNED = "unpinned"                          # SOP バージョンの指定の解除
    EARLY_START_APPROVED = "early_start_approved"  # 予定日より前の開始をリリース担当が承認
    CONFLICT_RAISED = "conflict_raised"            # plan の変更と矛盾（作業中に日時が動いた・発生枠が消えた等）
    CONFLICT_RESOLVED = "conflict_resolved"        # 矛盾の解決（新しい日時を採用・そのまま・取消）


class StepState(str, enum.Enum):
    """ステップの状態。approved が最終状態（差し戻しは submitted → todo）"""
    LOCKED = "locked"        # まだ開いていない（オーダーの開始・前のステップの提出 / 承認で開く）
    TODO = "todo"            # 作業できる（差し戻されたステップもここに戻る）
    SUBMITTED = "submitted"  # 作業者が提出した（チェック担当の承認待ち）
    APPROVED = "approved"    # チェック担当が承認した（提出者以外）


class StepEventType(str, enum.Enum):
    """ステップの記録の種類（追記のみ）"""
    CREATED = "created"              # ステップの作成（リリース時に、固定した SOP バージョンから）
    STATE_CHANGED = "state_changed"  # 状態の変更（開く・提出・承認・差し戻し）


class EventSource(str, enum.Enum):
    HUMAN = "human"    # 職員の操作（actor_staff_id 必須）
    SYSTEM = "system"  # システムの処理（plan への追従・自動リリース等。actor_staff_id は NULL）


# ==========================================
# OpeOrder（オーダー）
# 「この発生枠の業務を、この SOP バージョンで行う」という事実。plan の発生枠ごとに1件。
# plan はインスタンスを無効化・作り直すが、オーダーは同じ発生枠である限り残り続ける。
# ==========================================
class OpeOrder(Base):
    __tablename__ = "ope_order"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    # --- 発生枠の識別子（plan の _slot() と同じ規則） ---
    # シート由来は source_sheet_event_id（シートの日付が動いても同じ枠）、臨時（ONE_OFF）は one_off_instance_id、
    # それ以外は occurrence_date（ルール上のローカル日付）で枠を識別する。occurrence_date は常に持つ。
    template_id: Mapped[int] = mapped_column(ForeignKey("plan_template.id", ondelete="RESTRICT"), index=True)
    occurrence_date: Mapped[date] = mapped_column(Date, nullable=False, comment="発生枠のローカル日付（ルール上の日付）")
    source_sheet_event_id: Mapped[int | None] = mapped_column(
        Integer, comment="シート由来の発生枠: 算出元 reference_sheet_event.id（plan と同じくスナップショット。FK なし）"
    )
    one_off_instance_id: Mapped[int | None] = mapped_column(
        ForeignKey("plan_instance.id", ondelete="RESTRICT"),
        comment="臨時の予定の発生枠: 最初の単発インスタンス（日時を変えても、この ID で同じ枠を指す）",
    )

    # --- 現在の予定（plan の有効なインスタンスの写し） ---
    current_instance_id: Mapped[int] = mapped_column(
        ForeignKey("plan_instance.id", ondelete="RESTRICT"), comment="現在の plan インスタンス（発生枠が消えた後は最後のもの）"
    )
    biz_master_id: Mapped[int] = mapped_column(ForeignKey("biz_master.id", ondelete="RESTRICT"), index=True)
    # 顧客ごとの表示制限（予定: 顧客限定ロール）の絞り込み用に持つ
    client_code: Mapped[str] = mapped_column(CHAR(4), ForeignKey("client_master.code"), index=True)
    scheduled_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scheduled_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # --- 状態（最新の記録のキャッシュ。正は ope_order_event） ---
    # ※ SQLAlchemy の Enum はメンバー名を保存するため、条件式では 'ON_HOLD' 等の名前で比較する
    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus, name="ope_order_status_enum"), nullable=False, default=OrderStatus.PENDING,
        server_default=text("'PENDING'"),
    )
    status_before_hold: Mapped[OrderStatus | None] = mapped_column(
        Enum(OrderStatus, name="ope_order_status_enum"), comment="保留前の状態（再開で戻る。保留中のみ）"
    )

    # --- SOP バージョン ---
    pinned_sop_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("sop_version.id", ondelete="RESTRICT"), comment="人が指定したバージョン（未指定なら予定日に有効なもの）"
    )
    frozen_sop_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("sop_version.id", ondelete="RESTRICT"), comment="リリース時に固定したバージョン"
    )
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="リリース日時")

    # --- plan の変更との矛盾（人が解決するまで残す） ---
    conflict: Mapped[bool] = mapped_column(default=False, server_default=text("false"), comment="plan の変更と矛盾しているか")
    conflict_reason: Mapped[str | None] = mapped_column(Text, comment="矛盾の内容（conflict のときのみ）")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        # 1つの発生枠に、取消されていないオーダーは1件（枠の種類ごとの部分一意インデックス）。
        # 取消されたオーダーは履歴として残り、枠が戻ったとき（取消の復元・休日の解除など）は新しいオーダーを作る
        Index(
            "uq_ope_order_sheet_slot", "template_id", "source_sheet_event_id",
            unique=True, postgresql_where=text("source_sheet_event_id IS NOT NULL AND status <> 'CANCELED'"),
        ),
        Index(
            "uq_ope_order_one_off_slot", "template_id", "one_off_instance_id",
            unique=True, postgresql_where=text("one_off_instance_id IS NOT NULL AND status <> 'CANCELED'"),
        ),
        Index(
            "uq_ope_order_date_slot", "template_id", "occurrence_date",
            unique=True,
            postgresql_where=text(
                "source_sheet_event_id IS NULL AND one_off_instance_id IS NULL AND status <> 'CANCELED'"
            ),
        ),
        CheckConstraint(
            "source_sheet_event_id IS NULL OR one_off_instance_id IS NULL", name="ck_ope_order_one_slot_kind"
        ),
        CheckConstraint("scheduled_end > scheduled_start", name="ck_ope_order_period"),
        # 保留中だけが保留前の状態を持つ
        CheckConstraint(
            "(status = 'ON_HOLD') = (status_before_hold IS NOT NULL)", name="ck_ope_order_hold"
        ),
        CheckConstraint(
            "status_before_hold IS NULL OR status_before_hold IN ('PENDING', 'READY', 'IN_PROGRESS')",
            name="ck_ope_order_hold_from",
        ),
        CheckConstraint("NOT conflict OR conflict_reason IS NOT NULL", name="ck_ope_order_conflict_reason"),
        # リリース後（ready 以降）はリリース日時を持つ（pending のまま保留・取消されたものは持たない）
        CheckConstraint(
            "status NOT IN ('READY', 'IN_PROGRESS', 'FINISHED') OR released_at IS NOT NULL",
            name="ck_ope_order_released_at",
        ),
        Index("ix_ope_order_start", "scheduled_start"),
    )


# ==========================================
# OpeOrderEvent（オーダーの記録。追記のみ）
# 誰が（職員 or システム）・いつ・なぜ。状態の正はこの記録で、ope_order.status はそのキャッシュ。
# ==========================================
class OpeOrderEvent(Base):
    __tablename__ = "ope_order_event"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    # RESTRICT: 記録のあるオーダーは物理削除させない
    order_id: Mapped[int] = mapped_column(ForeignKey("ope_order.id", ondelete="RESTRICT"), index=True)
    event_type: Mapped[OrderEventType] = mapped_column(Enum(OrderEventType, name="ope_order_event_type_enum"), nullable=False)
    from_status: Mapped[OrderStatus | None] = mapped_column(Enum(OrderStatus, name="ope_order_status_enum"))
    to_status: Mapped[OrderStatus | None] = mapped_column(Enum(OrderStatus, name="ope_order_status_enum"))
    source: Mapped[EventSource] = mapped_column(Enum(EventSource, name="ope_event_source_enum"), nullable=False)
    actor_staff_id: Mapped[int | None] = mapped_column(
        ForeignKey("staff_user.id", ondelete="RESTRICT"), comment="操作した職員（システムの処理は NULL）"
    )
    reason: Mapped[str | None] = mapped_column(Text, comment="理由（取消・保留・強制開始・矛盾の解決などで人が入力）")
    detail: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, comment="種類ごとの詳細（例: 移動前後の日時、指定した SOP バージョン）"
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        # 人の操作は職員を、システムの処理は職員なしで記録する
        CheckConstraint("(source = 'HUMAN') = (actor_staff_id IS NOT NULL)", name="ck_ope_order_event_actor"),
        # 状態の変更は変更前後を、作成は作成時の状態を持つ
        CheckConstraint(
            "event_type <> 'STATUS_CHANGED' OR (from_status IS NOT NULL AND to_status IS NOT NULL)",
            name="ck_ope_order_event_status_change",
        ),
        CheckConstraint("event_type <> 'CREATED' OR to_status IS NOT NULL", name="ck_ope_order_event_created"),
        Index("ix_ope_order_event_order_at", "order_id", "occurred_at"),
    )


# ==========================================
# OpeStep（オーダーのステップ）
# リリース時に、固定した SOP バージョンのステップテンプレートのうち条件に合うものだけを作る（合わないものは作らない）。
# タイトル・内容・is_blocking は作成時の写し（SOP バージョン側が後で変わっても、このオーダーの手順は変わらない）。
# ==========================================
class OpeStep(Base):
    __tablename__ = "ope_step"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    # RESTRICT: ステップのあるオーダーは物理削除させない
    order_id: Mapped[int] = mapped_column(ForeignKey("ope_order.id", ondelete="RESTRICT"))
    step_template_id: Mapped[int] = mapped_column(
        ForeignKey("sop_step_template.id", ondelete="RESTRICT"), index=True, comment="作成元のステップテンプレート"
    )
    # テンプレートのステップ番号のまま（条件で作らなかったステップがあれば番号は飛ぶ。SOP の手順書と同じ番号）
    step_num: Mapped[int] = mapped_column(Integer, nullable=False, comment="ステップの実行順序（テンプレートの番号）")

    # --- 作成時の写し ---
    title: Mapped[str] = mapped_column(String(100), nullable=False, comment="ステップタイトル（作成時の写し）")
    content: Mapped[str | None] = mapped_column(Text, comment="作業手順・マニュアル内容（作成時の写し）")
    is_blocking: Mapped[bool] = mapped_column(
        Boolean, nullable=False, comment="承認されるまで次のステップを開けないか（作成時の写し）"
    )

    # --- 状態（最新の記録のキャッシュ。正は ope_step_event。誰が提出・承認したかも記録の側にだけ持つ） ---
    state: Mapped[StepState] = mapped_column(
        Enum(StepState, name="ope_step_state_enum"), nullable=False, default=StepState.LOCKED,
        server_default=text("'LOCKED'"),
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        CheckConstraint("step_num > 0", name="ck_ope_step_num_positive"),
        # 1つのオーダーに、同じ番号・同じテンプレートのステップは1件
        Index("uq_ope_step_order_num", "order_id", "step_num", unique=True),
        Index("uq_ope_step_order_template", "order_id", "step_template_id", unique=True),
    )


# ==========================================
# OpeStepEvent（ステップの記録。追記のみ）
# 誰が（職員 or システム）・いつ・なぜ。状態の正はこの記録で、ope_step.state はそのキャッシュ。
# 差し戻し = submitted → todo（理由が必須）。四つの目の確認（提出者 ≠ 承認者）はこの記録の職員で行う。
# ==========================================
class OpeStepEvent(Base):
    __tablename__ = "ope_step_event"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    # RESTRICT: 記録のあるステップは物理削除させない
    step_id: Mapped[int] = mapped_column(ForeignKey("ope_step.id", ondelete="RESTRICT"), index=True)
    event_type: Mapped[StepEventType] = mapped_column(Enum(StepEventType, name="ope_step_event_type_enum"), nullable=False)
    from_state: Mapped[StepState | None] = mapped_column(Enum(StepState, name="ope_step_state_enum"))
    to_state: Mapped[StepState | None] = mapped_column(Enum(StepState, name="ope_step_state_enum"))
    source: Mapped[EventSource] = mapped_column(Enum(EventSource, name="ope_event_source_enum"), nullable=False)
    actor_staff_id: Mapped[int | None] = mapped_column(
        ForeignKey("staff_user.id", ondelete="RESTRICT"), comment="操作した職員（システムの処理は NULL）"
    )
    reason: Mapped[str | None] = mapped_column(Text, comment="理由（差し戻しでは必須）")
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB, comment="種類ごとの詳細")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        # 人の操作は職員を、システムの処理は職員なしで記録する
        CheckConstraint("(source = 'HUMAN') = (actor_staff_id IS NOT NULL)", name="ck_ope_step_event_actor"),
        # 状態の変更は変更前後を、作成は作成時の状態を持つ
        CheckConstraint(
            "event_type <> 'STATE_CHANGED' OR (from_state IS NOT NULL AND to_state IS NOT NULL)",
            name="ck_ope_step_event_state_change",
        ),
        CheckConstraint("event_type <> 'CREATED' OR to_state IS NOT NULL", name="ck_ope_step_event_created"),
        # 差し戻し（submitted → todo）は理由が必須
        CheckConstraint(
            "NOT (from_state = 'SUBMITTED' AND to_state = 'TODO') OR reason IS NOT NULL",
            name="ck_ope_step_event_reject_reason",
        ),
        Index("ix_ope_step_event_step_at", "step_id", "occurred_at"),
    )


# ==========================================
# 不変性の強制（DBトリガー）
# マイグレーションにも同じSQLがあります。ここは create_all（テスト等）用です。
# ==========================================
OPE_REJECT_MUTATION_FUNCTION = """
CREATE OR REPLACE FUNCTION ope_reject_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION USING MESSAGE = TG_TABLE_NAME || ' is immutable';
END;
$$ LANGUAGE plpgsql
"""

event.listen(
    OpeOrderEvent.__table__, "after_create", DDL(OPE_REJECT_MUTATION_FUNCTION).execute_if(dialect="postgresql")
)
event.listen(
    OpeOrderEvent.__table__,
    "after_create",
    DDL(
        "CREATE TRIGGER trg_ope_order_event_immutable BEFORE UPDATE OR DELETE ON ope_order_event "
        "FOR EACH ROW EXECUTE FUNCTION ope_reject_mutation()"
    ).execute_if(dialect="postgresql"),
)

# create_all でテーブルの作成順は決まらないため、ステップの記録の側でも関数を作る（CREATE OR REPLACE なので二重でも可）
event.listen(
    OpeStepEvent.__table__, "after_create", DDL(OPE_REJECT_MUTATION_FUNCTION).execute_if(dialect="postgresql")
)
event.listen(
    OpeStepEvent.__table__,
    "after_create",
    DDL(
        "CREATE TRIGGER trg_ope_step_event_immutable BEFORE UPDATE OR DELETE ON ope_step_event "
        "FOR EACH ROW EXECUTE FUNCTION ope_reject_mutation()"
    ).execute_if(dialect="postgresql"),
)
