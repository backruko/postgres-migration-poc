# tests/test_mfa_parity.py
"""
新旧 mfa.txt の照合（adapters/mfa/parity_report.py。並行稼働 P.3a）のテスト。DB は使わない。
旧の行は、いまの mfa.txt の実物の形（命令の後ろの空白の数がばらばら、全角空白と「※」の付け足し、Shell・Mplay の行）。
- 一致: 同じ顧客・同じ作業・開始の差 3 分以内（日付をまたいでも）。1 対 1（同じ作業が 2 回なら 2 つの対応が要る）
- 時刻ずれ・欠け → NG（欠けには同じ顧客・同じ時刻の新の行を候補として出す）。余り → 要確認（NG にしない）
- 別名の表で旧の名前を新の名前に読み替える。時間帯（新を作った時刻から 24 時間）の外は見ない
- アダプターが書いた mfa.txt は、そのまま照合しても全部一致する（2 つの道具の形がそろっている）
"""
import importlib.util
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

_dir = Path(__file__).resolve().parent.parent / "adapters" / "mfa"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _dir / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclass は sys.modules のモジュールを引く
    spec.loader.exec_module(module)
    return module


parity = _load("parity_report")
adapter = _load("mfa_sync_adapter")

JST = timezone(timedelta(hours=9))
START = datetime(2026, 9, 16, 2, 0, tzinfo=JST)

LEGACY = "\r\n".join([
    r"26/09/16 09:41 Mplay  Repeat E:\sound\gm\e_警報.wav",
    "26/09/16 09:41 Msg    【EFGH】サンプル銀行自振送信を【11時まで】に実施してください　※手動作業の確認メッセージ",
    r"26/09/16 13:31 Shell  D:\EDIAPI\KSAI\KsaiJifriSend.bat",
    "26/09/16 15:30 Msg  【ABCD】月次ファイル送信を【17時まで】に実施してください",
    "",
]) + "\r\n"


def kinds(report):
    return [(r.kind, (r.legacy or r.new).client) for r in report.rows]


def test_real_legacy_lines_match_the_new_layout_within_three_minutes():
    new = "\r\n".join([
        "26/09/16 09:43 Msg    【EFGH】サンプル銀行自振送信を【11時まで】に実施してください",  # 2 分後
        "26/09/16 15:30 Msg    【ABCD】月次ファイル送信を【17:30まで】に実施してください",  # 締切の言い方だけ違う
    ]) + "\r\n"
    report = parity.compare(LEGACY, new, start=START)
    assert report.passed
    assert kinds(report) == [(parity.MATCHED, "EFGH"), (parity.OUT_OF_SCOPE, ""), (parity.MATCHED, "ABCD")]
    first = report.rows[0]
    assert first.minutes == 2 and first.note == ""
    assert "旧「17時」/ 新「17:30」" in report.rows[2].note  # 参考
    # Mplay は Msg に付くものとして数えるだけ。Shell は対象外として一覧に残す
    assert report.counts_in["旧"] == {"Mplay": 1, "Msg": 2, "Shell": 1}


def test_a_four_minute_shift_and_a_missing_line_fail_extras_only_need_review():
    new = "\r\n".join([
        "26/09/16 09:45 Msg    【EFGH】サンプル銀行自振送信を【11時まで】に実施してください",  # 4 分後 → 時刻ずれ
        "26/09/16 15:30 Msg    【ABCD】月次ファイル送付を【17時まで】に実施してください",  # 名前が違う → 欠け + 余り
        "26/09/16 20:00 Msg    【ZZZZ】夜間確認を実施してください",  # 旧に無い → 余り
    ]) + "\r\n"
    report = parity.compare(LEGACY, new, start=START)
    assert not report.passed
    assert report.count(parity.SHIFTED) == 1 and report.count(parity.MISSING) == 1 and report.count(parity.EXTRA) == 2
    shifted = next(r for r in report.rows if r.kind == parity.SHIFTED)
    assert shifted.minutes == 4
    missing = next(r for r in report.rows if r.kind == parity.MISSING)
    assert "月次ファイル送付" in missing.note and "別名の表" in missing.note  # 同じ顧客・同じ時刻の候補
    # 余りだけなら OK（旧の入力漏れのことがあるので NG にしない）
    only_extra = parity.compare(LEGACY, LEGACY + "26/09/16 20:00 Msg    【ZZZZ】夜間確認を実施してください\r\n", start=START)
    assert only_extra.passed and only_extra.count(parity.EXTRA) == 1


