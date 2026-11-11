from datetime import datetime, time, timezone
from typing import Any
import enum
from datetime import datetime


from datetime import datetime

from sqlalchemy import (
    Boolean,
    Integer,
    String, 
    Text, 
    Enum, 
    DateTime, 
    CheckConstraint, 
    ForeignKey, 
    Index,  
    Computed,          
    CHAR,
    SmallInteger,
    Time,
    text
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from sqlalchemy.orm import validates, relationship

from src.infrastructure.database import Base
from src.core.mixin import SoftDeleteMixin


class ExecutionType(str, enum.Enum):
    """業務の実行のしかた（Phase 7）"""
    SOP = "sop"                  # SOP バージョンのステップで作業する（既定）
    LIGHTWEIGHT = "lightweight"  # ステップなし（顧客側の出来事: データの受領など）。完了は人の受領操作か信号


class CompletionMode(str, enum.Enum):
    """LIGHTWEIGHT の業務のオーダーを完了にする方法（SOP の業務はステップの承認で完了する）"""
    MANUAL = "manual"        # 作業者が受領を記録する（既定）
    AUTOMATIC = "automatic"  # 信号（POST /ope/signals）で完了する。signal_type が必須


class ReleaseMode(str, enum.Enum):
    """オーダーを pending から ready（実行可能）にする方法"""
    MANUAL_APPROVAL = "manual_approval"  # リリース担当・業務管理者が SOP とステップを確認して承認する
    AUTOMATIC = "automatic"              # 予定日の release_days_before 日前の release_time（日本時間）に自動で


class BizMaster(Base, SoftDeleteMixin):
    """
    業務マスタ(Biz_Master)
    
    不変的、または変更頻度の極めて低い「業務の基本情報（ビジネスの看板）」を管理するルートエンティティ。
    下位層となるSOPバージョン(SOP_Version)やSOPステップ(SOP_Step_Template)の親レコードとして機能する。
    """
    __tablename__ = "biz_master"

#　（N+1問題）対策
# bizes = session.query(BizMaster).options(selectinload(BizMaster.sop_versions)).all()

    sop_versions: Mapped[list["SOPVersion"]] = relationship(
        "SOPVersion",
        back_populates="biz_master",
        cascade="all, delete-orphan", # ➔ Python上で親を消した時、メモリ上の子も連動して削除扱いにする
        passive_deletes=True          # ➔ DB側の ON DELETE CASCADE に処理をお任せして高速化する設定
    )

    # --- Primary Key ---
    id: Mapped[int] = mapped_column(
        primary_key=True, 
        index=True, 
        comment="システム内部ID(サロゲートキー)"
    )
    
    # --- Business Keys & Foreign References ---
    client_code: Mapped[str] = mapped_column(
        CHAR(4), # 4文字固定
        ForeignKey("client_master.code"),  
        index=True, 
        comment="顧客コード(ClientMaster外部キー)"
    )

    business_seq: Mapped[int] = mapped_column(
        comment="業務連番（同一顧客内で一意。例: 1, 2, 101)"
    )

    # 自動計算される業務コード（DBが自動で組み立てる）
    # 例: client_code='ABCD', business_seq=1 -> 'ABCD0001'
    business_code: Mapped[str] = mapped_column(
        String(100), 
        Computed("client_code || LPAD(business_seq::text, 4, '0')", persisted=True),
        unique=True, 
        comment="業務コード (DB自動計算。例: ABCD0001)"
    )
       
    # --- Basic Attributes ---
    name: Mapped[str] = mapped_column(
        String(100), 
        comment="業務名称（例: 〇〇様向け日次照合業務）"
    )
    manager_email: Mapped[str | None] = mapped_column(
        String(100), 
        nullable=True, 
        comment="業務管理者のメールアドレス（異常時のエスカレーション先等を想定）"
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="業務の概要・補足説明"
    )

    # --- Release Settings（オーダーを実行可能にする方法。時刻は日本時間） ---
    # ※ SQLAlchemy の Enum はメンバー名を保存するため、server_default は 'MANUAL_APPROVAL' 等の名前で指定する
    release_mode: Mapped["ReleaseMode"] = mapped_column(
        Enum(ReleaseMode, name="biz_release_mode_enum"),
        nullable=False,
        default=ReleaseMode.MANUAL_APPROVAL,
        server_default=text("'MANUAL_APPROVAL'"),
        comment="リリース方法（MANUAL_APPROVAL: リリース担当・業務管理者が承認 / AUTOMATIC: 指定時刻に自動）"
    )
    release_time: Mapped[time] = mapped_column(
        Time,
        nullable=False,
        default=time(3, 0),
        server_default=text("'03:00:00'"),
        comment="自動リリースの時刻（日本時間。既定 03:00 = 夜間バッチの後）"
    )
    release_days_before: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
        comment="自動リリースを予定日の何日前に行うか（0 = 予定日当日）"
    )

    # --- 顧客側の出来事（Phase 7）。AUTOMATIC は LIGHTWEIGHT のみ・signal_type 必須（顧客ごとに一意） ---
    execution_type: Mapped["ExecutionType"] = mapped_column(
        Enum(ExecutionType, name="biz_execution_type_enum"),
        nullable=False,
        default=ExecutionType.SOP,
        server_default=text("'SOP'"),
        comment="実行のしかた（SOP: ステップで作業 / LIGHTWEIGHT: ステップなし・顧客側の出来事）"
    )
    completion_mode: Mapped["CompletionMode"] = mapped_column(
        Enum(CompletionMode, name="biz_completion_mode_enum"),
        nullable=False,
        default=CompletionMode.MANUAL,
        server_default=text("'MANUAL'"),
        comment="完了のしかた（MANUAL: 作業者が受領を記録 / AUTOMATIC: 信号で完了。LIGHTWEIGHT のみ）"
    )
    signal_type: Mapped[str | None] = mapped_column(
        String(50), nullable=True, comment="完了の信号の種類（AUTOMATIC のとき必須。同じ顧客の業務どうしで一意）"
    )

    # --- System Audit Trail ---
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        server_default=func.now(), 
        comment="レコード作成日時"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        server_default=func.now(), 
        onupdate=func.now(), 
        comment="レコード最終更新日時"
    )


    __table_args__ = (
        # 1. 組み合わせユニーク：同一クライアント内で同じ連番は絶対に使わせない
        Index(
            'uq_client_business_seq_alive',  
            'client_code', 
            'business_seq', 
            unique=True,
            postgresql_where=text("deleted_at IS NULL")
        ),

        #  business_code も未削除のデータの中だけでユニークにする
        Index(
            'uq_business_code_alive',
            'business_code', 
            unique=True,
            postgresql_where=text("deleted_at IS NULL")
        ),
        # 2. 値域チェック：連番は必ず1以上の正の整数（0や負の数はDBが弾く）
        CheckConstraint(
            'business_seq > 0',
            name='chk_business_seq_positive'
        ),
        # 3. 自動リリースは予定日の当日〜30日前まで
        CheckConstraint(
            'release_days_before BETWEEN 0 AND 30',
            name='chk_biz_release_days_before'
        ),
        # 4. 信号での完了は LIGHTWEIGHT の業務のみ。信号の種類は信号で完了する業務だけが持つ
        CheckConstraint(
            "completion_mode <> 'AUTOMATIC' OR execution_type = 'LIGHTWEIGHT'",
            name='chk_biz_automatic_completion_lightweight'
        ),
        CheckConstraint(
            "(completion_mode = 'AUTOMATIC') = (signal_type IS NOT NULL)",
            name='chk_biz_signal_type'
        ),
        # 5. 信号の種類は、同じ顧客の未削除の業務どうしで一意（1つの信号が見つける業務は1つ）
        Index(
            'uq_biz_client_signal_type_alive',
            'client_code',
            'signal_type',
            unique=True,
            postgresql_where=text("signal_type IS NOT NULL AND deleted_at IS NULL")
        ),
    )

    @validates('client_code')
    def validate_client_code(self, key, value):
        if value is not None:
            return value.strip().upper() 
        return value


