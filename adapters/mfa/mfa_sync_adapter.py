"""
mfa 連携アダプター（並行稼働）。業務オーケストレーション（新しいシステム）の外に置く、使い捨ての橋渡し。

新しいシステムの API（GET /ops/schedule-feed。読み取り専用の API キー）から、これから始める作業の時刻表を読み、
- mfa が読むテキスト（mfa.txt）: 開始の時刻に「始めて」と知らせる Msg の行（任意で、同じ時刻の Mplay の行も）
- Access と見比べるための CSV: 日付・顧客コード・業務コード・予定名・開始 HH:MM・状態
を書く。どちらも Shift_JIS（CP932）・改行 CRLF（いまの Windows の仕組みと同じ）。

- 毎晩 1 回（タスク スケジューラー）と、必要なら手で実行する。並行稼働の間は、mfa が読む本物のファイルではなく、
  見比べるための別のフォルダーに書く（設定の [output] dir）
- 新しいシステムの規則（どの作業を・いつ知らせるか）は持たない。時刻表をそのまま書き写すだけ（文面の形だけを決める）
- 書くときは一時ファイルに書いてから置き換える（mfa や人が書きかけのファイルを読まない）。API が失敗したら何も書かない
- 書いたファイルの写しを archive フォルダーに日時つきで残す（新旧の照合の材料）
- 標準ライブラリだけで動く（サーバーに追加のパッケージは要らない）。Python 3.10 以上

切り替えのあと（GO-LIVE.md の J.、設定の [cutover] enabled = true）は、さらに:
- 組み合わせ: 運用の担当が Excel から書き出して置く txt（excel_file）を元に、mfa が読む本物のファイル（live_file）を組み立てる。
  新しいシステムが出す開始の知らせ（同じ作業・tolerance 分以内。照合と同じ決まり・同じ別名の表）は新しいシステムの行に
  置き換え（Excel のその知らせの音の行を一緒に移す）、新しいシステムが出さない Excel の知らせはそのまま残し、ほかの行
  （Shell のスクリプト・知らせの無い音・ほかの命令）はそのまま写す。いまより前の行は書かない
- 読み直し: 書いたあと、前後 quiet_minutes 分に行の無い分を待って「MFA.exe /R」で mfa に読み直させる
- [output] の新しいシステムだけのファイルと写しは、いままでどおり書く（照合は組み合わせる前のものと見比べる）

    python mfa_sync_adapter.py --config mfa_adapter.ini            # 書く
    python mfa_sync_adapter.py --config mfa_adapter.ini --dry-run  # 書かずに、書く内容を表示する

終了コード: 0 = 書いた（または dry-run）、2 = 設定の誤り、3 = API に失敗、4 = ファイルを書けない、5 = 時刻表が上限で切れた、
6 = Excel の txt を読めない（何も書かない）、7 = 書いたが mfa に読み直させられなかった（mfa は前のファイルのまま）
"""
from __future__ import annotations

import argparse
import configparser
import csv
import io
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 切り替えの組み合わせは、新旧の照合と同じ決まりで知らせを結ぶ（同じフォルダーの parity_report.py）
sys.path.insert(0, str(Path(__file__).resolve().parent))
import parity_report  # 上の行で、このフォルダーを探す場所に足してから読む

# 日本時間（夏時間はないので固定の +9 時間でよい。Windows の Python には時間帯の辞書が無いことがあるため zoneinfo を使わない）
JST = timezone(timedelta(hours=9), "JST")

COMMAND_WIDTH = 7  # いまの mfa.txt と同じ桁: "Mplay  " "Shell  " "Msg    "（命令の後ろを空白で 7 桁にそろえる）
DEFAULT_MESSAGE = "【{client}】{task}を【{deadline}まで】に実施してください"
DEFAULT_MESSAGE_NO_DEADLINE = "【{client}】{task}を実施してください"
STATUS_WORDS = {"pending": "未処理", "ready": "リリース済"}  # Access と見比べるときの言葉

