# tests/test_mfa_adapter.py
"""
mfa 連携アダプター（adapters/mfa/mfa_sync_adapter.py。並行稼働 P.2）のテスト。DB は使わない（テストの中の小さな API サーバー）。
- mfa.txt: 「YY/MM/DD HH:MM 命令（7 桁）引数」、CP932・CRLF。Msg の文面（締切の言い方: 同じ日 / 翌日 / それより後、ちょうどの時）。
  音を設定すれば同じ時刻の Mplay の行が前に入る
- CSV: 全部をダブルクォート、CP932・CRLF。状態は 未処理 / リリース済
- CP932 に無い文字（〜）は見た目の同じ文字に。API の失敗・時刻表の切れでは何も書かない。dry-run は書かない。写しを残す
"""
import importlib.util
import json
import sys
import threading
from datetime import datetime, timedelta, timezone, UTC
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

_path = Path(__file__).resolve().parent.parent / "adapters" / "mfa" / "mfa_sync_adapter.py"
_spec = importlib.util.spec_from_file_location("mfa_sync_adapter", _path)
assert _spec and _spec.loader
adapter = importlib.util.module_from_spec(_spec)
sys.modules["mfa_sync_adapter"] = adapter  # dataclass は sys.modules のモジュールを引く
_spec.loader.exec_module(adapter)

JST = timezone(timedelta(hours=9))


def item(start: datetime, deadline: datetime | None, *, client="EFGH", title="サンプル銀行自振送信", status="pending", key="k"):
    return {
        "key": key, "scheduled_start": start.isoformat(), "deadline_at": deadline.isoformat() if deadline else None,
        "client_code": client, "client_name": "顧客", "biz_code": f"{client}0001", "biz_name": "業務", "title": title,
        "status": status, "category": "adhoc", "one_off": False,
    }


FEED_ITEMS = [
    item(datetime(2026, 9, 16, 9, 41, tzinfo=JST), datetime(2026, 9, 16, 11, 0, tzinfo=JST)),
    item(datetime(2026, 9, 16, 22, 0, tzinfo=JST), datetime(2026, 9, 17, 6, 30, tzinfo=JST), client="ABCD", title="夜間〜取込", status="ready", key="k2"),
]