def test_aliases_map_a_legacy_name_to_the_new_one(tmp_path):
    table = tmp_path / "parity_aliases.csv"
    table.write_bytes("旧の顧客,旧の作業,新の顧客,新の作業\r\nABCD,月次ファイル送信,,月次ファイル送付\r\n".encode("cp932"))  # Excel の保存
    new = LEGACY.replace("月次ファイル送信", "月次ファイル送付")
    report = parity.compare(LEGACY, new, start=START, aliases=parity.load_aliases(table))
    assert report.passed and report.count(parity.MATCHED) == 2
    bad = tmp_path / "bad.csv"
    bad.write_text("旧の顧客,旧の作業,新の顧客,新の作業\nABCD,月次\n", encoding="utf-8-sig")
    with pytest.raises(parity.ParityError, match="2 行目"):
        parity.load_aliases(bad)


def test_one_to_one_across_midnight_and_only_inside_the_window():
    legacy = "\r\n".join([
        "26/09/16 10:00 Msg    【EFGH】照合を実施してください",
        "26/09/16 14:00 Msg    【EFGH】照合を実施してください",  # 同じ作業が 2 回
        "26/09/16 23:59 Msg    【EFGH】夜間送信を実施してください",
        "26/09/16 01:00 Msg    【EFGH】前の晩の分を実施してください",  # 時間帯の前（前の晩のファイルが受け持つ）
        "26/09/17 02:00 Msg    【EFGH】次の晩の分を実施してください",  # 時間帯の後
    ]) + "\r\n"
    new = "\r\n".join([
        "26/09/16 10:01 Msg    【EFGH】照合を実施してください",
        "26/09/17 00:01 Msg    【EFGH】夜間送信を実施してください",  # 日付をまたいで 2 分後
    ]) + "\r\n"
    report = parity.compare(legacy, new, start=START)
    assert [(r.kind, r.legacy and r.legacy.number) for r in report.rows] == [
        (parity.MATCHED, 1), (parity.MISSING, 2), (parity.MATCHED, 3),
    ]
    assert report.outside == {"旧": 2, "新": 0}


def test_unreadable_lines_and_sound_only_lines_are_listed_for_review():
    legacy = "\r\n".join([
        "26/09/16 25:00 Msg    【EFGH】時刻の誤り",
        "これは mfa の行ではない",
        r"26/09/16 08:00 Mplay  Repeat E:\sound\朝.wav",
    ]) + "\r\n"
    report = parity.compare(legacy, "", start=START)
    assert report.passed and report.count(parity.UNREADABLE) == 2 and report.count(parity.SOUND_ONLY) == 1


def test_the_adapters_own_file_matches_itself():
    """アダプターの書く mfa.txt（音の行つき）を新にも旧にもすると、全部一致する"""
    s = adapter.Settings(
        base_url="http://x", api_key="k", days=1, categories=("adhoc",), timeout_seconds=1, output_dir=Path("."),
        mfa_file="mfa.txt", csv_file="c.csv", archive_dir=None, archive_keep_days=0, sound=r"E:\sound\gm\e_警報.wav",
        message=adapter.DEFAULT_MESSAGE, message_no_deadline=adapter.DEFAULT_MESSAGE_NO_DEADLINE,
    )
    items = [{"scheduled_start": (START + timedelta(hours=h)).isoformat(), "deadline_at": (START + timedelta(hours=h + 2)).isoformat(),
              "client_code": "EFGH", "title": f"作業{h}〜確認", "status": "pending"} for h in (1, 5, 23)]
    text = adapter.to_cp932(adapter.render_mfa(items, s), "mfa.txt").decode("cp932")
    report = parity.compare(text, text, start=START)
    assert report.passed and report.count(parity.MATCHED) == 3 and report.count(parity.SOUND_ONLY) == 0


