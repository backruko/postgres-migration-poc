"""
メールのチャネル（SMTP。通知の仕組み N.4a、2026-11-12）。設定は環境変数 SMTP_*（src/infrastructure/config.py）。

開発では Mailpit・MailHog のような受けるだけのサーバー、本番では社内の SMTP サーバーを、設定だけで替えられる。

- 1 通ごとにつなぐ（通知は 1 分に数通の見込み。つなぎっぱなしにしない）。待つのは最大 SMTP_TIMEOUT_SECONDS 秒
- STARTTLS: 空ならサーバーが対応していれば使う・1 なら必ず・0 なら使わない。SMTP_USER があればログインする
- 件名・本文は日本語（UTF-8）。Message-ID を付け、それを送り先の ID として記録する
- 永続的な失敗（すぐあきらめる）は、宛先を断られた 5xx と、本文を断られた 5xx だけ。つながらない・ログインできない・
  差出人を断られた などは一時的な失敗として扱う: 設定を直せば、再試行（約 80 分）のうちに届く
"""
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from src.domain_notification.channels import SendResult
from src.domain_notification.models import Notification, NotificationDestination


@dataclass(frozen=True)
class SmtpSettings:
    host: str
    port: int = 587
    user: str | None = None
    password: str | None = None
    from_address: str = "system-noreply@example.com"
    starttls: bool | None = None
    timeout_seconds: float = 30

    def describe(self) -> str:
        """ログ用（パスワードは出さない）"""
        tls = {None: "STARTTLS if offered", True: "STARTTLS required", False: "no STARTTLS"}[self.starttls]
        return f"SMTP {self.host}:{self.port} ({tls}{', login as ' + self.user if self.user else ''}) from {self.from_address}"


def build_message(settings: SmtpSettings, destination: NotificationDestination, notification: Notification) -> EmailMessage:
    message = EmailMessage()
    message["From"] = settings.from_address
    message["To"] = destination.address
    message["Subject"] = notification.subject
    message["Date"] = formatdate(localtime=False)
    domain = settings.from_address.rsplit("@", 1)[-1] if "@" in settings.from_address else None
    message["Message-ID"] = make_msgid(domain=domain)
    message.set_content(notification.body, charset="utf-8")
    return message


def _is_permanent(error: smtplib.SMTPException) -> bool:
    if isinstance(error, smtplib.SMTPRecipientsRefused):
        codes = [code for code, _ in error.recipients.values()]
        return bool(codes) and all(500 <= code < 600 for code in codes)
    if isinstance(error, smtplib.SMTPDataError):
        return 500 <= error.smtp_code < 600
    return False


@dataclass
class SmtpChannel:
    settings: SmtpSettings

    def send(self, destination: NotificationDestination, notification: Notification) -> SendResult:
        s = self.settings
        message = build_message(s, destination, notification)
        try:
            with smtplib.SMTP(s.host, s.port, timeout=s.timeout_seconds) as smtp:
                smtp.ehlo()
                if s.starttls is True or (s.starttls is None and smtp.has_extn("starttls")):
                    smtp.starttls()
                    smtp.ehlo()
                if s.user:
                    smtp.login(s.user, s.password or "")
                smtp.send_message(message)
        except smtplib.SMTPException as error:
            return SendResult(ok=False, error=f"{type(error).__name__}: {error}"[:2000], permanent=_is_permanent(error))
        except OSError as error:  # つながらない・名前を引けない・時間切れ
            return SendResult(ok=False, error=f"{type(error).__name__}: {error}"[:2000], permanent=False)
        return SendResult(ok=True, provider_message_id=message["Message-ID"])
