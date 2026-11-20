"""
通知の文面（通知の仕組み）。文面は当面コードにある（ルールの template_code で選ぶ）。

文面は出来事の中身（formats の写し）だけから作る: オーダーや予定の表は読まない（依存は一方向。設計書の決定）。
そのため、知らせない時間帯で後から送るときも、文面は「その時刻の事実」を書く（例: 13:00 の締切を過ぎた・その時点で作業中）。

- 1 つのルールは 1 つの文面を使うので、文面は扱える出来事の種類を持つ（ルールを作るときに確かめる）
- 件名・本文は日本語。時刻は日本時間
- 画面へのリンクは APP_BASE_URL があるときだけ（日次ダッシュボードの「締切・遅れ」、その予定日）
"""
from dataclasses import dataclass
from datetime import datetime
from collections.abc import Callable

from src.domain_events.formats import DestinationTestV1, EventType, OrderDeadlineV1
from src.domain_events.models import DomainEvent
from src.infrastructure.config import settings
from src.core.clock import LOCAL_TIMEZONE


# 見つけた時点のオーダーの状態（画面と同じ言葉。通知のドメインはオーダーの型を読まないので、ここに持つ）
_STATUS_LABEL = {"pending": "未リリース", "ready": "リリース済", "in_progress": "作業中", "on_hold": "保留中"}


@dataclass(frozen=True)
class Rendered:
    subject: str
    body: str
    link: str | None


@dataclass(frozen=True)
class Template:
    code: str
    name: str  # 表示名（設定の画面で選ぶとき）
    event_types: frozenset[str]
    render: Callable[[DomainEvent, str], Rendered]  # (出来事, ルール名) → 文面


def _local(value: datetime) -> datetime:
    return value.astimezone(LOCAL_TIMEZONE)


def _order_deadline(event: DomainEvent, rule_name: str) -> Rendered:
    """OrderLate・OrderDueSoon の文面（版 1 の写しから）"""
    o = OrderDeadlineV1.model_validate(event.payload)
    late = event.event_type == EventType.ORDER_LATE
    deadline = _local(o.deadline_at)
    day = o.occurrence_date
    tag = "遅れ" if late else "まもなく"
    subject = f"【{tag}】{o.client_code} {o.biz_name} / {o.template_name} {day.month}/{day.day} 締切 {deadline:%H:%M}"
    lead = (
        f"締切（{deadline:%m/%d %H:%M}）を過ぎても完了していないオーダーがあります。"
        if late else
        f"締切（{deadline:%m/%d %H:%M}）が近づいています。まだ完了していません。"
    )
    noticed = _local(event.occurred_at)
    link = None
    if settings.app_base_url:
        link = f"{settings.app_base_url.rstrip('/')}/daily?date={day.isoformat()}&tab=attention"
    lines = [
        lead,
        "",
        f"顧客: {o.client_code} {o.client_name}",
        f"業務: {o.business_code} {o.biz_name}",
        f"予定: {o.template_name}（予定日 {day.isoformat()}）",
        f"予定の時間: {_local(o.scheduled_start):%m/%d %H:%M} 〜 {_local(o.scheduled_end):%m/%d %H:%M}",
        f"締切: {deadline:%Y-%m-%d %H:%M}",
        f"状態（{noticed:%m/%d %H:%M} 時点）: {_STATUS_LABEL.get(o.status, o.status)}",
        f"オーダー: #{o.order_id}",
    ]
    if link:
        lines += ["", f"画面: {link}"]
    lines += ["", f"この通知はルール「{rule_name}」で送られました。"]
    return Rendered(subject=subject[:200], body="\n".join(lines), link=link)


def _destination_test(event: DomainEvent, rule_name: str) -> Rendered:
    """テスト送信の文面。本物のアラームと同じ道（ワーカー → チャネル）で送る"""
    t = DestinationTestV1.model_validate(event.payload)
    requested = _local(event.occurred_at)
    who = t.requested_by_name or "（不明）"
    body = "\n".join([
        "これは通知の宛先のテスト送信です。このメールが届いていれば、宛先とメールサーバーの設定は正しく動いています。",
        "",
        f"宛先: {t.destination_name}（{t.address}）",
        f"頼んだ人: {who}（{requested:%Y-%m-%d %H:%M}）",
        "",
        "返信は不要です。",
    ])
    return Rendered(subject=f"【テスト】通知のテスト送信（宛先: {t.destination_name}）"[:200], body=body, link=None)


ORDER_DEADLINE = "order_deadline"
DESTINATION_TEST = "destination_test"  # システムの文面（ルールでは選べない）

TEMPLATES: dict[str, Template] = {
    ORDER_DEADLINE: Template(
        code=ORDER_DEADLINE,
        name="オーダーの遅れ・まもなく",
        event_types=frozenset({EventType.ORDER_LATE, EventType.ORDER_DUE_SOON}),
        render=_order_deadline,
    ),
}

# ルールでは選べない、システムの文面
SYSTEM_TEMPLATES: dict[str, Template] = {
    DESTINATION_TEST: Template(
        code=DESTINATION_TEST, name="宛先のテスト送信", event_types=frozenset({EventType.DESTINATION_TEST}),
        render=_destination_test,
    ),
}
