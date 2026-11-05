import enum
from datetime import date, datetime, time
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
    Time,
    event,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from src.domain_biz.models import DependencyKind, DependencyMatchRule
from src.domain_plan.models import DeadlineMode
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
    DEPENDENCY_OVERRIDE_APPROVED = "dependency_override_approved"  # 満たされていない依存関係のまま開始することを承認（Phase 6）


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
    RETIRED = "retired"              # 作り直しで外れた（開始前のオーダーの矛盾を、plan に合わせて解決したとき）


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

    # --- 締切（N.2、2026-11-05 の決定）: SLA の正。作成時にテンプレートの決め方を写し、締切を保存する ---
    # テンプレートの後の変更では変わらない（リリース前のオーダーだけ写し直す: N.3）。
    # 予定が動いたら、このオーダー自身の写した決め方で締切を計算し直す（domain_ope/deadlines.py）
    deadline_mode: Mapped[DeadlineMode] = mapped_column(
        Enum(DeadlineMode, name="plan_deadline_mode_enum"), nullable=False, comment="締切の決め方（テンプレートの写し）",
    )
    deadline_offset_minutes: Mapped[int | None] = mapped_column(comment="OFFSET: 予定開始から締切までの分（写し）")
    deadline_time: Mapped[time | None] = mapped_column(Time, comment="FIXED_TIME: 締切のローカル時刻（写し）")
    deadline_day_offset: Mapped[int | None] = mapped_column(comment="FIXED_TIME: 予定日の何日後か（写し）")
    deadline_timezone: Mapped[str] = mapped_column(String(64), nullable=False, comment="締切のローカル時刻のタイムゾーン（写し）")
    warn_before_minutes: Mapped[int] = mapped_column(
        nullable=False, default=0, server_default=text("0"), comment="締切の何分前に「まもなく」（写し。0 = 出さない）"
    )
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="締切")
    warn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="「まもなく」を出し始める日時（知らせなしは NULL）")
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="完了（受領）した日時（完了のときのみ）")

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
        # 締切の写し: 決め方ごとに必要な項目だけ（テンプレートと同じ規則）。知らせがあるときだけ warn_at
        CheckConstraint(
            "(deadline_mode = 'OFFSET') = (deadline_offset_minutes IS NOT NULL) "
            "AND (deadline_mode = 'FIXED_TIME') = (deadline_time IS NOT NULL) "
            "AND (deadline_mode = 'FIXED_TIME') = (deadline_day_offset IS NOT NULL)",
            name="ck_ope_order_deadline_rule",
        ),
        CheckConstraint("(warn_before_minutes = 0) = (warn_at IS NULL)", name="ck_ope_order_warn_at"),
        # 完了したオーダーだけが完了日時を持つ
        CheckConstraint("(status = 'FINISHED') = (finished_at IS NOT NULL)", name="ck_ope_order_finished_at"),
        Index("ix_ope_order_start", "scheduled_start"),
        # 遅れ・まもなくの一覧（/ops/attention）: 完了・取消以外のオーダーの締切・「まもなく」の開始
        Index("ix_ope_order_open_deadline", "deadline_at", postgresql_where=text("status NOT IN ('FINISHED', 'CANCELED')")),
        Index("ix_ope_order_open_warn", "warn_at", postgresql_where=text("status NOT IN ('FINISHED', 'CANCELED') AND warn_at IS NOT NULL")),
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
# 開始前のオーダーの予定が動いて条件の結果が変わり、矛盾を「plan に合わせる」で解決したときだけ作り直す:
# 古い行は retired_at を付けて残し（記録があるので消さない）、新しい行を作る。一意性は外れていない行だけで見る。
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
    retired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="作り直しで外れた日時（外れていないステップは NULL）"
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        CheckConstraint("step_num > 0", name="ck_ope_step_num_positive"),
        # 1つのオーダーに、同じ番号・同じテンプレートのステップは1件（作り直しで外れたものは除く）
        Index("uq_ope_step_order_num", "order_id", "step_num", unique=True, postgresql_where=text("retired_at IS NULL")),
        Index(
            "uq_ope_step_order_template", "order_id", "step_template_id",
            unique=True, postgresql_where=text("retired_at IS NULL"),
        ),
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


