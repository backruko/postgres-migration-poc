from datetime import datetime

from sqlalchemy import (
    String, 
    Text, 
    DateTime, 
    CheckConstraint, 
    ForeignKey, 
    Index,  
    Computed,          
    CHAR,
    text              
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from sqlalchemy.orm import validates, relationship

from src.infrastructure.database import Base
from src.infrastructure.mixin import SoftDeleteMixin

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
    )

    @validates('client_code')
    def validate_client_code(self, key, value):
        if value is not None:
            return value.strip().upper() 
        return value


class SOPVersion(Base):
    __tablename__ = "sop_version"
    # ...（これまでの id, biz_master_id, version_number などの定義）...

    # 下位のステップテンプレートとのリレーション設定
    step_templates: Mapped[list["SOPStepTemplate"]] = relationship(
        "SOPStepTemplate",
        back_populates="sop_version",
        cascade="all, delete-orphan", # ➔ Python上でVersionが消えたらステップも連動削除
        passive_deletes=True          # ➔ DB側の ON DELETE CASCADE に処理をお任せして高速化
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    biz_master_id: Mapped[int] = mapped_column(ForeignKey("biz_master.id", ondelete="CASCADE"), nullable=False)
    
    #1. 内部用：確実なソートのための整数連番 (1, 2, 3...)
    version_number: Mapped[int] = mapped_column(
        nullable=False, 
        comment="内部バージョン連番（ソート・最新判定用。例: 1, 2, 3)"
    )
    
    #2. 外部用：ユーザー向けの直感的なセマンティックタグ (v1.0.0, v2.1.0...)
    version_tag: Mapped[str] = mapped_column(
        String(20), 
        nullable=False, 
        comment="表示用バージョンタグ（例: v1.0.0)"
    )

    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)

    # リレーション
    biz_master: Mapped["BizMaster"] = relationship("BizMaster", back_populates="sop_versions")

    __table_args__ = (
        #version_number は必ず1以上の正の整数
        CheckConstraint("version_number > 0", name="chk_sop_version_number_positive"),
        
        #version_tag のフォーマット強制 (vX.Y.Z)
        CheckConstraint("version_tag ~ '^v[0-9]+\\.[0-9]+\\.[0-9]+$'", name="chk_sop_version_tag_format"),
        
        #同一業務内では、内部連番は絶対に重複させない
        Index("uq_biz_version_number", "biz_master_id", "version_number", unique=True),
        
        #同一業務内では、外部タグも絶対に重複させない
        Index("uq_biz_version_tag", "biz_master_id", "version_tag", unique=True),
    )


class SOPStepTemplate(Base):
    """
    SOPステップテンプレート(SOP_Step_Template)
    
    特定のSOPバージョン内に含まれる、具体的な1つ1つの作業手順(ステップ)
    例: 「ステップ1: ファイルをダウンロードする」「ステップ2: データを照合する」など。
    """
    __tablename__ = "sop_step_template"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    
    # 1. 完全に SOPVersion に橋渡し（ブリッジ）する外部キー
    # 親（Version）がhard deleteされたら、ステップも連動して消滅するように設定
    sop_version_id: Mapped[int] = mapped_column(
        ForeignKey("sop_version.id", ondelete="CASCADE"), 
        nullable=False
    )
    
    # 2. ステップの実行順序（例: 1, 2, 3...）
    step_num: Mapped[int] = mapped_column(
        nullable=False, 
        comment="ステップの実行順序連番"
    )
    
    # --- 属性 ---
    title: Mapped[str] = mapped_column(String(100), nullable=False, comment="ステップタイトル")
    content: Mapped[str | None] = mapped_column(Text, nullable=True, comment="具体的な作業手順・マニュアル内容")

    # 3. 双方向リレーションの設定
    sop_version: Mapped["SOPVersion"] = relationship(
        "SOPVersion", 
        back_populates="step_templates"
    )

    __table_args__ = (
        #ステップ番号は必ず1以上の正の整数
        CheckConstraint("step_num > 0", name="chk_sop_step_num_positive"),
        
        #同一のSOPバージョン内では、同じステップ番号を二重に登録させない
        Index("uq_sop_version_step_num", "sop_version_id", "step_num", unique=True),
    )