def test_main_writes_csv_and_summary_and_returns_exit_codes(tmp_path):
    legacy = tmp_path / "mfa.txt"
    legacy.write_bytes(LEGACY.encode("cp932"))
    new = tmp_path / "mfa_20260916_020000.txt"  # アーカイブの名前の日時が時間帯の始め
    new.write_bytes(LEGACY.encode("cp932"))
    out = tmp_path / "parity"
    assert parity.main(["--legacy", str(legacy), "--new", str(new), "--out", str(out)]) == 0
    raw = (out / "parity_20260916_0200.csv").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # Excel で開ける UTF-8
    rows = raw.decode("utf-8-sig").split("\r\n")
    assert rows[0].startswith("判定,種類,旧の時刻,新の時刻,ずれ（分）") and rows[1].startswith("OK,一致,2026-09-16 09:41,2026-09-16 09:41,+0,EFGH")
    summary = (out / "parity_20260916_0200_summary.txt").read_text(encoding="utf-8-sig")
    assert "判定: OK" in summary and "2026-09-16 02:00 ～ 2026-09-17 02:00" in summary
    new.write_bytes("26/09/16 09:41 Msg    【EFGH】サンプル銀行自振送信を実施してください\r\n".encode("cp932"))
    assert parity.main(["--legacy", str(legacy), "--new", str(new), "--out", str(out)]) == 1
    assert "判定: NG（旧の Msg の行のうち 1 行に" in (out / "parity_20260916_0200_summary.txt").read_text(encoding="utf-8-sig")
    # --window-start は名前の日時より優先。ファイルが無い・文字コードが違う → 2
    assert parity.main(["--legacy", str(legacy), "--new", str(new), "--out", str(out), "--window-start", "2026-09-16T10:00", "--hours", "1"]) == 0
    assert parity.main(["--legacy", str(tmp_path / "none.txt"), "--new", str(new), "--out", str(out)]) == 2
    (tmp_path / "bin.txt").write_bytes(b"\x82\xff\xfe")
    assert parity.main(["--legacy", str(tmp_path / "bin.txt"), "--new", str(new), "--out", str(out)]) == 2


# ---------------------------------------------------------------------------
# 毎晩の写しと期間のまとめ（P.3c）
# ---------------------------------------------------------------------------
def _msg(day: int, task: str, hhmm: str = "09:41") -> bytes:
    return f"26/09/{day:02d} {hhmm} Msg    【EFGH】{task}を【11時まで】に実施してください\r\n".encode("cp932")


def _setup(tmp_path: Path, *, aliases: bool = False) -> Path:
    """アダプターと同じ設定ファイル（相対のパスは設定ファイルの場所から）"""
    ini = tmp_path / "mfa_adapter.ini"
    ini.write_text(
        "[api]\nbase_url = http://x\nkey_env = OPS_API_KEY\n"
        "[output]\ndir = out\narchive_dir = archive\n"
        "[parity]\nlegacy_file = legacy/mfa.txt\nout_dir = parity\n" + ("aliases = parity_aliases.csv\n" if aliases else ""),
        encoding="utf-8")
    (tmp_path / "archive").mkdir()
    (tmp_path / "legacy").mkdir()
    return ini


