from datetime import datetime
from sqlalchemy import String, Text, DateTime, Integer,CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from sqlalchemy.orm import validates 
from src.infrastructure.database import Base

class BizMaster(Base):
    """
    業務マスタ（Biz_Master）
    
    不変的、または変更頻度の極めて低い「業務の基本情報（ビジネスの看板）」を管理するルートエンティティ。
    下位層となるSOPバージョン（SOP_Version）やSOPステップ（SOP_Step_Template）の親レコードとして機能する。
    """
    __tablename__ = "biz_master"

    __table_args__ = (
        CheckConstraint(
            "business_code LIKE client_code || '%'", 
            name='check_biz_code_prefix_match'
        ),
    )

    # --- Primary Key ---
    id: Mapped[int] = mapped_column(
        primary_key=True, 
        index=True, 
        comment="システム内部ID（サロゲートキー）"
    )
    
    # --- Business Keys & Foreign References ---
    business_code: Mapped[str] = mapped_column(
        String(50), 
        unique=True, 
        index=True, 
        comment="業務コード（システム全体で一意となるビジネスキー）"
    )
    client_code: Mapped[str] = mapped_column(
        String(50), 
        index=True, 
        comment="顧客コード（該当業務を所有・委託するクライアントの識別子）"
    )
    
    # --- Basic Attributes ---
    name: Mapped[str] = mapped_column(
        String(100), 
        nullable=False, 
        comment="業務名称（例: 〇〇様向け日次照合業務）"
    )
    schedule_rule: Mapped[str | None] = mapped_column(
        String(100), 
        nullable=True, 
        comment="実行スケジュール規則（例: Every_Monday）"
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

    @validates('client_code', 'business_code')
    def validate_business_code(self, key, value):
        # client_codeが既に設定されている場合、先頭のプレフィックスが一致するか検証（チェック）する
        if value is not None:
            return value.strip().upper() 
        # 検証を通過した値を返す（これを忘れると値が代入されません）
        return value