"""出来事の一覧（読み取りのみ。通知の仕組み）の応答の形"""
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DomainEventResponse(BaseModel):
    """記録された出来事 1 件（中身は event_type と schema_version の形。処理の列は通知の仕組みが書く）"""
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_type: str = Field(description="出来事の種類（例: OrderLate）")
    schema_version: int = Field(description="payload の形の版")
    occurred_at: datetime = Field(description="起きた日時（遅れなら締切、まもなくなら「まもなく」の開始）")
    recorded_at: datetime = Field(description="記録した日時（監視が見つけた日時）")
    client_code: str | None
    subject_type: str | None = Field(description="何についての出来事か（例: order）")
    subject_id: int | None = Field(description="その ID（例: オーダーの ID）")
    payload: dict[str, Any] = Field(description="起きた時点の事実の写し")
    dedupe_key: str
    processed_at: datetime | None = Field(description="通知の仕組みが処理を終えた日時（まだなら null）")
    attempts: int
    last_error: str | None


class DomainEventListResponse(BaseModel):
    """出来事の一覧（起きた日時の新しい順）"""
    items: list[DomainEventResponse]
    truncated: bool = Field(description="true if more events matched than the limit")