def test_nightly_copies_the_legacy_file_next_to_the_adapters_newest_copy(tmp_path):
    ini = _setup(tmp_path)
    archive = tmp_path / "archive"
    (archive / "mfa_20260915_020000.txt").write_bytes(_msg(15, "前の晩"))
    (archive / "mfa_20260916_020000.txt").write_bytes(_msg(16, "照合"))
    (archive / "access_check_20260916_020000.csv").write_bytes(b"x")  # CSV の写しは関係ない
    (tmp_path / "legacy" / "mfa.txt").write_bytes(_msg(16, "照合", "09:42"))
    cfg = parity.load_config(ini)
    assert cfg.archive_dir == tmp_path / "archive" and cfg.out_dir == tmp_path / "parity"
    report = parity.nightly(cfg, now=datetime(2026, 9, 16, 2, 15, tzinfo=JST))
    copy = archive / "legacy_mfa_20260916_020000.txt"
    assert report.passed and report.count(parity.MATCHED) == 1 and copy.read_bytes() == _msg(16, "照合", "09:42")
    assert [p.name for _, p in parity.new_copies(cfg)] == ["mfa_20260915_020000.txt", "mfa_20260916_020000.txt"]  # 旧の写しは入らない
    # 昼間にもう一度動かしても、その晩の旧の写しは置き換えない
    (tmp_path / "legacy" / "mfa.txt").write_bytes(_msg(16, "昼に変わった"))
    assert parity.nightly(cfg, now=datetime(2026, 9, 16, 14, 0, tzinfo=JST)).passed
    assert copy.read_bytes() == _msg(16, "照合", "09:42")
    # アダプターが昨夜動かなかった（最新の写しが 26 時間より古い）→ 入力の誤り
    with pytest.raises(parity.ParityError, match="古すぎます"):
        parity.nightly(cfg, now=datetime(2026, 9, 17, 4, 1, tzinfo=JST))
    # main: 要約をログに追記し、結果のファイルを書く
    log = tmp_path / "logs" / "parity.log"
    assert parity.main(["--nightly", "--config", str(ini), "--log-file", str(log)]) == 2  # いまの日時では写しが古い
    assert "古すぎます" in log.read_text(encoding="utf-8")


def test_nightly_needs_the_legacy_file_setting(tmp_path):
    ini = tmp_path / "mfa_adapter.ini"
    ini.write_text("[output]\ndir = out\narchive_dir = archive\n", encoding="utf-8")
    with pytest.raises(parity.ParityError, match="legacy_file"):
        parity.nightly(parity.load_config(ini))
    ini.write_text("[output]\ndir = out\n", encoding="utf-8")
    with pytest.raises(parity.ParityError, match="archive_dir"):
        parity.load_config(ini)
    # 見本の設定ファイルもそのまま読める（キーの名前が読み込みと合っている）。値は書き換える前の仮の置き場所なので確かめない
    # （D:\ の見本のパスは Linux では絶対パスにならず、設定ファイルの場所からの相対として読まれる）
    assert parity.load_config(_dir / "mfa_adapter.ini.example").legacy_file is not None


def test_rollup_recompares_every_night_with_todays_aliases(tmp_path):
    ini = _setup(tmp_path, aliases=True)
    archive = tmp_path / "archive"

    def night(day: int, legacy_task: str, new_task: str, hhmmss: str = "020000"):
        name = f"mfa_202609{day:02d}_{hhmmss}.txt"
        (archive / name).write_bytes(_msg(day, new_task, "09:41" if hhmmss == "020000" else "15:00"))
        (archive / f"legacy_{name}").write_bytes(_msg(day, legacy_task, "09:41" if hhmmss == "020000" else "15:00"))

    night(15, "照合", "照合")
    night(16, "自振送信", "自振送信（サンプル銀行）")  # 名前の違い: 別名が無ければ欠け + 余り
    night(17, "照合", "照合")
    night(17, "昼の別作業", "まったく違う", "140000")  # 同じ日の 2 組目（昼に手で実行）は使わない
    (archive / "mfa_20260918_020000.txt").write_bytes(_msg(18, "照合"))  # 旧の写しが無い晩 → 照合なし
    cfg = parity.load_config(ini)

    days = parity.rollup(cfg, date(2026, 9, 15), date(2026, 9, 19))
    assert [(d.day.day, d.passed) for d in days] == [(15, True), (16, False), (17, True), (18, False), (19, False)]
    assert days[2].report is not None and days[2].report.new_path.name == "mfa_20260917_020000.txt"
    assert parity.main(["--rollup", "--from", "2026-09-15", "--to", "2026-09-19", "--config", str(ini)]) == 1
    out = tmp_path / "parity"
    table = (out / "parity_rollup_20260915_20260919.csv").read_bytes().decode("utf-8-sig").split("\r\n")
    assert table[0].startswith("日付,判定,見比べた時間帯,一致,時刻ずれ,欠け（旧だけ）,余り（新だけ）")
    assert table[1].startswith("2026-09-15,OK,09/15 02:00 ～ 09/16 02:00,1,0,0,0")
    assert table[4].startswith("2026-09-18,NG,照合なし")
    details = (out / "parity_rollup_20260915_20260919_details.csv").read_bytes().decode("utf-8-sig")
    assert details.count("\r\n") == 3 and "2026-09-16,NG,欠け（旧だけ）" in details and "2026-09-16,要確認,余り（新だけ）" in details
    summary = (out / "parity_rollup_20260915_20260919_summary.txt").read_text(encoding="utf-8-sig")
    assert "照合した日: 3 日、照合の無い日: 2 日、NG の日: 1 日" in summary and "2026-09-18  照合なし" in summary and "判定: NG" in summary
    assert (out / "parity_20260916_0200.csv").exists()  # 日ごとの結果も書き直す

    # 別名を足してまとめ直すと、前の晩にも効く
    (tmp_path / "parity_aliases.csv").write_text("旧の顧客,旧の作業,新の顧客,新の作業\nEFGH,自振送信,,自振送信（サンプル銀行）\n", encoding="utf-8-sig")
    assert parity.main(["--rollup", "--from", "2026-09-15", "--to", "2026-09-17", "--config", str(ini)]) == 0
    assert "判定: OK（3 日すべてで" in (out / "parity_rollup_20260915_20260917_summary.txt").read_text(encoding="utf-8-sig")
    with pytest.raises(SystemExit):
        parity.main(["--rollup", "--from", "2026-09-15", "--config", str(ini)])  # --to が無い
    assert parity.main(["--rollup", "--from", "2026-09-17", "--to", "2026-09-15", "--config", str(ini)]) == 2


