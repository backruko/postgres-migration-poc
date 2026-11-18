# tests/test_mfa_cutover.py
"""
mfa の切り替え（アダプターの M.1 組み合わせ・M.2 読み直し。GO-LIVE.md の J.）のテスト。DB は使わない。
- M.1: Excel の txt を元に、新しいシステムが出す知らせ（同じ作業・3 分以内。照合と同じ決まり・別名の表）を置き換え、
  その音（Excel の Mplay）を新しい時刻へ移す。Excel だけの知らせは音つきのまま、Shell・知らせの無い音・ほかの命令はそのまま。
  いまより前の行は書かない。日時を読めない行は先頭にそのまま
- run: [output] の新しいシステムだけのファイルと照合の写しは、切り替えの前と同じ。本物のファイルの写しは live_… の名前で、
  照合の写しと取り違えない。Excel の txt が無ければ何も書かない（終了コード 6）。dry-run は書かず、mfa も動かさない
- M.2: 前後 2 分に行の無い分まで待って MFA.exe /R。失敗・静かな分が無いと終了コード 7。終わらない MFA.exe は止めない
"""
from datetime import datetime
from pathlib import Path

import pytest

from tests.test_mfa_adapter import JST, adapter, api, settings  # noqa: F401  （api は fixture。名前で使う）

parity_report = adapter.parity_report
NOW = datetime(2026, 9, 16, 2, 0, tzinfo=JST)

EXCEL = "\r\n".join([
    "注意: 手で足した行",                                                         # 日時を読めない行（先頭にそのまま）
    "26/09/15 08:00 Msg    【OLD】過去の作業を【9時まで】に実施してください",   # いまより前（書かない）
    r"26/09/16 09:40 Mplay  Repeat E:\sound\em\urgent.wav",                       # 下の知らせの音（緊急）
    "26/09/16 09:40 Msg    【EFGH】サンプル銀行自振送信を【11時まで】に実施してください",  # 新しいシステムが 09:41 に出す
    r"26/09/16 13:31 Mplay  Repeat E:\sound\gm\e_警報.wav",                       # スクリプトの音（知らせの無い音）
    r"26/09/16 13:31 Shell  D:\EDIAPI\KSAI\KsaiJifriSend.bat",                    # スクリプト
    r"26/09/16 15:00 Mplay  Repeat E:\sound\x\a.wav",
    "26/09/16 15:00 Msg    【WXYZ】入金確認を【17時まで】に実施してください",     # 新しいシステムが出さない知らせ
    r"26/09/16 20:00 Exec   C:\tools\x.exe",                                      # ほかの命令
]) + "\r\n"

NEW = "\r\n".join([
    r"26/09/16 09:41 Mplay  Repeat E:\default.wav",
    "26/09/16 09:41 Msg    【EFGH】サンプル銀行自振送信を【11時まで】に実施してください",
    r"26/09/16 22:00 Mplay  Repeat E:\default.wav",
    "26/09/16 22:00 Msg    【ABCD】夜間～取込を【翌日6:30まで】に実施してください",
]) + "\r\n"


def lines_of(text: str) -> list[str]:
    assert text.endswith("\r\n") and "\n" not in text.replace("\r\n", "")
    return text.split("\r\n")[:-1]


# ==========================================
# M.1 組み合わせ
# ==========================================
def test_covered_prompts_are_replaced_with_the_excel_sound_and_everything_else_passes(tmp_path):
    text, counts = adapter.combine(EXCEL, NEW, now=NOW, tolerance=3, aliases={})
    assert lines_of(text) == [
        "注意: 手で足した行",
        r"26/09/16 09:41 Mplay  Repeat E:\sound\em\urgent.wav",   # Excel の音（緊急）が、新しい知らせの時刻へ
        "26/09/16 09:41 Msg    【EFGH】サンプル銀行自振送信を【11時まで】に実施してください",
        r"26/09/16 13:31 Mplay  Repeat E:\sound\gm\e_警報.wav",
        r"26/09/16 13:31 Shell  D:\EDIAPI\KSAI\KsaiJifriSend.bat",
        r"26/09/16 15:00 Mplay  Repeat E:\sound\x\a.wav",
        "26/09/16 15:00 Msg    【WXYZ】入金確認を【17時まで】に実施してください",
        r"26/09/16 20:00 Exec   C:\tools\x.exe",
        r"26/09/16 22:00 Mplay  Repeat E:\default.wav",            # 新しいシステムだけ: 既定の音
        "26/09/16 22:00 Msg    【ABCD】夜間～取込を【翌日6:30まで】に実施してください",
    ]
    assert (counts.replaced, counts.excel_only, counts.new_only, counts.passed, counts.past, counts.timeless) == (1, 1, 1, 3, 1, 1)
    assert counts.by_command == {"Mplay": 1, "Shell": 1, "Exec": 1}


