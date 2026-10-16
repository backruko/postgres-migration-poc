from datetime import datetime

from sqlalchemy import String, Text, UniqueConstraint, DateTime, CHAR              
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from sqlalchemy.orm import validates 

from src.infrastructure.database import Base

class ClientMaster(Base):
    __tablename__ = "client_master" 

    id: Mapped[int] = mapped_column(primary_key=True, index=True, comment="サロゲートキー")
    code: Mapped[str] = mapped_column(CHAR(4), comment="ナチュラルキー")
    name: Mapped[str] = mapped_column(String(100), comment="顧客名")


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

    # Boolean は import 不要、Python標準の bool を使います。
    # default=True を設定すると、新規作成時は自動的に True になります。

    is_active: Mapped[bool] = mapped_column(default=True, comment="有効フラグ（論理削除）")

    # 複合ユニーク制約
    __table_args__ = (
        UniqueConstraint('code', name='uq_client_id'),
    )

    @validates('code')
    def validate_code(self, key, value):
        if value is not None:
            # 1. 自動で前後の空白を削除し、大文字に変換
            cleaned_value = value.strip().upper()
            
            # 2. 4文字固定（Char4）の厳格なバリデーション
            if len(cleaned_value) != 4:
                raise ValueError(f"Client code must be exactly 4 characters after stripping. Got: '{cleaned_value}' ({len(cleaned_value)} chars)")
            
            return cleaned_value
            
        return value