"""届かなかったアラームの応答の形。画面（日次ダッシュボード）が日本語にする: 状態・理由は英語のコードのまま"""
from datetime import date, datetime, time

from pydantic import BaseModel, Field

from src.domain_notification.models import DeliveryStatus, NotificationStatus, QuietMode


class FailedDelivery(BaseModel):
    destination_id: int
    destination_name: str = Field(description="宛先の表示名（削除した宛先も、そのときの名前）")
    address: str
    status: DeliveryStatus
    attempts: int
    last_error: str | None = Field(description="最後の失敗の理由（相手のサーバーの返事のまま）")
    sent_at: datetime | None


class FailedNotification(BaseModel):
    id: int
    status: NotificationStatus = Field(description="failed（どこにも届かなかった）か partly_failed（一部に届かなかった）")
    created_at: datetime = Field(description="アラームを作った日時")
    completed_at: datetime | None = Field(description="配信をあきらめた日時")
    event_type: str = Field(description="もとの出来事の種類（OrderLate / OrderDueSoon）")
    client_code: str | None
    client_name: str | None = Field(description="出来事の写しの顧客名")
    order_id: int | None = Field(description="対象のオーダー（オーダーの出来事のとき）")
    occurrence_date: date | None = Field(description="オーダーの予定日（写しから。画面のリンク用）")
    subject: str
    rule_id: int | None
    rule_name: str
    deliveries: list[FailedDelivery]
    acknowledged_at: datetime | None = Field(description="職員が確認した日時（まだなら null）")
    acknowledged_by_id: int | None
    acknowledged_by_name: str | None


class NotificationFailuresResponse(BaseModel):
    """日次ダッシュボードの「届かなかったアラーム」と「再試行中」"""
    unacknowledged: int = Field(description="まだ確認されていない、届かなかったアラームの数（赤い帯・件数）")
    retrying: int = Field(description="いま再試行中の配信の数（黄色の行。メールサーバーの不調の早い知らせ）")
    last_retry_error: str | None = Field(description="再試行中の配信の、いちばん新しい失敗の理由")
    last_retry_at: datetime | None
    items: list[FailedNotification] = Field(description="まだ確認されていないもの（古くても）と、直近 7 日に確認したもの。新しい順")
    truncated: bool


# ==========================================
# 通知の設定（宛先・ルール）
# ==========================================
class StaffRef(BaseModel):
    id: int
    name: str


class NamedRef(BaseModel):
    id: int
    name: str


class DestinationIn(BaseModel):
    """宛先を作る・直す（直すときもすべての項目を送る）。届け方は今はメールだけ"""
    name: str = Field(min_length=1, max_length=100)
    address: str = Field(min_length=3, max_length=2000, description="メールアドレス")
    client_code: str | None = Field(None, min_length=4, max_length=4, description="この顧客用（空 = 全顧客）")
    owner: str | None = Field(None, max_length=100, description="持ち主のチーム")
    enabled: bool = True


class LastTest(BaseModel):
    """最後のテスト送信の結果"""
    requested_at: datetime
    requested_by_name: str | None
    state: str = Field(description="waiting（ワーカー待ち）/ sending / sent / failed")
    sent_at: datetime | None
    error: str | None = Field(description="届かなかった理由（相手のサーバーの返事のまま）")


class DestinationOut(BaseModel):
    id: int
    name: str
    channel: str
    address: str
    client_code: str | None
    owner: str | None
    enabled: bool
    is_deleted: bool
    used_by: list[NamedRef] = Field(description="この宛先を使っている（削除していない）ルール")
    last_test: LastTest | None
    created_at: datetime
    created_by: StaffRef | None
    updated_at: datetime
    updated_by: StaffRef | None


class RuleIn(BaseModel):
    """ルールを作る・直す（直すときもすべての項目を送る）"""
    name: str = Field(min_length=1, max_length=100)
    event_types: list[str] = Field(description="反応する出来事の種類（OrderLate / OrderDueSoon）")
    client_code: str | None = Field(None, min_length=4, max_length=4, description="この顧客の出来事だけ（空 = 全顧客）")
    biz_master_id: int | None = Field(None, description="この業務の出来事だけ（空 = すべての業務。業務を選ぶと顧客もその業務のもの）")
    template_code: str = Field(description="文面（GET /notifications/options の templates）")
    destination_ids: list[int]
    dedupe_window_minutes: int | None = Field(None, description="同じオーダー・同じ種類の通知を、この分数のうちは 2 回出さない（空 = 制限なし）")
    quiet_start: time | None = Field(None, description="知らせない時間帯の始まり（日本時間）")
    quiet_end: time | None = Field(None, description="知らせない時間帯の終わり（日本時間。日をまたいでよい）")
    quiet_mode: QuietMode | None = Field(None, description="その時間帯の出来事: hold（終わってから知らせる）/ drop（知らせない）")
    enabled: bool = True


class RuleOut(BaseModel):
    id: int
    name: str
    event_types: list[str]
    client_code: str | None
    biz_master_id: int | None
    biz_name: str | None
    template_code: str
    destinations: list[NamedRef]
    dedupe_window_minutes: int | None
    quiet_start: time | None
    quiet_end: time | None
    quiet_mode: QuietMode | None
    enabled: bool
    is_deleted: bool
    created_at: datetime
    created_by: StaffRef | None
    updated_at: datetime
    updated_by: StaffRef | None


class EventTypeOption(BaseModel):
    code: str
    label: str


class TemplateOption(BaseModel):
    code: str
    name: str
    event_types: list[str]


class NotificationOptions(BaseModel):
    """ルールの画面で選べるもの（出来事の種類・文面）"""
    event_types: list[EventTypeOption]
    templates: list[TemplateOption]


class TestRequested(BaseModel):
    event_id: int = Field(description="記録した出来事（DestinationTestRequested）。ワーカーが 1 分以内に送る")


# ==========================================
# 通知の記録（アラームの記録）
# ==========================================
class AttemptOut(BaseModel):
    attempt_no: int
    attempted_at: datetime
    address: str = Field(description="この回に送ったアドレス（そのときの宛先のもの）")
    ok: bool
    permanent: bool
    error: str | None
    provider_message_id: str | None


class LogDelivery(BaseModel):
    destination_id: int
    destination_name: str
    address: str = Field(description="宛先のいまのアドレス（送ったアドレスは attempt_log）")
    status: DeliveryStatus
    attempts: int
    next_attempt_at: datetime | None
    sent_at: datetime | None
    last_error: str | None
    attempt_log: list[AttemptOut] = Field(description="1 回ごとの試み（2026-11-12 の N.5b より前に送ったものは空）")


class LogEntry(BaseModel):
    id: int
    created_at: datetime
    completed_at: datetime | None
    status: NotificationStatus
    suppressed_reason: str | None = Field(description="知らせなかった理由（dedupe_window / quiet_hours / no_destination / template_missing）")
    is_test: bool = Field(description="宛先のテスト送信（ルールなし）")
    event_id: int
    event_type: str
    client_code: str | None
    client_name: str | None
    order_id: int | None
    subject: str
    rule_id: int | None
    rule_name: str | None
    deliveries: list[LogDelivery]
    acknowledged_at: datetime | None
    acknowledged_by_name: str | None


class LogEntryDetail(LogEntry):
    body: str
    link: str | None


class NotificationLogResponse(BaseModel):
    items: list[LogEntry] = Field(description="作った日時の新しい順")
    truncated: bool
