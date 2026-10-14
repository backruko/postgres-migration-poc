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
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from sqlalchemy.orm import validates, relationship
from src.infrastructure.database import Base

class BizMaster(Base):
    """
    業務マスタ（Biz_Master）
    
    不変的、または変更頻度の極めて低い「業務の基本情報（ビジネスの看板）」を管理するルートエンティティ。
    下位層となるSOPバージョン（SOP_Version）やSOPステップ（SOP_Step_Template）の親レコードとして機能する。
    """
    __tablename__ = "biz_master"

#　（N+1問題）対策
# bizes = session.query(BizMaster).options(selectinload(BizMaster.sop_versions)).all()

    sop_versions: Mapped[list["SOPVersion"]] = relationship(
        "SOPVersion",
        back_populates="biz_master"
    )

    # --- Primary Key ---
    id: Mapped[int] = mapped_column(
        primary_key=True, 
        index=True, 
        comment="システム内部ID（サロゲートキー）"
    )
    
    # --- Business Keys & Foreign References ---
    client_code: Mapped[str] = mapped_column(
        CHAR(4), # 4文字固定
        ForeignKey("client_master.code"),  
        index=True, 
        comment="顧客コード（ClientMaster外部キー）"
    )

    business_seq: Mapped[int] = mapped_column(
        comment="業務連番（同一顧客内で一意。例: 1, 2, 101）"
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

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), 
        default=None, 
        comment="削除日時（未削除の場合はNULL）"
    )


    __table_args__ = (
        # 1. 組み合わせユニーク：同一クライアント内で同じ連番は絶対に使わせない
        Index(
            'uq_client_business_seq_alive',  
            'client_code', 
            'business_seq', 
            unique=True,
            postgresql_where=deleted_at.is_(None)  
        ),

        #  business_code も未削除のデータの中だけでユニークにする
        Index(
            'uq_business_code_alive',
            'business_code', 
            unique=True,
            postgresql_where=deleted_at.is_(None)
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

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    
    biz_master_id: Mapped[int] = mapped_column(ForeignKey("biz_master.id"))
    
    biz_master: Mapped["BizMaster"] = relationship(
        "BizMaster",
        back_populates="sop_versions"
    )