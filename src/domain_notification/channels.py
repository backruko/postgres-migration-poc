"""
届け方（チャネル）の口（ポート。通知の仕組み）。

配信の処理はこの口だけを使い、メールやチャットの仕組みを知らない。届け方を足すときは、アダプターを 1 つ足す。
偽物のチャネル（FakeChannel）は本当には送らず、宛先のアドレスで結果を決める（テスト・QA 用）。
メールは SMTP のアダプター（smtp.py）。どちらを使うかは default_channels が設定で決める。

- 送れた: provider_message_id（送り先の仕組みの ID）を返す
- 一時的な失敗（相手のサーバーが落ちている など）: もう一度試す。永続的な失敗（宛先が存在しない など）: すぐあきらめる
- アダプターが例外を投げたら、一時的な失敗として扱う（配信の処理が止まらない）
"""
import uuid
from dataclasses import dataclass, field
from typing import Protocol

from src.domain_notification.models import Notification, NotificationChannel, NotificationDestination


@dataclass(frozen=True)
class SendResult:
    ok: bool
    provider_message_id: str | None = None
    error: str | None = None
    permanent: bool = False  # 失敗のとき: もう試しても無駄か


class Channel(Protocol):
    def send(self, destination: NotificationDestination, notification: Notification) -> SendResult: ...


@dataclass
class FakeChannel:
    """
    本当には送らないチャネル。アドレスの @ の前で結果を決める:
    fail-temp で始まる → 一時的な失敗、fail-perm で始まる → 永続的な失敗、それ以外 → 送れた（ID は fake: で始まる）
    """
    sent: list[tuple[str, str, str]] = field(default_factory=list)  # 送ったもの（宛先, 件名, 本文）。テストが確かめる

    def send(self, destination: NotificationDestination, notification: Notification) -> SendResult:
        local = destination.address.split("@", 1)[0].lower()
        if local.startswith("fail-perm"):
            return SendResult(ok=False, error="fake: the address does not exist", permanent=True)
        if local.startswith("fail-temp"):
            return SendResult(ok=False, error="fake: the server is not answering", permanent=False)
        self.sent.append((destination.address, notification.subject, notification.body))
        return SendResult(ok=True, provider_message_id=f"fake:{uuid.uuid4()}")


def default_channels() -> dict[NotificationChannel, Channel]:
    """
    いま使うチャネル。SMTP_HOST があればメールは SMTP で送る。
    なければ、開発・テストでは偽物（送らない）、本番ではメールのチャネルなし: 設定を忘れたサーバーで、偽物が「送れた」と
    記録しないように。配信は一時的な失敗として再試行され、最後は失敗として見える
    """
    from src.domain_notification.smtp import SmtpChannel, SmtpSettings  # smtp が SendResult を読むので、ここで読む
    from src.infrastructure.config import settings

    if settings.smtp_host:
        return {NotificationChannel.EMAIL: SmtpChannel(SmtpSettings(
            host=settings.smtp_host, port=settings.smtp_port, user=settings.smtp_user, password=settings.smtp_password,
            from_address=settings.smtp_from_address, starttls=settings.smtp_starttls,
            timeout_seconds=settings.smtp_timeout_seconds,
        ))}
    if settings.environment == "production":
        return {}
    return {NotificationChannel.EMAIL: FakeChannel()}


def describe(channels: dict[NotificationChannel, Channel]) -> str:
    """ワーカーの起動時のログ用（どのチャネルで送るか。パスワードは出さない）"""
    email = channels.get(NotificationChannel.EMAIL)
    if email is None:
        return "email: none (SMTP_HOST is not set; deliveries will fail)"
    describe_one = getattr(email, "settings", None)
    return "email: " + (describe_one.describe() if describe_one is not None else "fake (nothing is sent)")