# ==========================================
# 照合の結果をまるごと固定する（R.6 で compare を分ける前に。分けた後も同じ結果になること）
# ==========================================
SNAPSHOT_OLD = "\r\n".join([
    r"26/09/16 09:00 Mplay  Repeat E:\sound\a.wav",                       # 1 同じ分に Msg がある音（一覧に出ない）
    "26/09/16 09:00 Msg    【AAAA】照合を【10時まで】に実施してください",   # 2 一致（3 分。締切の言い方が違う）
    "26/09/16 11:00 Msg    【AAAA】送信を実施してください",                 # 3 時刻ずれ（10 分）
    "26/09/16 12:00 Msg    【BBBB】集計を実施してください",                 # 4 欠け（同じ顧客・同じ時刻の新の行が 4 つ）
    r"26/09/16 13:00 Mplay  Repeat E:\sound\b.wav",                       # 5 音だけ
    r"26/09/16 14:00 Shell  D:\x.bat",                                    # 6 対象外
    "26/09/16 99:00 Msg    【AAAA】壊れた時刻",                              # 7 読めない（新の 7 行目と同じ行番号）
    "26/09/17 03:00 Msg    【AAAA】次の晩の分を実施してください",           # 8 時間帯の外
    "",                                                                   # 9 空の行（数えない）
    "26/09/16 18:00 Msg    【CCCC】確認を実施してください　※手で確認",      # 10 一致（※ 以降は見ない）
]) + "\r\n"
SNAPSHOT_NEW = "\r\n".join([
    "26/09/16 09:03 Msg    【AAAA】照合を【10:00まで】に実施してください",  # 1
    "26/09/16 11:10 Msg    【AAAA】送信を実施してください",                 # 2
    "26/09/16 12:00 Msg    【BBBB】集計1を実施してください",                # 3 余り
    "26/09/16 12:01 Msg    【BBBB】集計2を実施してください",                # 4 余り
    "26/09/16 12:02 Msg    【BBBB】集計3を実施してください",                # 5 余り
    "26/09/16 12:03 Msg    【BBBB】集計4を実施してください",                # 6 余り（欠けの候補に出るのは 3 つまで）
    "26/09/16 99:00 Msg    【AAAA】壊れた時刻",                              # 7 読めない
    r"26/09/16 16:00 Exec   C:\y.exe",                                    # 8 対象外
    "26/09/16 01:00 Msg    【AAAA】前の晩の分を実施してください",           # 9 時間帯の外
    "26/09/16 18:00 Msg    【ＣＣＣＣ】確　認を実施してください",            # 10 一致（全角・空白をそろえる）
]) + "\r\n"
UNREADABLE_NOTE = "「YY/MM/DD HH:MM 命令 引数」の形ではありません"