# CP932 に無い文字を、見た目の同じ CP932 の文字に（例: 〜 は Python の cp932 では書けない）
CP932_FIXES = str.maketrans({
    "〜": "～",  # 〜 → ～
    "−": "－",  # − → －
    "‖": "∥",  # ‖ → ∥
    "¢": "￠",  # ¢ → ￠
    "£": "￡",  # £ → ￡
    "¬": "￢",  # ¬ → ￢
    "—": "―",  # — → ―
})

log = logging.getLogger("mfa_sync_adapter")


class AdapterError(Exception):
    def __init__(self, message: str, code: int):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Cutover:
    """切り替えのあとの設定（[cutover]。enabled = true のときだけ）"""
    excel_file: Path                  # 運用の担当が Excel から書き出して置く txt（J-2 で替えた置き場所）
    live_file: Path                   # mfa が読む本物のファイル（組み合わせた結果を書く）
    mfa_exe: str | None               # 読み直させる mfa の場所（空なら読み直さない）
    reload_args: tuple[str, ...]      # 読み直しの引数（既定 /R）
    quiet_minutes: int                # 読み直す分の前後に、行が無いこと（分）
    max_wait_minutes: int             # 静かな分を待つ上限（分）
    tolerance_minutes: int            # 同じ知らせとみなす開始の差（[parity] と同じ）
    aliases_file: Path | None         # 別名の表（[parity] と同じ）


@dataclass(frozen=True)
class Settings:
    base_url: str
    api_key: str
    days: int
    categories: tuple[str, ...]
    timeout_seconds: float
    output_dir: Path
    mfa_file: str
    csv_file: str
    archive_dir: Path | None
    archive_keep_days: int
    sound: str | None
    message: str
    message_no_deadline: str
    cutover: Cutover | None = None


def _load_cutover(parser: configparser.ConfigParser, base: Path) -> Cutover | None:
    """[cutover] を読む。無いか enabled が true でなければ None（並行稼働のまま。いままでどおり）"""
    if not parser.has_section("cutover") or not parser["cutover"].getboolean("enabled", False):
        return None
    cut = parser["cutover"]
    parity = parser["parity"] if parser.has_section("parity") else None
    aliases = (parity.get("aliases", "") if parity else "").strip()
    excel, live = cut.get("excel_file", "").strip(), cut.get("live_file", "").strip()
    if not excel or not live:
        raise AdapterError("[cutover] enabled = true のときは excel_file と live_file が要ります", 2)
    return Cutover(
        excel_file=base / excel,
        live_file=base / live,
        mfa_exe=cut.get("mfa_exe", "").strip() or None,
        reload_args=tuple(cut.get("reload_args", "/R").split()),
        quiet_minutes=cut.getint("quiet_minutes", 2),
        max_wait_minutes=cut.getint("max_wait_minutes", 30),
        tolerance_minutes=parity.getint("tolerance_minutes", 3) if parity else 3,
        aliases_file=base / aliases if aliases else None,
    )


