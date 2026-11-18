"""
出来事の一覧（読み取りのみ。通知の仕組み N.2b、2026-11-12）。

QA・運用が「正しい出来事が、正しい時刻に記録されたか」を確かめるためのもの。書き換えはしない。
- ログインした職員なら誰でも読める。顧客の範囲がある職員には、その顧客の出来事だけ（顧客のない出来事は見せない）
- 起きた日時（occurred_at）の新しい順。種類・顧客・オーダー・期間で絞れる
- タイムゾーンのない日時は日本時間として読む（画面・QA が「2026-11-12T09:00」と書けるように）
"""
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.exceptions import BadRequestError
from src.domain_events.models import DomainEvent
from src.domain_events.schemas import DomainEventListResponse, DomainEventResponse
from src.domain_staff.scope import ALL_CLIENTS, ClientScope
from src.core.clock import assume_local


def list_events(
    db: Session,
    *,
    event_type: str | None = None,
    client_code: str | None = None,
    subject_type: str | None = None,
    subject_id: int | None = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
    limit: int = 100,
    scope: ClientScope = ALL_CLIENTS,
) -> DomainEventListResponse:
    """出来事の一覧。occurred_from 以上・occurred_to 未満。件数の上限は範囲で絞った後"""
    occurred_from, occurred_to = assume_local(occurred_from), assume_local(occurred_to)
    if occurred_from and occurred_to and occurred_from >= occurred_to:
        raise BadRequestError("'from' must be earlier than 'to'.")
    # 範囲の絞り込みは client_code IN (…) なので、顧客のない出来事（NULL）も範囲のある職員には出ない
    stmt = select(DomainEvent).where(scope.clause(DomainEvent.client_code))
    if event_type:
        stmt = stmt.where(DomainEvent.event_type == event_type)
    if client_code:
        stmt = stmt.where(DomainEvent.client_code == client_code.strip().upper())
    if subject_type:
        stmt = stmt.where(DomainEvent.subject_type == subject_type)
    if subject_id is not None:
        stmt = stmt.where(DomainEvent.subject_id == subject_id)
    if occurred_from:
        stmt = stmt.where(DomainEvent.occurred_at >= occurred_from)
    if occurred_to:
        stmt = stmt.where(DomainEvent.occurred_at < occurred_to)
    rows = list(db.scalars(stmt.order_by(DomainEvent.occurred_at.desc(), DomainEvent.id.desc()).limit(limit + 1)))
    return DomainEventListResponse(
        items=[DomainEventResponse.model_validate(e) for e in rows[:limit]], truncated=len(rows) > limit,
    )