# ==========================================
# OpeDependency（オーダー同士の依存関係。Phase 6）
# biz_dependency（業務間の定義）から、下流のオーダーごとに作る「この上流のオーダーを待つ」というつながり。
# 上流のオーダーがまだ無い（見つからない）ときは upstream_order_id が NULL の行（待ち先なし = 条件を満たさない）を置き、
# 上流のオーダーができたら埋める（6.3）。定義が削除されたら行は残して removed_at を付ける（記録として残す）。
# 種類・突き合わせの規則は作成時の写し（定義が無くなっても読める）。
# ==========================================
class OpeDependency(Base):
    __tablename__ = "ope_dependency"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    downstream_order_id: Mapped[int] = mapped_column(ForeignKey("ope_order.id", ondelete="RESTRICT"), index=True)
    upstream_order_id: Mapped[int | None] = mapped_column(
        ForeignKey("ope_order.id", ondelete="RESTRICT"), index=True, comment="待つ上流のオーダー（見つからない間は NULL）"
    )
    biz_dependency_id: Mapped[int | None] = mapped_column(
        ForeignKey("biz_dependency.id", ondelete="SET NULL"), index=True, comment="作成元の定義（定義が削除されたら NULL）"
    )
    upstream_biz_id: Mapped[int] = mapped_column(
        ForeignKey("biz_master.id", ondelete="RESTRICT"), comment="上流の業務（上流のオーダーが無い間も、何を待つかが分かるように）"
    )

    # --- 作成時の写し ---
    kind: Mapped[DependencyKind] = mapped_column(Enum(DependencyKind, name="biz_dependency_kind_enum"), nullable=False)
    match_rule: Mapped[DependencyMatchRule] = mapped_column(
        Enum(DependencyMatchRule, name="biz_dependency_match_rule_enum"), nullable=False
    )
    match_sheet_event_id: Mapped[int | None] = mapped_column(Integer, comment="突き合わせたシート日付（same_sheet_event）")
    match_date: Mapped[date | None] = mapped_column(Date, comment="突き合わせたルール上の日付（same_occurrence_date）")

    # --- 外れた（定義の削除・発生枠の変更など。行は記録として残す） ---
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="外れた日時（有効な行は NULL）")
    removed_reason: Mapped[str | None] = mapped_column(Text, comment="外れた理由（removed_at があるときのみ）")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        CheckConstraint("upstream_order_id IS NULL OR upstream_order_id <> downstream_order_id", name="ck_ope_dependency_not_self"),
        CheckConstraint(
            "match_rule <> 'SAME_SHEET_EVENT' OR match_sheet_event_id IS NOT NULL", name="ck_ope_dependency_sheet_key"
        ),
        CheckConstraint(
            "match_rule <> 'SAME_OCCURRENCE_DATE' OR match_date IS NOT NULL", name="ck_ope_dependency_date_key"
        ),
        CheckConstraint("(removed_at IS NULL) = (removed_reason IS NULL)", name="ck_ope_dependency_removed"),
        # 有効なつながりは、下流と上流の組み合わせごとに1件。上流が見つからない行は、下流と上流の業務ごとに1件
        Index(
            "uq_ope_dependency_pair", "downstream_order_id", "upstream_order_id",
            unique=True, postgresql_where=text("removed_at IS NULL AND upstream_order_id IS NOT NULL"),
        ),
        Index(
            "uq_ope_dependency_waiting", "downstream_order_id", "upstream_biz_id",
            unique=True, postgresql_where=text("removed_at IS NULL AND upstream_order_id IS NULL"),
        ),
    )


# ==========================================
# OpeSignal（顧客側の出来事の信号。Phase 7）
# POST /ope/signals で受け取った信号をすべて残す（照合できなかったもの・重複も捨てない）。
# 照合の結果は受け取ったときに決まり、変えない。人が「対応済み」にする handled_* だけを一度だけ書ける（DB トリガー）。
# ==========================================
class SignalResult(str, enum.Enum):
    MATCHED = "matched"      # 対応するオーダーを完了にした
    DUPLICATE = "duplicate"  # 対応するオーダーは前の信号ですでに完了していた（何もしない）
    UNMATCHED = "unmatched"  # 対応する業務・オーダーが見つからない（人が対応する）
    AMBIGUOUS = "ambiguous"  # 対応するオーダーが複数ある（人が対応する）