def load_settings(path: Path) -> Settings:
    """設定ファイル（INI）を読む。API キーは設定ファイルに書かず、環境変数（key_env）か別のファイル（key_file）から"""
    parser = configparser.ConfigParser(interpolation=None)
    if not parser.read(path, encoding="utf-8-sig"):
        raise AdapterError(f"設定ファイルを読めません: {path}", 2)
    try:
        api, out, mfa = parser["api"], parser["output"], parser["mfa"] if parser.has_section("mfa") else {}
        key = ""
        if api.get("key_env"):
            key = os.environ.get(api["key_env"], "")
        if not key and api.get("key_file"):
            key = Path(api["key_file"]).read_text(encoding="utf-8").strip()
        if not key:
            raise AdapterError("API キーがありません（[api] key_env の環境変数か key_file）", 2)
        output_dir = Path(out["dir"])
        archive = out.get("archive_dir", "").strip()
        categories = tuple(c.strip() for c in api.get("categories", "adhoc,special").split(",") if c.strip())
        return Settings(
            base_url=api["base_url"].rstrip("/"),
            api_key=key,
            days=api.getint("days", 30),
            categories=categories,
            timeout_seconds=api.getfloat("timeout_seconds", 60),
            output_dir=output_dir,
            mfa_file=out.get("mfa_file", "mfa.txt"),
            csv_file=out.get("csv_file", "access_check.csv"),
            archive_dir=Path(archive) if archive else None,
            archive_keep_days=out.getint("archive_keep_days", 60),
            sound=(mfa.get("sound", "") or "").strip() or None,
            message=(mfa.get("message", "") or "").strip() or DEFAULT_MESSAGE,
            message_no_deadline=(mfa.get("message_no_deadline", "") or "").strip() or DEFAULT_MESSAGE_NO_DEADLINE,
            cutover=_load_cutover(parser, path.resolve().parent),
        )
    except AdapterError:
        raise
    except (KeyError, ValueError, OSError) as error:
        raise AdapterError(f"設定ファイルの誤り: {error}", 2) from error


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
def fetch_feed(s: Settings) -> dict:
    query = urllib.parse.urlencode([("days", s.days), *[("categories", c) for c in s.categories]])
    url = f"{s.base_url}/ops/schedule-feed?{query}"
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {s.api_key}", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=s.timeout_seconds) as response:
            feed = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        with error:  # 応答の本文を読んだら閉じる（閉じないと接続と一時ファイルが残る）
            body = error.read()[:300]
        raise AdapterError(f"API が {error.code} を返しました（{url}）: {body!r}", 3) from error
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise AdapterError(f"API に接続できません（{url}）: {error}", 3) from error
    if feed.get("truncated"):
        raise AdapterError("時刻表が上限で切れています。days を小さくしてください（何も書きませんでした）", 5)
    return feed


