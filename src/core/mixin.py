from datetime import datetime

from sqlalchemy import Boolean, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from src.core.clock import utc_now

class SoftDeleteMixin:
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=None,
        comment="削除日時(未削除の場合はNULL)"
    )

    def soft_delete(self) -> None:
        self.is_deleted = True
        # タイムゾーン付きの UTC で持つ（DB やサーバーのタイムゾーンの設定に左右されない）
        self.deleted_at = utc_now()

    def restore(self) -> None:
        self.is_deleted = False
        self.deleted_at = None
