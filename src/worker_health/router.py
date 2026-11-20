"""ワーカーの健康（並行稼働）のエンドポイント"""
from datetime import date

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from src.core.schemas.error import COMMON_ERRORS
from src.domain_staff.auth import ScopeKind, client_scope, get_client_scope
from src.domain_staff.scope import ClientScope
from src.infrastructure.database import get_db
from src.worker_health.schemas import WorkerErrorListResponse, WorkerHealthState, WorkerHealthSummaryResponse
from src.worker_health.services import get_summary, list_errors, public_health

# ログインなし（外の監視が使う）。状態と最後の心拍だけを返す
health_router = APIRouter(prefix="/health", tags=["health"])
# ログインした職員なら誰でも読める（QA・運用）
router = APIRouter(prefix="/worker-health", tags=["worker-health"])


@health_router.get("/app")
@client_scope(ScopeKind.SHARED)
def app_health_endpoint() -> dict[str, str]:
    """
    API が動いているか（ログインなし）。Docker の healthcheck が使う（deploy/docker-compose.yml）。
    データベースもワーカーも見ない: worker は app が healthy になってから起動するので、ワーカーの状態を見ると
    どちらも起動しなくなる。API はマイグレーションが終わってから起動するので、答えれば使える。
    """
    return {"status": "ok"}


@health_router.get(
    "/worker", response_model=WorkerHealthState,
    responses={503: {"model": WorkerHealthState, "description": "ワーカーの心拍が 5 分より古い・止まっている・一度も動いていない"}},
)
@client_scope(ScopeKind.SHARED)
def worker_health_endpoint(db: Session = Depends(get_db)):
    """
    ワーカーが動いているか（ログインなし。外の監視用）。最後の心拍が 5 分以内なら 200、そうでなければ 503。
    マシンの名前やエラーの中身は返さない（それは /worker-health/summary。ログインが要る）。
    """
    health = public_health(db)
    return JSONResponse(health.model_dump(mode="json"), status_code=200 if health.ok else 503)


@router.get("/summary", response_model=WorkerHealthSummaryResponse, responses={**COMMON_ERRORS})
@client_scope(ScopeKind.FILTERED)
def worker_health_summary_endpoint(
    first: date | None = Query(None, alias="from", description="最初の日（日本時間。省略時は to の 13 日前 = 14 日間）"),
    last: date | None = Query(None, alias="to", description="最後の日（その日を含む。省略時は今日）"),
    db: Session = Depends(get_db), scope: ClientScope = Depends(get_client_scope),
):
    """
    ワーカーの健康の、日本時間の 1 日ごとの数字と期間の合計（並行稼働の承認の記録。最大 62 日）。
    承認に数える問題 = 仕事のエラー + きれいに止まらなかった実行 + 5 分を超える心拍の途切れ。
    処理済みの問題（届かなかった通知・諦めた出来事）は数えずに並べる。顧客の範囲がある職員には、その顧客の分だけを数える。
    """
    return get_summary(db, first=first, last=last, scope=scope)


@router.get("/errors", response_model=WorkerErrorListResponse, responses={**COMMON_ERRORS})
@client_scope(ScopeKind.SHARED)
def worker_errors_endpoint(
    first: date | None = Query(None, alias="from", description="最初の日（日本時間。省略時は to の 13 日前）"),
    last: date | None = Query(None, alias="to", description="最後の日（その日を含む。省略時は今日）"),
    job: str | None = Query(None, description="仕事で絞る（release / extension / deadline_watch / dispatch / delivery / worker）"),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    """ワーカーが受け止めたエラーの一覧（新しい順。トレースバックつき）。顧客に関係しない"""
    return list_errors(db, first=first, last=last, job=job, limit=limit)