def test_a_prompt_further_than_the_tolerance_rings_from_both_and_aliases_join_renamed_tasks():
    excel = "26/09/16 09:37 Msg    【EFGH】サンプル銀行自振送信を【11時まで】に実施してください\r\n"  # 4 分ずれ
    text, counts = adapter.combine(excel, NEW, now=NOW, tolerance=3, aliases={})
    assert (counts.replaced, counts.excel_only) == (0, 1)  # 置き換えない: 両方鳴る（鳴らないよりよい）
    assert sum("【EFGH】" in line for line in lines_of(text)) == 2

    renamed = "26/09/16 09:41 Msg    【EFGH】自振データ送信を【11時まで】に実施してください\r\n"
    alias = {(parity_report.normalize("EFGH"), parity_report.normalize("自振データ送信")):
             (parity_report.normalize("EFGH"), parity_report.normalize("サンプル銀行自振送信"))}
    text, counts = adapter.combine(renamed, NEW, now=NOW, tolerance=3, aliases=alias)
    assert counts.replaced == 1 and "自振データ送信を" not in text


def test_a_prompt_without_a_sound_stays_without_one_and_two_sounds_move_together():
    no_sound = "26/09/16 09:41 Msg    【EFGH】サンプル銀行自振送信を【11時まで】に実施してください\r\n"
    text, _ = adapter.combine(no_sound, NEW, now=NOW, tolerance=3, aliases={})
    assert lines_of(text)[0].startswith("26/09/16 09:41 Msg")  # 既定の音も付けない（Excel のとおり）
    two = (r"26/09/16 09:40 Mplay  Repeat E:\a.wav" "\r\n" r"26/09/16 09:40 Mplay  Repeat E:\b.wav" "\r\n"
           "26/09/16 09:40 Msg    【EFGH】サンプル銀行自振送信を【11時まで】に実施してください\r\n")
    text, _ = adapter.combine(two, NEW, now=NOW, tolerance=3, aliases={})
    assert lines_of(text)[:3] == [r"26/09/16 09:41 Mplay  Repeat E:\a.wav", r"26/09/16 09:41 Mplay  Repeat E:\b.wav",
                                  "26/09/16 09:41 Msg    【EFGH】サンプル銀行自振送信を【11時まで】に実施してください"]


# ==========================================
# run（設定の [cutover]）
# ==========================================
def cutover(tmp_path: Path, **kw):
    data = dict(excel_file=tmp_path / "excel" / "mfa.txt", live_file=tmp_path / "mfa" / "mfa.txt", mfa_exe=None,
                reload_args=("/R",), quiet_minutes=2, max_wait_minutes=30, tolerance_minutes=3, aliases_file=None)
    data.update(kw)
    return adapter.Cutover(**data)


def put_excel(tmp_path: Path, text: str = EXCEL) -> None:
    (tmp_path / "excel").mkdir(exist_ok=True)
    (tmp_path / "excel" / "mfa.txt").write_bytes(text.encode("cp932"))


def test_run_writes_the_live_file_and_keeps_the_parity_inputs_as_before(tmp_path, api):
    before = tmp_path / "before"
    adapter.run(settings(before, api.url), now=NOW)  # 切り替えの前（組み合わせない）
    put_excel(tmp_path)
    s = settings(tmp_path, api.url, cutover=cutover(tmp_path))
    assert adapter.run(s, now=NOW) == 0
    # 照合が見比べる、新しいシステムだけのファイル（[output]）は、切り替えの前と同じ
    assert (tmp_path / "out" / "mfa.txt").read_bytes() == (before / "out" / "mfa.txt").read_bytes()
    live = (tmp_path / "mfa" / "mfa.txt").read_bytes().decode("cp932")
    assert r"KsaiJifriSend.bat" in live and "【WXYZ】" in live and "【ABCD】" in live
    copies = sorted(p.name for p in (tmp_path / "archive").iterdir())
    assert copies == ["access_check_20260916_020000.csv", "live_mfa_20260916_020000.txt", "mfa_20260916_020000.txt"]
    # 照合は mfa_YYYYMMDD_HHMMSS.txt だけを新の写しとして拾う（本物のファイルの写しと取り違えない）
    pattern = parity_report._copy_pattern("mfa.txt")
    assert [name for name in copies if pattern.match(name)] == ["mfa_20260916_020000.txt"]


def test_a_missing_excel_file_writes_nothing(tmp_path, api):
    s = settings(tmp_path, api.url, cutover=cutover(tmp_path))
    with pytest.raises(adapter.AdapterError) as error:
        adapter.run(s, now=NOW)
    assert error.value.code == 6
    assert not (tmp_path / "out").exists() and not (tmp_path / "mfa").exists() and not (tmp_path / "archive").exists()


def test_dry_run_prints_the_combined_file_and_starts_nothing(tmp_path, api, capsys, monkeypatch):
    put_excel(tmp_path)
    monkeypatch.setattr(adapter, "_launch", lambda args: pytest.fail(f"started {args}"))
    s = settings(tmp_path, api.url, cutover=cutover(tmp_path, mfa_exe=r"D:\mfa\MFA.exe"))
    assert adapter.run(s, dry_run=True, now=NOW) == 0
    assert "KsaiJifriSend.bat" in capsys.readouterr().out
    assert not (tmp_path / "mfa").exists() and not (tmp_path / "out").exists()


