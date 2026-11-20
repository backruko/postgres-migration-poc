"""出来事の一覧（読み取りのみ。通知の仕組み）のエンドポイント"""
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from src.core.schemas.error import COMMON_ERRORS
from src.domain_events.schemas import DomainEventListResponse
from src.domain_events.services import list_events
from src.domain_staff.auth import ScopeKind, client_scope, get_client_scope
from src.domain_staff.scope import ClientScope
from src.infrastructure.database import get_db

router = APIRouter(prefix="/events", tags=["events"])


@router.get("/", response_model=DomainEventListResponse, responses={**COMMON_ERRORS})
@client_scope(ScopeKind.FILTERED)
def list_events_endpoint(
    event_type: str | None = Query(None, description="種類で絞り込む（例: OrderLate, OrderDueSoon）"),
    client_code: str | None = Query(None, description="顧客コードで絞り込む"),
    order_id: int | None = Query(None, description="このオーダーの出来事だけ"),
    occurred_from: datetime | None = Query(
        None, alias="from", description="起きた日時がこれ以降（例: 2026-11-12T09:00。タイムゾーンがなければ日本時間）",
    ),
    occurred_to: datetime | None = Query(None, alias="to", description="起きた日時がこれより前（同上）"),
    limit: int = Query(100, ge=1, le=1000, description="最大件数（起きた日時の新しい順）"),
    db: Session = Depends(get_db), scope: ClientScope = Depends(get_client_scope),
):
    """
    記録された出来事（アウトボックス）を、起きた日時の新しい順に返します。読み取りのみ。
    QA・運用が、締切の監視が正しい出来事を正しい時刻に記録したかを確かめるためのもの（まだ何も送りません）。
    顧客の範囲がある職員には、その顧客の出来事だけを返します。
    """
    return list_events(
        db, event_type=event_type, client_code=client_code,
        subject_type="order" if order_id is not None else None, subject_id=order_id,
        occurred_from=occurred_from, occurred_to=occurred_to, limit=limit, scope=scope,
    )