# ==========================================
# 🔄 1. SOPStatus 定義（Enum クラス）
# ==========================================
class SOPStatus(str, enum.Enum):
    """
    SOPバージョンの保存される状態（M2.1）。草稿か承認済みかだけを持つ。
    承認済みの「予定 / 現行 / 過去」は effective_at / expired_at と現在日時から決まる（SopState・SOPVersion.state）。
    """
    DRAFT = "draft"          # 草稿：編集できる。オーダーには使われない（複数並行可）
    APPROVED = "approved"    # 承認済み：内容は変えられない。effective_at ≤ 予定日時 < expired_at のオーダーに使われる


class SopState(str, enum.Enum):
    """画面に出すバージョンの状態（保存しない。日付から決まる）"""
    DRAFT = "draft"          # 草稿
    SCHEDULED = "scheduled"  # 予定：承認済みで、まだ発効していない（業務ごとに最大1つ）
    CURRENT = "current"      # 現行：今有効
    PAST = "past"            # 過去：次の版の発効で終わった


# ==========================================
# 📊 2. SOPVersion モデル
# ==========================================
class SOPVersion(Base):
    __tablename__ = "sop_version"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    biz_master_id: Mapped[int] = mapped_column(ForeignKey("biz_master.id", ondelete="CASCADE"), nullable=False)
    
    # 1. 内部用：確実なソートとDB一意性のための整数連番 (1, 2, 3...)
    # 人間には見せず、システムが自動で「最大値 + 1」で発番し続けます。
    version_number: Mapped[int] = mapped_column(
        nullable=False, 
        comment="内部バージョン連番（ソート・最新判定用。例: 1, 2, 3)"
    )
    
    # 2. 外部用：ユーザーが自由に入力する直感的なセマンティックタグ (v1.0.0, v2.1.0-draft-teamA...)
    # Pydantic（スキーマ層）のバリデーションを通過した綺麗な文字列がここに格納されます。
    version_tag: Mapped[str] = mapped_column(
        String(50),  # 👈 サフィックスを考慮して文字数を20から50に拡張
        nullable=False, 
        comment="表示用バージョンタグ（例: v1.0.0, v1.1.0-draft-teamA)"
    )

    # 3. 状態管理：String から SQLAlchemy の Enum 型（SOPStatus）にアップグレード
    status: Mapped[SOPStatus] = mapped_column(
        Enum(SOPStatus, name="sop_status_enum"), 
        default=SOPStatus.DRAFT, 
        nullable=False
    )

    # 4. スケジュール自動切り替え用の期間フィールド
    effective_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), 
        nullable=True, 
        comment="有効開始日時（未来実装のバージョンはここを指定）"
    )
    
    expired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), 
        nullable=True, 
        comment="有効終了日時（この日時を過ぎたら自動で無効化）"
    )
    # 誰が作った・直した・承認を依頼した（P.1、2026-11-11。リリース担当も草稿を書く。これより前の版は NULL）
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("staff_user.id", ondelete="RESTRICT"), comment="版（草稿）を作った職員"
    )
    updated_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("staff_user.id", ondelete="RESTRICT"), comment="草稿（タグ・ステップ）を最後に直した職員"
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="草稿を最後に直した日時")
    # 承認の依頼: 依頼中の草稿は直せない（取り下げ・差し戻しで草稿に戻る。承認で外れる）
    submitted_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("staff_user.id", ondelete="RESTRICT"), comment="承認を依頼した職員（依頼中のときだけ）"
    )
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="承認を依頼した日時（依頼中のときだけ。承認待ちの一覧の並び）"
    )

    # リレーション
    biz_master: Mapped["BizMaster"] = relationship("BizMaster", back_populates="sop_versions")
    
    # 下位のステップテンプレートとのリレーション設定
    step_templates: Mapped[list["SOPStepTemplate"]] = relationship(
        "SOPStepTemplate",
        back_populates="sop_version",
        cascade="all, delete-orphan",
        passive_deletes=True          
    )

    __table_args__ = (
        # version_number は必ず1以上の正の整数
        CheckConstraint("version_number > 0", name="chk_sop_version_number_positive"),
        
        # 💡【解拘：修正点】
        # 人間が自由にタグを入力できるようにするため、以前存在した
        # ガチガチのフォーマット強制チェック制約（chk_sop_version_tag_format）を完全に削除しました！
        
        # 同一業務内では、内部連番は絶対に重複させない
        # これが裏側で「最大値 + 1」の自動発番と組み合わさり、データの堅牢性を完璧に担保します。
        Index("uq_biz_version_number", "biz_master_id", "version_number", unique=True),
        
        # 同一業務内では、外部タグ（例：v1.1.0-draft-teamA）も絶対に重複させない
        # 人間の入力ミスによって全く同じ名前の草稿が作られるのを防ぎます。
        Index("uq_biz_version_tag", "biz_master_id", "version_tag", unique=True),

        # 草稿は日付を持たない。承認済みは発効日時を持ち、終了日時（次の版の発効日時）はその後（M2.1）
        CheckConstraint(
            "(status = 'DRAFT') = (effective_at IS NULL)", name="ck_sop_version_effective_iff_approved"
        ),
        CheckConstraint(
            "expired_at IS NULL OR (effective_at IS NOT NULL AND expired_at > effective_at)", name="ck_sop_version_expired_after"
        ),
        # 承認の依頼は草稿だけ。依頼した人と日時はそろって持つ
        CheckConstraint(
            "(submitted_at IS NULL) = (submitted_by_id IS NULL) AND (status = 'DRAFT' OR submitted_at IS NULL)",
            name="ck_sop_version_submitted",
        ),
        # 承認待ちの一覧（依頼中の草稿だけ）
        Index("ix_sop_version_submitted", "submitted_at", postgresql_where=text("submitted_at IS NOT NULL")),
    )

    def state_at(self, now: datetime) -> SopState:
        """その時点での状態（草稿 / 予定 / 現行 / 過去）"""
        if self.status == SOPStatus.DRAFT or self.effective_at is None:
            return SopState.DRAFT
        if self.effective_at > now:
            return SopState.SCHEDULED
        if self.expired_at is not None and self.expired_at <= now:
            return SopState.PAST
        return SopState.CURRENT

    @property
    def state(self) -> SopState:
        """今の状態（API の応答用）"""
        return self.state_at(datetime.now(timezone.utc))


