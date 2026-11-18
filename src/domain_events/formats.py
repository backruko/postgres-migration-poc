"""
出来事の形（N.2、2026-11-12）。domain_event.payload に入る内容の、種類ごとの決まり。

通知のドメインはこの形だけに頼り、オーダーや予定の表の中身は読まない（依存は一方向）。そのため payload は
「その時点の事実の写し」です（2026-11-12 の決定）: 後でオーダーが変わっても、出来事は起きたときのまま残り、
通知の文面は出来事だけから作れる。

- 種類の名前は設計書と同じ（OrderDueSoon・OrderLate）。QA・運用が設計書・API・DB で同じ言葉を見る
- 形を変えるときは版（schema_version）を上げ、古い版の行もそのまま読めるようにする（行は書き換えない）
"""
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class EventType:
    """出来事の種類（domain_event.event_type の値）"""
    ORDER_DUE_SOON = "OrderDueSoon"  # まもなく: 「まもなく」の開始（warn_at）を過ぎた。締切はまだ
    ORDER_LATE = "OrderLate"         # 遅れ: 締切（deadline_at）を過ぎても完了・取消になっていない
    # 宛先のテスト送信を頼まれた（N.5a）。通知の仕組みが、ルールを通さずその宛先だけに送る。ルールは反応できない
    DESTINATION_TEST = "DestinationTestRequested"


class EventFormat(BaseModel):
    """payload の形の共通の土台。知らない項目は受け付けない（書き手の誤りを早く見つける）"""
    model_config = ConfigDict(extra="forbid", frozen=True)


class OrderDeadlineV1(EventFormat):
    """OrderDueSoon・OrderLate の版 1: 見つけた時点のオーダーの写し（文面に要る名前も含む）"""
    order_id: int
    client_code: str
    client_name: str
    biz_master_id: int
    business_code: str
    biz_name: str
    template_id: int
    template_name: str
    occurrence_date: date
    scheduled_start: datetime
    scheduled_end: datetime
    deadline_at: datetime
    warn_at: datetime | None
    status: str  # 見つけた時点のオーダーの状態（pending / ready / in_progress / on_hold）


class DestinationTestV1(EventFormat):
    """DestinationTestRequested の版 1: 頼まれた時点の宛先と、頼んだ職員"""
    destination_id: int
    destination_name: str
    address: str
    requested_by_id: int | None
    requested_by_name: str | None


# 種類ごとの、今書く版とその形。publish() はここにない種類や違う形を受け付けない
CURRENT_FORMATS: dict[str, tuple[int, type[EventFormat]]] = {
    EventType.ORDER_DUE_SOON: (1, OrderDeadlineV1),
    EventType.ORDER_LATE: (1, OrderDeadlineV1),
    EventType.DESTINATION_TEST: (1, DestinationTestV1),
}

# システムの出来事（ルールでは選べない。通知の仕組みが自分で扱う）
SYSTEM_EVENT_TYPES: frozenset[str] = frozenset({EventType.DESTINATION_TEST})
