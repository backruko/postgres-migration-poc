"""ワーカーの健康（並行稼働）の API の形"""
from datetime import date, datetime

from pydantic import BaseModel, Field


class WorkerHealthState(BaseModel):
    state: str = Field(description="ok（動いている）/ stale（心拍が 5 分より古い）/ stopped（止まった印がある）/ never（一度も動いていない）")
    ok: bool
    last_beat_at: datetime | None = Field(None, description="最後の心拍")
    seconds_since_beat: float | None = None


class WorkerStatus(WorkerHealthState):
    run_id: int | None = None
    host: str | None = None
    started_at: datetime | None = None


class WorkerGap(BaseModel):
    start: datetime = Field(description="途切れる前の最後の心拍")
    end: datetime = Field(description="次の心拍（続いていれば今）")
    minutes: float
    ongoing: bool = Field(description="今も途切れている")


class WorkerUncleanEnd(BaseModel):
    run_id: int
    host: str
    ended_at: datetime
    reason: str = Field(description="crash（落ちた）/ no_stop（止まった印のないまま途絶えた: 強制終了・ハング）")


class WorkerHealthDay(BaseModel):
    day: date = Field(description="日本時間の日")
    start: datetime
    end: datetime = Field(description="次の日の 0 時か、今（今日）")
    ok: bool = Field(description="承認に数える問題が 0")
    problems: int = Field(description="承認に数える問題: エラー + きれいに止まらなかった実行 + 途切れ")
    passes: int = Field(description="終えた 1 回り（ふつうは 1 日 1,440）")
    uptime_percent: float | None = Field(None, description="動いていた割合（最初の心拍より前は数えない）")
    longest_gap_minutes: float | None = Field(None, description="心拍の間のいちばん長いもの（分）")
    slowest_pass_ms: int | None = Field(None, description="いちばん遅かった 1 回り（ミリ秒）")
    errors: int
    errors_by_job: dict[str, int]
    gaps: list[WorkerGap] = Field(description="5 分を超える途切れ（その日に重なるもの）")
    unclean_ends: list[WorkerUncleanEnd]
    restarts: int = Field(description="その日に起動した回数")
    failed_notifications: int = Field(description="処理済みの問題（数えない）: 再送の後も届かなかった通知")
    given_up_events: int = Field(description="処理済みの問題（数えない）: 5 回で諦めた出来事")


class WorkerHealthTotal(BaseModel):
    ok: bool
    problems: int
    days: int
    passes: int
    uptime_percent: float | None
    errors: int
    errors_by_job: dict[str, int]
    gaps: int = Field(description="5 分を超える途切れ（日をまたいでも 1 つ）")
    longest_gap_minutes: float | None
    unclean_ends: int
    restarts: int
    failed_notifications: int
    given_up_events: int


class WorkerHealthSummaryResponse(BaseModel):
    period_from: date = Field(serialization_alias="from")
    period_to: date = Field(serialization_alias="to")
    status: WorkerStatus
    total: WorkerHealthTotal
    days: list[WorkerHealthDay] = Field(description="古い順")


class WorkerErrorItem(BaseModel):
    id: int
    run_id: int
    host: str
    occurred_at: datetime
    job: str
    error_type: str
    message: str
    traceback: str


class WorkerErrorListResponse(BaseModel):
    items: list[WorkerErrorItem]
    total: int