# ---------------------------------------------------------------------------
# 文面とファイルの形
# ---------------------------------------------------------------------------
def _local(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(JST)


def deadline_text(start: datetime, deadline: datetime) -> str:
    """締切の言い方: ちょうどの時なら「11時」、そうでなければ「11:30」。開始の翌日なら「翌日」、それより後なら「11/18 」を前に付ける"""
    clock = f"{deadline.hour}時" if deadline.minute == 0 else f"{deadline.hour}:{deadline.minute:02d}"
    days = (deadline.date() - start.date()).days
    if days == 0:
        return clock
    if days == 1:
        return f"翌日{clock}"
    return f"{deadline.month}/{deadline.day} {clock}"


def _one_line(text: str) -> str:
    return " ".join(str(text).split())  # 改行・タブを含めない（mfa は 1 行 1 命令）


def message_of(item: dict, s: Settings) -> str:
    start = _local(item["scheduled_start"])
    values = {"client": _one_line(item["client_code"]), "task": _one_line(item["title"]),
              "client_name": _one_line(item.get("client_name", "")), "biz_code": _one_line(item.get("biz_code", "")),
              "biz_name": _one_line(item.get("biz_name", ""))}
    if item.get("deadline_at"):
        return s.message.format(**values, deadline=deadline_text(start, _local(item["deadline_at"])))
    return s.message_no_deadline.format(**values)


def mfa_line(at: datetime, command: str, argument: str) -> str:
    """mfa の 1 行: 「YY/MM/DD HH:MM 命令（7 桁）引数」"""
    return f"{at:%y/%m/%d %H:%M} {command.ljust(COMMAND_WIDTH)}{argument}"


def render_mfa(items: list[dict], s: Settings) -> str:
    lines: list[str] = []
    for item in items:
        at = _local(item["scheduled_start"])
        if s.sound:
            lines.append(mfa_line(at, "Mplay", f"Repeat {s.sound}"))
        lines.append(mfa_line(at, "Msg", message_of(item, s)))
    return "".join(f"{line}\r\n" for line in lines)


def render_csv(items: list[dict]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    for item in items:
        start = _local(item["scheduled_start"])
        writer.writerow([
            f"{start:%Y-%m-%d}", item["client_code"], item.get("biz_code", ""), _one_line(item["title"]),
            f"{start:%H:%M}", STATUS_WORDS.get(item.get("status", ""), item.get("status", "")),
        ])
    return buffer.getvalue()


def to_cp932(text: str, name: str) -> bytes:
    """CP932 に。書けない文字は似た文字に直し、それでも書けなければ「?」にして警告する（ファイルは必ず書けるように）"""
    fixed = text.translate(CP932_FIXES)
    try:
        return fixed.encode("cp932")
    except UnicodeEncodeError:
        bad = sorted({ch for ch in fixed if not _encodable(ch)})
        log.warning("%s: CP932 で書けない文字を ? にしました: %s", name, " ".join(f"{ch}(U+{ord(ch):04X})" for ch in bad))
        return fixed.encode("cp932", errors="replace")


def _encodable(ch: str) -> bool:
    try:
        ch.encode("cp932")
        return True
    except UnicodeEncodeError:
        return False


# ---------------------------------------------------------------------------
# 切り替えの組み合わせ: Excel の txt と、新しいシステムの知らせを組み合わせる
# ---------------------------------------------------------------------------
@dataclass
class _Block:
    """ひとまとまりの行: 知らせ（Msg）とその直前の同じ分の音（Mplay）、または、ほかの 1 行"""
    lines: list[parity_report.Line]
    order: int
    msg: parity_report.Line | None = None

    @property
    def at(self) -> datetime | None:
        return self.lines[-1].at

    @property
    def sounds(self) -> list[parity_report.Line]:
        return [line for line in self.lines if line is not self.msg]


@dataclass
class CombineCounts:
    replaced: int = 0       # Excel の知らせを、新しいシステムの知らせに置き換えた
    excel_only: int = 0     # 新しいシステムが出さないので、Excel の知らせのまま残した
    new_only: int = 0       # 新しいシステムだけが出す知らせ（既定の音）
    passed: int = 0         # そのまま写した、知らせ以外の行（Shell・知らせの無い音・ほかの命令）
    past: int = 0           # いまより前なので書かなかった Excel の行
    timeless: int = 0       # 日時を読めないので、そのまま写した Excel の行
    by_command: dict[str, int] = field(default_factory=dict)  # 写した知らせ以外の行の、命令ごとの数


def _blocks(lines: list[parity_report.Line]) -> list[_Block]:
    """行をまとまりに: Msg の直前に続く同じ分の Mplay は、その Msg の音（ほかの Mplay は 1 行だけのまとまり）"""
    blocks: list[_Block] = []
    pending: list[parity_report.Line] = []

    def flush() -> None:
        for sound in pending:
            blocks.append(_Block([sound], len(blocks)))
        pending.clear()

    for line in lines:
        if line.command == "Mplay" and line.at is not None:
            if pending and pending[-1].at != line.at:
                flush()
            pending.append(line)
        elif line.command == "Msg" and line.at is not None:
            if pending and pending[-1].at != line.at:
                flush()
            blocks.append(_Block([*pending, line], len(blocks), msg=line))
            pending.clear()
        else:
            flush()
            blocks.append(_Block([line], len(blocks)))
    flush()
    return blocks


def combine(excel_text: str, new_text: str, *, now: datetime, tolerance: int,
            aliases: dict[tuple[str, str], tuple[str, str]]) -> tuple[str, CombineCounts]:
    """
    mfa が読む本物のファイルの中身（CRLF）。Excel の txt を元に、新しいシステムが出す知らせ（同じ作業・tolerance 分以内）を
    新しいシステムの行に置き換える。その知らせの音（Excel の Mplay）は新しい知らせの時刻へ移す。いまより前の行は書かない
    """
    counts = CombineCounts()
    since = now.replace(second=0, microsecond=0)
    excel = _from_now(_blocks(parity_report.parse(excel_text, "旧")), since, counts)
    new = [b for b in _blocks(parity_report.parse(new_text, "新")) if b.msg is not None and b.at is not None and b.at >= since]
    for block in excel:
        if block.msg is not None:
            block.msg.client, block.msg.task = aliases.get(block.msg.key, block.msg.key)
    excel_by_msg = {id(b.msg): b for b in excel if b.msg is not None}
    new_msgs = [b.msg for b in new if b.msg is not None]
    pairs = parity_report._pair([b.msg for b in excel if b.msg is not None], new_msgs, tolerance)  # 照合と同じ結び方
    partner = {id(new_line): excel_by_msg[id(old)] for old, new_line in pairs}
    replaced = {id(b.msg) for b in partner.values()}
    out = _excel_lines(excel, replaced, counts) + _new_lines(new, partner, counts)
    out.sort(key=lambda item: item[0])
    return "".join(f"{line}\r\n" for _key, lines in out for line in lines), counts


_SortedLines = tuple[tuple, list[str]]  # (並べる鍵, 行)


def _from_now(blocks: list[_Block], since: datetime, counts: CombineCounts) -> list[_Block]:
    """いまより前のまとまりを除く（数える）。日時を読めない行は判断できないので残す"""
    kept: list[_Block] = []
    for block in blocks:
        if block.at is None:
            counts.timeless += 1
            kept.append(block)
        elif block.at < since:
            counts.past += len(block.lines)
        else:
            kept.append(block)
    return kept


def _excel_lines(excel: list[_Block], replaced: set[int], counts: CombineCounts) -> list[_SortedLines]:
    """Excel の行のうち、置き換えない知らせ（音つき）と、知らせ以外の行を、そのまま"""
    out: list[_SortedLines] = []
    for block in excel:
        if block.msg is not None and id(block.msg) in replaced:
            continue
        if block.msg is not None:
            counts.excel_only += 1
        elif block.at is not None:
            counts.passed += 1
            command = block.lines[0].command
            counts.by_command[command] = counts.by_command.get(command, 0) + 1
        key = (0, block.order) if block.at is None else (1, block.at, 0, block.order)  # 日時の無い行は先頭
        out.append((key, [line.raw for line in block.lines]))
    return out


def _new_lines(new: list[_Block], partner: dict[int, _Block], counts: CombineCounts) -> list[_SortedLines]:
    """新しいシステムの知らせ。置き換えた知らせには Excel の音を新しい時刻で付け、新しいシステムだけの知らせは既定の音のまま"""
    out: list[_SortedLines] = []
    for block in new:
        assert block.msg is not None and block.msg.at is not None
        old = partner.get(id(block.msg))
        if old is None:
            counts.new_only += 1
            lines = [line.raw for line in block.lines]
        else:
            counts.replaced += 1  # Excel のその知らせの音（緊急度・鳴らす機器）を、新しい時刻へ移す
            lines = [mfa_line(block.msg.at, "Mplay", sound.argument) for sound in old.sounds] + [block.msg.raw]
        out.append(((1, block.msg.at, 1, block.order), lines))
    return out


def read_excel_file(path: Path) -> str:
    """運用の担当が置いた Excel の txt（CP932）。読めなければ何も書かない（スクリプトの行を落とさないため）"""
    try:
        return parity_report.read_text(path)
    except parity_report.ParityError as error:
        raise AdapterError(f"Excel の txt を読めません（何も書きませんでした）: {error}", 6) from error


def load_aliases(cut: Cutover) -> dict[tuple[str, str], tuple[str, str]]:
    if cut.aliases_file is None or not cut.aliases_file.exists():
        return {}  # 照合と同じ: 別名の表がまだ無ければ、別名なし
    try:
        return parity_report.load_aliases(cut.aliases_file)
    except parity_report.ParityError as error:
        raise AdapterError(f"別名の表の誤り: {error}", 2) from error


# ---------------------------------------------------------------------------
# 切り替えの読み直し: mfa に読み直させる（MFA.exe /R）
# ---------------------------------------------------------------------------
RELOAD_TIMEOUT_SECONDS = 60


def quiet_minute(times: list[datetime], *, start: datetime, quiet: int, max_wait: int) -> datetime | None:
    """start から max_wait 分のうち、前後 quiet 分に行が無い最初の分（無ければ None）"""
    first = start.replace(second=0, microsecond=0)
    for step in range(max_wait + 1):
        candidate = first + timedelta(minutes=step)
        if all(abs((t - candidate).total_seconds()) > quiet * 60 for t in times):
            return candidate
    return None


def _launch(args: list[str]) -> int | None:
    """MFA.exe /R を動かす。終了コードか、RELOAD_TIMEOUT_SECONDS のうちに終わらなければ None（止めない: mfa そのものかもしれない）"""
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    process = subprocess.Popen(args, creationflags=flags)
    try:
        return process.wait(timeout=RELOAD_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return None


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def reload_mfa(cut: Cutover, times: list[datetime], *, now: datetime) -> None:
    """静かな分を待って mfa に読み直させる。読み直させられなければ終了コード 7（ファイルは書いたまま。mfa は前のファイル）"""
    if cut.mfa_exe is None:
        log.info("mfa_exe が空なので、読み直させていません（手で %s を動かす）", " ".join(("MFA.exe", *cut.reload_args)))
        return
    at = quiet_minute(times, start=now, quiet=cut.quiet_minutes, max_wait=cut.max_wait_minutes)
    if at is None:
        raise AdapterError(f"{cut.max_wait_minutes} 分のうちに、前後 {cut.quiet_minutes} 分に行の無い分がありません。"
                           "mfa は前のファイルのままです（手で読み直させる）", 7)
    wait = (at - now).total_seconds()
    if wait > 0:
        log.info("%s まで待ってから読み直させます（前後 %d 分に行が無い分）", f"{at:%H:%M}", cut.quiet_minutes)
        _sleep(wait)
    args = [cut.mfa_exe, *cut.reload_args]
    try:
        code = _launch(args)
    except OSError as error:
        raise AdapterError(f"mfa を動かせません（{' '.join(args)}）: {error}。mfa は前のファイルのままです", 7) from error
    if code is None:
        log.warning("%s が %d 秒たっても終わりません（そのままにしています）。J-1 で確かめた動きか見てください",
                    " ".join(args), RELOAD_TIMEOUT_SECONDS)
    elif code != 0:
        raise AdapterError(f"{' '.join(args)} が終了コード {code} を返しました。mfa は前のファイルのままかもしれません", 7)
    else:
        log.info("mfa に読み直させました: %s", " ".join(args))


# ---------------------------------------------------------------------------
# 書き込み
# ---------------------------------------------------------------------------
def write_atomically(path: Path, data: bytes) -> None:
    """同じフォルダーの一時ファイルに書いてから置き換える（読む側が書きかけのファイルを見ない）"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def archive(files: dict[str, bytes], s: Settings, now: datetime) -> None:
    """書いたファイルの写しを日時つきで残し、古い写しを消す"""
    if s.archive_dir is None:
        return
    stamp = f"{now:%Y%m%d_%H%M%S}"
    for name, data in files.items():
        stem, dot, ext = name.rpartition(".")
        write_atomically(s.archive_dir / (f"{stem}_{stamp}.{ext}" if dot else f"{name}_{stamp}"), data)
    if s.archive_keep_days > 0:
        limit = now.timestamp() - s.archive_keep_days * 86400
        for old in s.archive_dir.iterdir():
            if old.is_file() and old.stat().st_mtime < limit:
                old.unlink()


def run(s: Settings, *, dry_run: bool = False, now: datetime | None = None) -> int:
    now = now or datetime.now(JST)
    feed = fetch_feed(s)
    items = sorted(feed.get("items", []), key=lambda i: (i["scheduled_start"], i.get("key", "")))
    files = {s.mfa_file: to_cp932(render_mfa(items, s), s.mfa_file), s.csv_file: to_cp932(render_csv(items), s.csv_file)}
    first = _local(items[0]["scheduled_start"]).date() if items else None
    last = _local(items[-1]["scheduled_start"]).date() if items else None
    log.info("時刻表: %d 件（%s 〜 %s、分類 %s、%s 日分）", len(items), first or "-", last or "-", ",".join(feed.get("categories", [])), s.days)
    if not items:
        log.warning("知らせる作業がありません（テンプレートの分類・「開始時刻に知らせる」を確かめてください）")
    live = _combined(s, files[s.mfa_file].decode("cp932"), now) if s.cutover else None
    if dry_run:
        sys.stdout.write((live[0] if live else files[s.mfa_file]).decode("cp932"))
        sys.stdout.write(files[s.csv_file].decode("cp932"))
        if live and s.cutover and s.cutover.mfa_exe:
            at = quiet_minute(live[1], start=now, quiet=s.cutover.quiet_minutes, max_wait=s.cutover.max_wait_minutes)
            log.info("dry-run: 読み直させる時刻は %s", f"{at:%H:%M}" if at else "（静かな分が無い）")
        log.info("dry-run: ファイルは書いていません")
        return 0
    try:
        for name, data in files.items():
            write_atomically(s.output_dir / name, data)
        archive({**files, **({LIVE_PREFIX + s.mfa_file: live[0]} if live else {})}, s, now)
        if live and s.cutover:
            write_atomically(s.cutover.live_file, live[0])
    except OSError as error:
        raise AdapterError(f"ファイルを書けません: {error}", 4) from error
    log.info("書きました: %s", ", ".join(str(s.output_dir / name) for name in files))
    if live and s.cutover:
        log.info("mfa のファイルを書きました: %s", s.cutover.live_file)
        reload_mfa(s.cutover, live[1], now=now)
    return 0


LIVE_PREFIX = "live_"  # 組み合わせた本物のファイルの写し（live_mfa_YYYYMMDD_HHMMSS.txt。照合の写し mfa_… とは別の名前）


def _combined(s: Settings, new_text: str, now: datetime) -> tuple[bytes, list[datetime]]:
    """組み合わせた mfa のファイル（CP932）と、その中の行の時刻（読み直す分を選ぶため）"""
    assert s.cutover is not None
    cut = s.cutover
    text, counts = combine(read_excel_file(cut.excel_file), new_text, now=now, tolerance=cut.tolerance_minutes,
                           aliases=load_aliases(cut))
    others = ", ".join(f"{command} {n}" for command, n in sorted(counts.by_command.items())) or "なし"
    log.info("組み合わせ: 置き換えた知らせ %d・Excel のまま残した知らせ %d・新しいシステムだけの知らせ %d・"
             "そのまま写した行 %d（%s）・過去なので書かなかった行 %d", counts.replaced, counts.excel_only,
             counts.new_only, counts.passed, others, counts.past)
    if counts.timeless:
        log.warning("日時を読めない Excel の行 %d 行を、そのまま先頭に写しました", counts.timeless)
    if counts.excel_only:
        log.warning("新しいシステムが出さない Excel の知らせ %d 件は、Excel のまま鳴ります（照合の「欠け」を見てテンプレートを直す）",
                    counts.excel_only)
    times = [line.at for line in parity_report.parse(text, "新") if line.at is not None]
    return to_cp932(text, cut.live_file.name), times


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="mfa 連携アダプター（新しいシステム → mfa.txt と Access 照合用 CSV）")
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("mfa_adapter.ini"))
    parser.add_argument("--dry-run", action="store_true", help="書かずに、書く内容を表示する")
    parser.add_argument("--log-file", type=Path, help="ログを追記するファイル（省略時は画面だけ）")
    args = parser.parse_args(argv)
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if args.log_file:
        handlers.append(logging.FileHandler(args.log_file, encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers, force=True)
    try:
        return run(load_settings(args.config), dry_run=args.dry_run)
    except AdapterError as error:
        log.error("%s", error)
        return error.code


if __name__ == "__main__":
    sys.exit(main())