# ==========================================
# M.2 読み直し
# ==========================================
def at(hh: int, mm: int) -> datetime:
    return datetime(2026, 9, 16, hh, mm, tzinfo=JST)


def test_the_quiet_minute_has_no_line_within_two_minutes_either_side():
    times = [at(2, 0), at(2, 3)]
    assert adapter.quiet_minute(times, start=at(2, 0), quiet=2, max_wait=30) == at(2, 6)  # 2 分ちょうども「近い」
    assert adapter.quiet_minute([], start=datetime(2026, 9, 16, 2, 0, 40, tzinfo=JST), quiet=2, max_wait=30) == at(2, 0)
    busy = [at(2, m) for m in range(0, 40, 3)]
    assert adapter.quiet_minute(busy, start=at(2, 0), quiet=2, max_wait=30) is None


def test_reload_waits_for_the_quiet_minute_then_runs_mfa(tmp_path, api, monkeypatch):
    put_excel(tmp_path, r"26/09/16 02:01 Shell  D:\x.bat" "\r\n")
    slept, started = [], []
    monkeypatch.setattr(adapter, "_sleep", slept.append)
    monkeypatch.setattr(adapter, "_launch", lambda args: started.append(args) or 0)
    s = settings(tmp_path, api.url, cutover=cutover(tmp_path, mfa_exe=r"D:\mfa\MFA.exe"))
    assert adapter.run(s, now=NOW) == 0
    assert slept == [4 * 60] and started == [[r"D:\mfa\MFA.exe", "/R"]]  # 02:01 の行の後ろ 2 分を空けて 02:04


@pytest.mark.parametrize(("launch", "failed"), [(lambda args: 3, True), (lambda args: None, False)])
def test_reload_failures_exit_with_seven_but_a_still_running_mfa_is_left_alone(tmp_path, launch, failed, monkeypatch):
    monkeypatch.setattr(adapter, "_launch", launch)
    cut = cutover(tmp_path, mfa_exe="MFA.exe")
    if failed:
        with pytest.raises(adapter.AdapterError) as error:
            adapter.reload_mfa(cut, [], now=NOW)
        assert error.value.code == 7
    else:
        adapter.reload_mfa(cut, [], now=NOW)  # 終わらない: 止めず、警告だけ

    def missing(args):
        raise FileNotFoundError(args[0])

    monkeypatch.setattr(adapter, "_launch", missing)
    with pytest.raises(adapter.AdapterError) as error:
        adapter.reload_mfa(cut, [], now=NOW)
    assert error.value.code == 7


def test_no_quiet_minute_means_no_reload(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter, "_launch", lambda args: pytest.fail("must not start"))
    cut = cutover(tmp_path, mfa_exe="MFA.exe", max_wait_minutes=5)
    with pytest.raises(adapter.AdapterError) as error:
        adapter.reload_mfa(cut, [at(2, m) for m in range(10)], now=NOW)
    assert error.value.code == 7
    adapter.reload_mfa(cutover(tmp_path), [], now=NOW)  # mfa_exe が空なら、読み直さない（手で）


# ==========================================
# 設定
# ==========================================
def test_cutover_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("OPS_API_KEY", "ops_abc")
    base = "[api]\nbase_url = http://10.0.0.100/api\nkey_env = OPS_API_KEY\n[output]\ndir = out\n[parity]\naliases = parity_aliases.csv\ntolerance_minutes = 4\n"
    ini = tmp_path / "mfa_adapter.ini"
    ini.write_text(base + "[cutover]\nenabled = false\nexcel_file = x\nlive_file = y\n", encoding="utf-8")
    assert adapter.load_settings(ini).cutover is None  # off の間は、いままでどおり
    ini.write_text(base + "[cutover]\nenabled = true\nexcel_file = excel\\mfa.txt\nlive_file = D:\\mfa\\mfa.txt\n"
                   "mfa_exe = D:\\mfa\\MFA.exe\n", encoding="utf-8")
    cut = adapter.load_settings(ini).cutover
    assert cut is not None
    assert (cut.mfa_exe, cut.reload_args, cut.quiet_minutes, cut.max_wait_minutes, cut.tolerance_minutes) == (
        r"D:\mfa\MFA.exe", ("/R",), 2, 30, 4)
    assert cut.aliases_file == tmp_path / "parity_aliases.csv"  # 照合と同じ別名の表（設定ファイルの場所から）
    ini.write_text(base + "[cutover]\nenabled = true\nexcel_file = x\n", encoding="utf-8")
    with pytest.raises(adapter.AdapterError) as error:
        adapter.load_settings(ini)
    assert error.value.code == 2
    example = Path(__file__).resolve().parent.parent / "adapters" / "mfa" / "mfa_adapter.ini.example"
    assert adapter.load_settings(example).cutover is None  # 見本は切り替えの前（off）