class FakeApi:
    """テストの中の API サーバー（GET /api/ops/schedule-feed を返す。受けた要求を残す）"""

    def __init__(self):
        self.status, self.body, self.requests = 200, {"items": FEED_ITEMS, "truncated": False, "categories": ["adhoc", "special"]}, []
        api = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                api.requests.append((self.path, self.headers.get("Authorization")))
                raw = json.dumps(api.body).encode()
                self.send_response(api.status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, format, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/api"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def api():
    fake = FakeApi()
    yield fake
    fake.close()


def settings(tmp_path: Path, url: str, **kw):
    data = dict(
        base_url=url, api_key="ops_test", days=30, categories=("adhoc", "special"), timeout_seconds=5,
        output_dir=tmp_path / "out", mfa_file="mfa.txt", csv_file="access_check.csv", archive_dir=tmp_path / "archive",
        archive_keep_days=60, sound=None, message=adapter.DEFAULT_MESSAGE, message_no_deadline=adapter.DEFAULT_MESSAGE_NO_DEADLINE,
    )
    data.update(kw)
    return adapter.Settings(**data)


def test_mfa_txt_has_the_legacy_layout_in_cp932_and_crlf(tmp_path, api):
    s = settings(tmp_path, api.url)
    assert adapter.run(s) == 0
    raw = (tmp_path / "out" / "mfa.txt").read_bytes()
    assert raw.endswith(b"\r\n") and b"\n" not in raw.replace(b"\r\n", b"")
    lines = raw.decode("cp932").split("\r\n")[:-1]
    assert lines == [
        "26/09/16 09:41 Msg    【EFGH】サンプル銀行自振送信を【11時まで】に実施してください",
        "26/09/16 22:00 Msg    【ABCD】夜間～取込を【翌日6:30まで】に実施してください",  # 〜 は CP932 の ～ に
    ]
    # API キーとクエリ
    path, auth = api.requests[0]
    assert auth == "Bearer ops_test" and path == "/api/ops/schedule-feed?days=30&categories=adhoc&categories=special"


def test_sound_line_comes_first_at_the_same_minute(tmp_path, api):
    s = settings(tmp_path, api.url, sound=r"E:\sound\gm\e_警報.wav")
    adapter.run(s)
    lines = (tmp_path / "out" / "mfa.txt").read_bytes().decode("cp932").split("\r\n")
    assert lines[0] == r"26/09/16 09:41 Mplay  Repeat E:\sound\gm\e_警報.wav"
    assert lines[1].startswith("26/09/16 09:41 Msg    【EFGH】")
    # 命令の後ろの空白は、いまの mfa.txt と同じく 7 桁にそろう
    assert lines[0].index("Repeat") == lines[1].index("【") == len("26/09/16 09:41 ") + 7


def test_access_csv(tmp_path, api):
    adapter.run(settings(tmp_path, api.url))
    raw = (tmp_path / "out" / "access_check.csv").read_bytes()
    assert raw.decode("cp932") == (
        '"2026-09-16","EFGH","EFGH0001","サンプル銀行自振送信","09:41","未処理"\r\n'
        '"2026-09-16","ABCD","ABCD0001","夜間～取込","22:00","リリース済"\r\n'
    )


def test_deadline_wording():
    start = datetime(2026, 11, 16, 9, 0, tzinfo=JST)
    assert adapter.deadline_text(start, start.replace(hour=11)) == "11時"
    assert adapter.deadline_text(start, start.replace(hour=11, minute=30)) == "11:30"
    assert adapter.deadline_text(start, start + timedelta(days=1, hours=-3)) == "翌日6時"
    assert adapter.deadline_text(start, start + timedelta(days=2)) == "11/18 9時"
    s = settings(Path("."), "http://x")
    no_deadline = item(start, None)
    assert adapter.message_of(no_deadline, s) == "【EFGH】サンプル銀行自振送信を実施してください"
    # 改行・タブは 1 行にまとめる（mfa は 1 行 1 命令）
    assert adapter.message_of(item(start, None, title="二行\nの\t予定"), s) == "【EFGH】二行 の 予定を実施してください"
    # UTC で来ても日本時間で書く
    utc = item(datetime(2026, 11, 16, 0, 30, tzinfo=UTC), datetime(2026, 11, 16, 2, 0, tzinfo=UTC))
    assert adapter.render_mfa([utc], s) == "26/11/16 09:30 Msg    【EFGH】サンプル銀行自振送信を【11時まで】に実施してください\r\n"


def test_api_failures_and_truncation_write_nothing(tmp_path, api):
    out = tmp_path / "out"
    out.mkdir()
    (out / "mfa.txt").write_bytes(b"previous\r\n")
    api.status = 401
    with pytest.raises(adapter.AdapterError) as refused:
        adapter.run(settings(tmp_path, api.url))
    assert refused.value.code == 3 and (out / "mfa.txt").read_bytes() == b"previous\r\n"
    api.status, api.body = 200, {"items": FEED_ITEMS, "truncated": True}
    with pytest.raises(adapter.AdapterError) as cut:
        adapter.run(settings(tmp_path, api.url))
    assert cut.value.code == 5 and (out / "mfa.txt").read_bytes() == b"previous\r\n"
    with pytest.raises(adapter.AdapterError) as down:
        adapter.run(settings(tmp_path, "http://127.0.0.1:9/api", timeout_seconds=2))
    assert down.value.code == 3


def test_dry_run_writes_nothing_and_archive_keeps_copies(tmp_path, api, capsys):
    assert adapter.run(settings(tmp_path, api.url), dry_run=True) == 0
    assert not (tmp_path / "out").exists() and "【EFGH】" in capsys.readouterr().out
    adapter.run(settings(tmp_path, api.url), now=datetime(2026, 11, 16, 2, 0, tzinfo=JST))
    copies = sorted(p.name for p in (tmp_path / "archive").iterdir())
    assert copies == ["access_check_20261116_020000.csv", "mfa_20261116_020000.txt"]
    assert not [p for p in (tmp_path / "out").iterdir() if p.name.endswith(".tmp")]  # 一時ファイルは残らない


def test_settings_file_and_api_key(tmp_path, monkeypatch):
    ini = tmp_path / "mfa_adapter.ini"
    ini.write_text("[api]\nbase_url = http://10.0.0.100/api/\nkey_env = OPS_API_KEY\ndays = 14\n"
                   "[output]\ndir = D:\\out\n[mfa]\nsound = E:\\s.wav\n", encoding="utf-8")
    monkeypatch.delenv("OPS_API_KEY", raising=False)
    with pytest.raises(adapter.AdapterError) as missing:
        adapter.load_settings(ini)
    assert missing.value.code == 2
    monkeypatch.setenv("OPS_API_KEY", "ops_abc")
    s = adapter.load_settings(ini)
    assert (s.base_url, s.api_key, s.days, s.categories, s.sound, s.message) == (
        "http://10.0.0.100/api", "ops_abc", 14, ("adhoc", "special"), r"E:\s.wav", adapter.DEFAULT_MESSAGE,
    )
    example = Path(__file__).resolve().parent.parent / "adapters" / "mfa" / "mfa_adapter.ini.example"
    assert adapter.load_settings(example).base_url.startswith("http://")  # 見本の設定ファイルもそのまま読める


def test_main_returns_exit_codes(tmp_path, api, monkeypatch):
    ini = tmp_path / "mfa_adapter.ini"
    ini.write_text(f"[api]\nbase_url = {api.url}\nkey_env = OPS_API_KEY\n[output]\ndir = {tmp_path / 'out'}\n", encoding="utf-8")
    monkeypatch.setenv("OPS_API_KEY", "ops_abc")
    assert adapter.main(["--config", str(ini), "--log-file", str(tmp_path / "a.log")]) == 0
    assert "時刻表: 2 件" in (tmp_path / "a.log").read_text(encoding="utf-8")
    api.status = 500
    assert adapter.main(["--config", str(ini)]) == 3
    assert adapter.main(["--config", str(tmp_path / "missing.ini")]) == 2