# ==========================================
# 📝 3. SOPStepTemplate モデル
# ==========================================
class SOPStepTemplate(Base):
    """
    SOPステップテンプレート(SOP_Step_Template)
    
    特定のSOPバージョン内に含まれる、具体的な1つ1つの作業手順(ステップ)
    """
    __tablename__ = "sop_step_template"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    sop_version_id: Mapped[int] = mapped_column(
        ForeignKey("sop_version.id", ondelete="CASCADE"), 
        nullable=False
    )
    
    step_num: Mapped[int] = mapped_column(
        nullable=False, 
        comment="ステップの実行順序連番"
    )
    
    title: Mapped[str] = mapped_column(String(100), nullable=False, comment="ステップタイトル")
    content: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="具体的な作業手順・マニュアル内容"
    )
    # オーダーの予定日に応じて、このステップを含めるかどうか（バージョンとともに固定される）
    # {"type": "always"} | {"type": "month_in", "months": [4]} | {"type": "first_in_week"}
    condition: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=lambda: {"type": "always"},
        server_default=text("""'{"type": "always"}'::jsonb"""),
        comment="ステップを含める条件（always / month_in / first_in_week）",
    )
    # true なら、このステップが承認されるまで次のステップを開けない（false なら提出で次が開く）
    is_blocking: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false"),
        comment="承認されるまで次のステップを開けないか",
    )
    # 双方向リレーションの設定
    sop_version: Mapped["SOPVersion"] = relationship(
        "SOPVersion", 
        back_populates="step_templates"
    )

    __table_args__ = (
        # ステップ番号は必ず1以上の正の整数
        CheckConstraint("step_num > 0", name="chk_sop_step_num_positive"),
        
        # 同一のSOPバージョン内では、同じステップ番号を二重に登録させない
        Index("uq_sop_version_step_num", "sop_version_id", "step_num", unique=True),
    )


