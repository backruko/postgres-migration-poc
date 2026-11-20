# tests/test_smtp_channel.py
"""
メールのチャネル（SMTP。通知の仕組み）のテスト。本物の smtplib を、テストの中の小さな SMTP サーバーにつないで確かめる
（DB は使わない）。
- 送れた: 差出人・宛先・日本語の件名と本文・Message-ID（送り先の ID として返す）。SMTP_USER があればログインする
- 宛先を断られた 5xx → 永続的な失敗。本文の一時的な拒否 4xx・つながらない → 一時的な失敗。STARTTLS が必須でサーバーが非対応 → 一時的
- どのチャネルを使うか: SMTP_HOST があれば SMTP、なければ開発は偽物・本番はなし
"""
import base64
import socket
import socketserver
import threading
from email import message_from_bytes, policy
from types import SimpleNamespace

import pytest

from src.domain_notification import channels as channels_module
from src.domain_notification.channels import FakeChannel, default_channels, describe
from src.domain_notification.models import NotificationChannel
from src.domain_notification.smtp import SmtpChannel, SmtpSettings
from src.infrastructure.config import settings


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        server = self.server
        write = lambda line: self.wfile.write((line + "\r\n").encode())  # noqa: E731
        write("220 stub ESMTP")
        mail = {"rcpt": [], "auth": None}
        while True:
            raw = self.rfile.readline()
            if not raw:
                return
            line = raw.decode().rstrip("\r\n")
            verb = line.split(" ", 1)[0].upper()
            if verb in ("EHLO", "HELO"):
                self.wfile.write(b"250-stub\r\n250-AUTH PLAIN\r\n250 8BITMIME\r\n")
            elif verb == "AUTH":
                mail["auth"] = base64.b64decode(line.split(" ")[2]).split(b"\0")[1:]
                write("235 ok")
            elif verb == "MAIL":
                write("250 ok")
            elif verb == "RCPT":
                address = line.split(":", 1)[1].strip("<> ")
                if address.startswith("nobody"):
                    write("550 no such user")
                else:
                    mail["rcpt"].append(address)
                    write("250 ok")
            elif verb == "DATA":
                write("354 go")
                data = b""
                while not data.endswith(b"\r\n.\r\n"):
                    data += self.rfile.readline()
                if server.data_reply.startswith("250"):  # pyright: ignore[reportAttributeAccessIssue]
                    server.received.append((mail, data[:-5]))  # pyright: ignore[reportAttributeAccessIssue]
                write(server.data_reply)  # pyright: ignore[reportAttributeAccessIssue]
            elif verb == "QUIT":
                write("221 bye")
                return
            else:
                write("250 ok")


@pytest.fixture
def smtp_server():
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Handler)
    server.daemon_threads = True
    server.received = []  # pyright: ignore[reportAttributeAccessIssue]
    server.data_reply = "250 queued"  # pyright: ignore[reportAttributeAccessIssue]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def notification(subject="【遅れ】MOCK 業務 / 予定 11/12 締切 14:00", body="締切を過ぎても完了していないオーダーがあります。\n顧客: MOCK"):
    return SimpleNamespace(subject=subject, body=body)


def destination(address="ops@example.com"):
    return SimpleNamespace(address=address)


def channel(server, **kwargs) -> SmtpChannel:
    return SmtpChannel(SmtpSettings(host="127.0.0.1", port=server.server_address[1], timeout_seconds=5, **kwargs))


def test_a_message_is_sent_with_japanese_subject_and_body(smtp_server):
    result = channel(smtp_server, from_address="alarm@ops.example").send(destination(), notification())  # pyright: ignore[reportArgumentType]
    assert result.ok and (result.provider_message_id or "").endswith("@ops.example>")
    [(mail, data)] = smtp_server.received
    assert mail["rcpt"] == ["ops@example.com"] and mail["auth"] is None
    message = message_from_bytes(data, policy=policy.default)
    assert message["From"] == "alarm@ops.example" and message["To"] == "ops@example.com"
    assert message["Subject"] == "【遅れ】MOCK 業務 / 予定 11/12 締切 14:00"
    assert message["Message-ID"] == result.provider_message_id
    assert message.get_content().startswith("締切を過ぎても完了していないオーダーがあります。")  # pyright: ignore[reportAttributeAccessIssue]