def rows_of(report):
    return [(r.kind, r.legacy and r.legacy.number, r.new and r.new.number, r.minutes, r.note) for r in report.rows]


def test_the_whole_report_for_every_kind_of_line():
    report = parity.compare(SNAPSHOT_OLD, SNAPSHOT_NEW, start=START)
    hints = " / ".join(f"「【BBBB】集計{i}を実施してください」" for i in (1, 2, 3))
    assert rows_of(report) == [
        (parity.MATCHED, 2, 1, 3, "締切の言い方が違う: 旧「10時」/ 新「10:00」（参考）"),
        (parity.SHIFTED, 3, 2, 10, "同じ作業が 3 分を超えてずれています"),
        (parity.MISSING, 4, None, None, f"同じ顧客・同じ時刻の新の行: {hints}（名前の違いなら別名の表へ）"),
        (parity.EXTRA, None, 3, None, ""),
        (parity.EXTRA, None, 4, None, ""),
        (parity.EXTRA, None, 5, None, ""),
        (parity.EXTRA, None, 6, None, ""),
        (parity.SOUND_ONLY, 5, None, None, "同じ分に Msg の行が無い音だけの行"),
        (parity.OUT_OF_SCOPE, 6, None, None, "Shell の行は新しいシステムが書かない（mfa がそのまま動かす）"),
        (parity.OUT_OF_SCOPE, None, 8, None, "Exec の行は新しいシステムが書かない（mfa がそのまま動かす）"),
        (parity.MATCHED, 10, 10, 0, ""),
        (parity.UNREADABLE, 7, None, None, UNREADABLE_NOTE),  # 時刻の無い行は最後。同じ行番号なら旧が先
        (parity.UNREADABLE, None, 7, None, UNREADABLE_NOTE),
    ]
    assert report.counts_in == {"旧": {"Mplay": 2, "Msg": 4, "Shell": 1}, "新": {"Msg": 7, "Exec": 1}}
    assert report.outside == {"旧": 1, "新": 1}
    assert not report.passed  # 時刻ずれと欠けがある
    assert (report.start, report.end, report.tolerance) == (START, START + timedelta(hours=24), 3)


def _msg_line(hhmm: str, task: str = "照合", client: str = "AAAA") -> str:
    return f"26/09/16 {hhmm} Msg    【{client}】{task}を実施してください\r\n"


def test_aliases_rename_the_old_file_only():
    aliases = {("AAAA", "照合"): ("AAAA", "照合確認")}
    # 新のファイルが旧の名前のまま: 旧だけが読み替わるので、欠け + 余り
    kept_old_name = parity.compare(_msg_line("09:00"), _msg_line("09:00"), start=START, aliases=aliases)
    assert [(r.kind, r.legacy and r.legacy.task, r.new and r.new.task) for r in kept_old_name.rows] == [
        (parity.MISSING, "照合確認", None), (parity.EXTRA, None, "照合"),
    ]
    renamed = parity.compare(_msg_line("09:00"), _msg_line("09:00", "照合確認"), start=START, aliases=aliases)
    assert [r.kind for r in renamed.rows] == [parity.MATCHED]


def test_the_tolerance_is_inclusive_and_named_in_the_shift_note():
    def kind_and_note(new_hhmm: str, **kw) -> tuple:
        [row] = parity.compare(_msg_line("10:00"), _msg_line(new_hhmm), start=START, **kw).rows
        return row.kind, row.minutes, row.note

    assert kind_and_note("10:03") == (parity.MATCHED, 3, "")
    assert kind_and_note("10:04") == (parity.SHIFTED, 4, "同じ作業が 3 分を超えてずれています")
    assert kind_and_note("09:57") == (parity.MATCHED, -3, "")  # 前にずれても同じ
    assert kind_and_note("10:05", tolerance=5) == (parity.MATCHED, 5, "")
    assert kind_and_note("10:06", tolerance=5) == (parity.SHIFTED, 6, "同じ作業が 5 分を超えてずれています")
