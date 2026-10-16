from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime
from sqlalchemy.orm import Mapped, mapped_column

class SoftDeleteMixin:
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    # timezone=True になっているため、PostgreSQL側は完璧にタイムゾーンを解釈できます
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), 
        default=None, 
        comment="削除日時(未削除の場合はNULL)"
    )

    def soft_delete(self) -> None:
        self.is_deleted = True
        # タイムゾーン情報（+00:00）を持たせたままDBに渡すことで、DB側の設定が何であれ絶対に時間がズレなくなります。
        self.deleted_at = datetime.now(timezone.utc)

    def restore(self) -> None:
        self.is_deleted = False
        self.deleted_at = None