class OpeSignal(Base):
    __tablename__ = "ope_signal"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    signal_type: Mapped[str] = mapped_column(String(50), nullable=False, comment="信号の種類（業務の signal_type と照合）")
    # 未登録の顧客コードの信号も捨てずに残すため、外部キーにしない
    client_code: Mapped[str] = mapped_column(String(10), nullable=False, comment="顧客コード（送られてきたまま）")
    key: Mapped[str] = mapped_column(String(100), nullable=False, comment="照合のキー（オーダーのルール上の日付 YYYY-MM-DD）")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="送信側が受け取った日時")
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), comment="記録した日時")
    sender_staff_id: Mapped[int] = mapped_column(
        ForeignKey("staff_user.id", ondelete="RESTRICT"), comment="送信した職員（signal_sender のアカウント）"
    )
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, comment="送信側の補足（ファイル名・件数など。照合には使わない）")

    # --- 照合の結果（受け取ったときに決まる） ---
    result: Mapped[SignalResult] = mapped_column(Enum(SignalResult, name="ope_signal_result_enum"), nullable=False)
    matched_order_id: Mapped[int | None] = mapped_column(
        ForeignKey("ope_order.id", ondelete="RESTRICT"), index=True, comment="対応したオーダー（matched / duplicate）"
    )
    result_note: Mapped[str | None] = mapped_column(Text, comment="照合の説明（見つからなかった理由など）")

    # --- 人の対応（照合できなかった信号を「対応済み」にする。一度だけ） ---
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    handled_by_staff_id: Mapped[int | None] = mapped_column(ForeignKey("staff_user.id", ondelete="RESTRICT"))
    handled_note: Mapped[str | None] = mapped_column(Text, comment="対応の内容（手動で完了にした・誤送信だった など）")

    __table_args__ = (
        CheckConstraint(
            "(result IN ('MATCHED', 'DUPLICATE')) = (matched_order_id IS NOT NULL)", name="ck_ope_signal_matched_order"
        ),
        CheckConstraint(
            "(handled_at IS NULL) = (handled_by_staff_id IS NULL) AND (handled_at IS NULL) = (handled_note IS NULL)",
            name="ck_ope_signal_handled",
        ),
        CheckConstraint(
            "handled_at IS NULL OR result IN ('UNMATCHED', 'AMBIGUOUS')", name="ck_ope_signal_handled_unmatched"
        ),
        Index("ix_ope_signal_open", "result", "handled_at"),
        Index("ix_ope_signal_client_type", "client_code", "signal_type"),
    )


# 信号の行は変えられない。handled_* だけを、まだ空のときに一度だけ書ける。削除はできない
# マイグレーションにも同じSQLがあります。ここは create_all（テスト等）用です。
OPE_SIGNAL_GUARD_FUNCTION = """
CREATE OR REPLACE FUNCTION ope_signal_guard() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION USING MESSAGE = 'ope_signal is immutable';
    END IF;
    IF OLD.handled_at IS NOT NULL
       OR (NEW.id, NEW.signal_type, NEW.client_code, NEW.key, NEW.received_at, NEW.recorded_at, NEW.sender_staff_id,
           NEW.payload, NEW.result, NEW.matched_order_id, NEW.result_note)
          IS DISTINCT FROM
          (OLD.id, OLD.signal_type, OLD.client_code, OLD.key, OLD.received_at, OLD.recorded_at, OLD.sender_staff_id,
           OLD.payload, OLD.result, OLD.matched_order_id, OLD.result_note) THEN
        RAISE EXCEPTION USING MESSAGE = 'ope_signal is immutable (only handled_* can be set, once)';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

event.listen(OpeSignal.__table__, "after_create", DDL(OPE_SIGNAL_GUARD_FUNCTION).execute_if(dialect="postgresql"))
event.listen(
    OpeSignal.__table__,
    "after_create",
    DDL(
        "CREATE TRIGGER trg_ope_signal_guard BEFORE UPDATE OR DELETE ON ope_signal "
        "FOR EACH ROW EXECUTE FUNCTION ope_signal_guard()"
    ).execute_if(dialect="postgresql"),
)