# ==========================================
# 業務間の依存関係の定義（Phase 6）
# 「下流の業務のオーダーは、上流の業務の対応するオーダーが終わってから」。オーダー同士の具体的なつながりは ope が作る。
# ==========================================
class DependencyMatchRule(str, enum.Enum):
    """下流のオーダーが、どの上流のオーダーを待つか"""
    SAME_SHEET_EVENT = "same_sheet_event"          # 同じシート日付（例: 同じ振込日から算出した受領と本処理）
    SAME_OCCURRENCE_DATE = "same_occurrence_date"  # 同じルール上の日付（例: 毎日の2つの業務）
    # 直前の上流: 下流の予定開始以前で最新の上流のオーダー（lookback_days 日前まで。例: 月次の受領 → 毎日の処理）
    LATEST_BEFORE = "latest_before"


class DependencyKind(str, enum.Enum):
    HARD = "hard"                    # 上流が終わるまで開始できない（リリース担当・業務管理者が理由付きで強制開始できる）
    INFORMATIONAL = "informational"  # 表示だけ（開始を止めない）


class BizDependency(Base):
    __tablename__ = "biz_dependency"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    upstream_biz_id: Mapped[int] = mapped_column(ForeignKey("biz_master.id", ondelete="RESTRICT"), index=True)
    downstream_biz_id: Mapped[int] = mapped_column(ForeignKey("biz_master.id", ondelete="RESTRICT"), index=True)
    match_rule: Mapped[DependencyMatchRule] = mapped_column(
        Enum(DependencyMatchRule, name="biz_dependency_match_rule_enum"), nullable=False
    )
    kind: Mapped[DependencyKind] = mapped_column(
        Enum(DependencyKind, name="biz_dependency_kind_enum"), nullable=False, default=DependencyKind.HARD,
        server_default=text("'HARD'"),
    )
    # 直前の上流（latest_before）のときだけ: 何日前の上流のオーダーまで待つ対象にするか（それより古ければ「待ち」）
    lookback_days: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint("upstream_biz_id <> downstream_biz_id", name="ck_biz_dependency_not_self"),
        CheckConstraint(
            # ::text で比べる（追加した ENUM の値は同じトランザクションの CHECK で使えないため。マイグレーションと同じ式）
            "(match_rule::text = 'LATEST_BEFORE') = (lookback_days IS NOT NULL)", name="ck_biz_dependency_lookback_rule"
        ),
        CheckConstraint("lookback_days IS NULL OR lookback_days BETWEEN 1 AND 366", name="ck_biz_dependency_lookback_range"),
        # 同じ組み合わせの定義は1件（循環は保存時にサービスで確かめる）
        Index("uq_biz_dependency_pair", "upstream_biz_id", "downstream_biz_id", unique=True),
    )