def test_login_only_when_a_user_is_set(smtp_server):
    channel(smtp_server, user="alarm", password="s3cret").send(destination(), notification())  # pyright: ignore[reportArgumentType]
    [(mail, _)] = smtp_server.received
    assert mail["auth"] == [b"alarm", b"s3cret"]


def test_failures_are_permanent_only_for_refused_recipients_and_rejected_messages(smtp_server):
    refused = channel(smtp_server).send(destination("nobody@example.com"), notification())  # pyright: ignore[reportArgumentType]
    assert not refused.ok and refused.permanent and "550" in (refused.error or "")

    smtp_server.data_reply = "451 try again later"
    later = channel(smtp_server).send(destination(), notification())  # pyright: ignore[reportArgumentType]
    assert not later.ok and not later.permanent and "451" in (later.error or "")

    smtp_server.data_reply = "554 message rejected"
    rejected = channel(smtp_server).send(destination(), notification())  # pyright: ignore[reportArgumentType]
    assert not rejected.ok and rejected.permanent

    smtp_server.data_reply = "250 queued"
    required = channel(smtp_server, starttls=True).send(destination(), notification())  # pyright: ignore[reportArgumentType]
    assert not required.ok and not required.permanent and "STARTTLS" in (required.error or "")  # 設定を直せば届く
    assert smtp_server.received == []


def test_an_unreachable_server_is_a_temporary_failure():
    with socket.socket() as s:  # 空いているポートを 1 つ取り、閉じる（誰も待っていない）
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    result = SmtpChannel(SmtpSettings(host="127.0.0.1", port=port, timeout_seconds=2)).send(destination(), notification())  # pyright: ignore[reportArgumentType]
    assert not result.ok and not result.permanent


def test_which_channel_is_used(monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "mail.example")
    monkeypatch.setattr(settings, "smtp_user", "alarm")
    monkeypatch.setattr(settings, "smtp_password", "s3cret")
    smtp = default_channels()[NotificationChannel.EMAIL]
    assert isinstance(smtp, SmtpChannel) and (smtp.settings.host, smtp.settings.port) == ("mail.example", 587)
    text = describe({NotificationChannel.EMAIL: smtp})
    assert "mail.example:587" in text and "alarm" in text and "s3cret" not in text  # パスワードはログに出さない

    monkeypatch.setattr(settings, "smtp_host", None)
    assert isinstance(default_channels()[NotificationChannel.EMAIL], FakeChannel)  # 開発・テスト
    monkeypatch.setattr(settings, "environment", "production")
    assert default_channels() == {}  # 本番で設定がない: 偽物で「送れた」にしない
    assert "none" in describe({})
    assert channels_module  # 読み込みの確かめ


def test_empty_environment_values_fall_back_to_the_defaults(monkeypatch):
    """docker compose は未設定の変数を空で渡す: 空なら既定値（SMTP_PORT= で起動に失敗しない）"""
    from src.infrastructure.config import Settings

    for name in ("SMTP_HOST", "SMTP_PORT", "SMTP_STARTTLS", "SMTP_FROM_ADDRESS"):
        monkeypatch.setenv(name, "")
    loaded = Settings()  # pyright: ignore[reportCallIssue]
    assert (loaded.smtp_host, loaded.smtp_port, loaded.smtp_starttls, loaded.smtp_from_address) == (
        None, 587, None, "system-noreply@example.com",
    )
    monkeypatch.setenv("SMTP_PORT", "25")
    monkeypatch.setenv("SMTP_STARTTLS", "0")
    loaded = Settings()  # pyright: ignore[reportCallIssue]
    assert (loaded.smtp_port, loaded.smtp_starttls) == (25, False)