# ==========================================
# SOP の版の記録（P.1、2026-11-11）: リリース担当が草稿を書き、業務管理者が承認する流れの履歴
# ==========================================
class SOPVersionEventType(str, enum.Enum):
    CREATED = "created"      # 草稿を作った（新規 / 承認済みの版の写し: detail.cloned_from_version_id）
    SUBMITTED = "submitted"  # 承認を依頼した
    WITHDRAWN = "withdrawn"  # 依頼を取り下げた（草稿に戻る）
    REJECTED = "rejected"    # 業務管理者が差し戻した（草稿に戻る。note = 理由、必須）
    APPROVED = "approved"    # 業務管理者が承認した（detail.effective_date。note = ひとこと）
    CANCELLED = "cancelled"  # 予定の版の承認を取り消した（草稿に戻る）


class SOPVersionEvent(Base):
    """
    SOP の版の記録（追記のみ）。草稿のステップの細かな編集は記録しない（版の updated_by / updated_at に最後の人と日時）。
    草稿を削除すると、その記録も消える（草稿は作業中の写しのため）。
    """
    __tablename__ = "sop_version_event"

    id: Mapped[int] = mapped_column(primary_key=True)
    sop_version_id: Mapped[int] = mapped_column(ForeignKey("sop_version.id", ondelete="CASCADE"), index=True)
    event_type: Mapped[SOPVersionEventType] = mapped_column(
        Enum(SOPVersionEventType, name="sop_version_event_type_enum"), nullable=False
    )
    actor_id: Mapped[int | None] = mapped_column(
        ForeignKey("staff_user.id", ondelete="RESTRICT"), comment="操作した職員（スクリプトなど職員の無い呼び出しは NULL）"
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, comment="差し戻しの理由（必須）・承認のひとこと")
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))

    __table_args__ = (
        CheckConstraint("event_type <> 'REJECTED' OR note IS NOT NULL", name="ck_sop_version_event_reject_note"),
    )


# ==========================================
# 業務のリリース設定の変更履歴（P.1、2026-11-11）: リリース担当も変えられるため、誰がいつ何を変えたかを残す
# ==========================================
RELEASE_FIELDS = ("release_mode", "release_time", "release_days_before")


class BizReleaseChange(Base):
    """
    リリース設定（release_mode / release_time / release_days_before）を変えた記録（追記のみ）。
    どの API で変えても、値が実際に変わったときだけ 1 行。before / after は 3 項目すべて（JSON の文字列・数値）。
    """
    __tablename__ = "biz_release_change"

    id: Mapped[int] = mapped_column(primary_key=True)
    biz_master_id: Mapped[int] = mapped_column(ForeignKey("biz_master.id", ondelete="RESTRICT"), index=True)
    changed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("staff_user.id", ondelete="RESTRICT"), comment="変えた職員（スクリプトなど職員の無い呼び出しは NULL）"
    )
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    before_values: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, comment="変更前のリリース設定 3 項目")
    after_values: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, comment="変更後のリリース設定 3 項目")

    __table_args__ = (
        CheckConstraint("before_values <> after_values", name="ck_biz_release_change_changed"),
    )
