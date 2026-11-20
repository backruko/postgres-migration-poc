"""
新旧 mfa.txt の照合（並行稼働）。mfa 連携アダプターと同じく、新しいシステムの外に置く使い捨ての道具。

並行稼働の 14 日間、mfa はいままでの（旧の）mfa.txt を読み、アダプターは新しい mfa.txt を別のフォルダーに書く。
この道具は、同じ時間帯の 2 つのファイルを見比べ、旧のどの行にも新の行があるか（同じ作業・開始の差 3 分以内）を確かめる。

- 見比べるのは Msg の行（「始めて」の知らせ）だけ。同じ分の Mplay（音）は Msg に付くものとして数えるだけ。
  Shell の行（スクリプトの自動実行）は新しいシステムが書かないので「対象外」として一覧に残す（mfa がそのまま動かす）
- 同じ作業 = 【顧客コード】と、「を【」「を実施」より前の作業名が同じ（全角・半角、空白の違いは無視、「※」から後は無視）。
  名前が違うだけの作業は、別名の表（parity_aliases.csv）で旧の名前を新の名前に読み替える
- 1 つの旧の行には 1 つの新の行だけを対応させる（同じ日に同じ作業が 2 回あっても、それぞれに対応が要る）
- 判定: 旧の行に 3 分以内の新の行が無い（欠け）、同じ作業はあるが 3 分を超えてずれている（時刻ずれ）→ NG。
  新の行だけにある（余り）→ 要確認（旧の入力漏れのことがある。NG にはしない）。締切の言い方の違い → 参考
- 見比べる時間帯: 新のファイルを作った時刻から 24 時間（その晩のファイルが受け持つ 1 日）。作った時刻は、
  アーカイブの名前の日時（mfa_20261116_020000.txt）か --window-start で。どちらも無ければファイルの更新時刻
- 標準ライブラリだけで動く。Python 3.10 以上

使い方は 3 つ:

    # 毎晩（タスク スケジューラー、アダプターのすぐ後）: 旧の mfa.txt をアダプターの最新の写しの隣に同じ日時で写し、見比べる
    python parity_report.py --nightly --config mfa_adapter.ini
    # 並行稼働の終わりに: 期間のすべての晩を、いまの別名の表で見比べ直し、日ごとの表と合否をまとめる（承認の記録）
    python parity_report.py --rollup --from 2026-11-17 --to 2026-11-30 --config mfa_adapter.ini
    # 2 つのファイルを指定して 1 回だけ
    python parity_report.py --legacy D:\\mfa\\mfa.txt --new D:\\ops_adapter\\archive\\mfa_20261116_020000.txt --out D:\\ops_adapter\\parity

結果: 1 行ずつの判定の CSV（UTF-8 BOM つき。Excel でそのまま開ける）と、要約のテキスト。
終了コード: 0 = 一致（NG なし）、1 = NG あり（まとめでは、照合の無い日があるときも）、
2 = 入力の誤り（ファイルが無い・読めない・別名の表や設定の誤り・アダプターの昨夜の写しが無い）
"""
from __future__ import annotations

import argparse
import configparser
import csv
import io
import os
import re
import sys
import tempfile
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9), "JST")  # 固定の +9 時間（アダプターと同じ理由で zoneinfo を使わない）

DEFAULT_TOLERANCE_MINUTES = 3
DEFAULT_HOURS = 24

# 「YY/MM/DD HH:MM 命令 引数」。命令の後ろの空白の数はファイルによって違ってよい
LINE = re.compile(r"^\s*(\d{2})/(\d{1,2})/(\d{1,2})\s+(\d{1,2}):(\d{2})\s+(\S+)\s*(.*?)\s*$")
CLIENT = re.compile(r"^【([^】]+)】")
DEADLINE = re.compile(r"を【(.+?)まで】")
STAMP = re.compile(r"_(\d{8})_(\d{6})(?:\.[^.]*)?$")  # アダプターのアーカイブの名前: mfa_20261116_020000.txt

# 種類（CSV の「種類」列）と判定
MATCHED, SHIFTED, MISSING, EXTRA = "一致", "時刻ずれ", "欠け（旧だけ）", "余り（新だけ）"
OUT_OF_SCOPE, SOUND_ONLY, UNREADABLE = "対象外", "要確認（音だけ）", "読めない行"
VERDICT = {MATCHED: "OK", SHIFTED: "NG", MISSING: "NG", EXTRA: "要確認", OUT_OF_SCOPE: "対象外",
           SOUND_ONLY: "要確認", UNREADABLE: "要確認"}
KIND_ORDER = [MISSING, SHIFTED, EXTRA, SOUND_ONLY, UNREADABLE, OUT_OF_SCOPE, MATCHED]


class ParityError(Exception):
    """入力の誤り（終了コード 2）"""


@dataclass
class Line:
    side: str  # "旧" / "新"
    number: int  # ファイルの何行目か
    raw: str
    at: datetime | None = None
    command: str = ""
    argument: str = ""
    client: str = ""
    task: str = ""  # 見比べる形（正規化した作業名。別名の読み替えの後）
    deadline: str = ""

    @property
    def key(self) -> tuple[str, str]:
        return (self.client, self.task)


@dataclass
class Row:
    kind: str
    legacy: Line | None = None
    new: Line | None = None
    note: str = ""

    @property
    def minutes(self) -> int | None:
        if self.legacy and self.new and self.legacy.at and self.new.at:
            return round((self.new.at - self.legacy.at).total_seconds() / 60)
        return None


@dataclass
class Report:
    legacy_path: Path
    new_path: Path
    start: datetime
    end: datetime
    tolerance: int
    rows: list[Row] = field(default_factory=list)
    counts_in: dict[str, dict[str, int]] = field(default_factory=dict)  # 旧 / 新 の 命令ごとの行数（時間帯の中）
    outside: dict[str, int] = field(default_factory=dict)  # 時間帯の外の行数

    def count(self, kind: str) -> int:
        return sum(1 for r in self.rows if r.kind == kind)

    @property
    def passed(self) -> bool:
        return not any(VERDICT[r.kind] == "NG" for r in self.rows)


# ---------------------------------------------------------------------------
# 読む
# ---------------------------------------------------------------------------
def read_text(path: Path) -> str:
    """mfa.txt は CP932。念のため UTF-8 も受ける（どちらでも読めなければ入力の誤り）"""
    try:
        data = path.read_bytes()
    except OSError as error:
        raise ParityError(f"ファイルを読めません: {path}（{error}）") from error
    for encoding in ("cp932", "utf-8-sig"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ParityError(f"文字コードが CP932 でも UTF-8 でもありません: {path}")


def normalize(text: str) -> str:
    """見比べる形: 全角・半角をそろえ（NFKC）、空白をすべて除く"""
    return "".join(unicodedata.normalize("NFKC", text).split())


def task_of(message: str) -> tuple[str, str, str]:
    """Msg の文面から（顧客コード, 作業名, 締切の言い方）。文面の形が違っても、作業名は文面全体で見比べられるようにする"""
    text = unicodedata.normalize("NFKC", message).split("※", 1)[0].strip()
    client = ""
    found = CLIENT.match(text)
    if found:
        client, text = normalize(found.group(1)), text[found.end():]
    deadline = DEADLINE.search(text)
    for marker in ("を【", "を実施"):
        if marker in text:
            text = text.split(marker, 1)[0]
            break
    return client, normalize(text), normalize(deadline.group(1)) if deadline else ""


def parse(text: str, side: str) -> list[Line]:
    lines: list[Line] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        line = Line(side=side, number=number, raw=raw.rstrip())
        found = LINE.match(raw)
        if found:
            yy, mm, dd, hh, mi, command, argument = found.groups()
            try:
                line.at = datetime(2000 + int(yy), int(mm), int(dd), int(hh), int(mi), tzinfo=JST)
            except ValueError:
                line.at = None  # 13 月・25 時など
            line.command, line.argument = command.capitalize(), argument
            if line.command == "Msg":
                line.client, line.task, line.deadline = task_of(argument)
        lines.append(line)
    return lines


def load_aliases(path: Path | None) -> dict[tuple[str, str], tuple[str, str]]:
    """別名の表: 見出しの行のあとに「旧の顧客,旧の作業,新の顧客,新の作業」。新の顧客が空なら旧と同じ。
    運用の人が Excel で直すので、UTF-8（BOM つき）でも CP932 でも読む"""
    if path is None:
        return {}
    text = read_text(path)
    aliases: dict[tuple[str, str], tuple[str, str]] = {}
    rows = list(csv.reader(io.StringIO(text)))
    for number, row in enumerate(rows[1:], start=2):
        if not any(cell.strip() for cell in row):
            continue
        if len(row) < 4 or not row[1].strip() or not row[3].strip():
            raise ParityError(f"別名の表の {number} 行目: 「旧の顧客,旧の作業,新の顧客,新の作業」の 4 列が要ります（{path}）")
        old_client, new_client = normalize(row[0]), normalize(row[2]) or normalize(row[0])
        aliases[(old_client, normalize(row[1]))] = (new_client, normalize(row[3]))
    return aliases


def stamp_of(path: Path) -> datetime | None:
    found = STAMP.search(path.name)
    if not found:
        return None
    try:
        return datetime.strptime(found.group(1) + found.group(2), "%Y%m%d%H%M%S").replace(tzinfo=JST)
    except ValueError:
        return None


def window_start(new_path: Path, given: datetime | None) -> datetime:
    if given is not None:
        return given if given.tzinfo else given.replace(tzinfo=JST)
    stamp = stamp_of(new_path)
    if stamp is not None:
        return stamp
    return datetime.fromtimestamp(new_path.stat().st_mtime, JST)


# ---------------------------------------------------------------------------
# 見比べる
# ---------------------------------------------------------------------------
def _pair(legacy: list[Line], new: list[Line], limit: int | None) -> list[tuple[Line, Line]]:
    """同じ作業の旧と新を、開始の差の小さい順に 1 対 1 で結ぶ（limit 分を超える組は結ばない。None なら同じ日なら結ぶ）"""
    by_key: dict[tuple[str, str], list[Line]] = {}
    for line in new:
        by_key.setdefault(line.key, []).append(line)
    candidates: list[tuple[float, int, int, Line, Line]] = []
    for old in legacy:
        for line in by_key.get(old.key, []):
            assert old.at and line.at
            gap = abs((line.at - old.at).total_seconds()) / 60
            if (limit is not None and gap <= limit) or (limit is None and line.at.date() == old.at.date()):
                candidates.append((gap, old.number, line.number, old, line))
    candidates.sort(key=lambda c: c[:3])
    used_old: set[int] = set()
    used_new: set[int] = set()
    pairs: list[tuple[Line, Line]] = []
    for _gap, _o, _n, old, line in candidates:
        if id(old) in used_old or id(line) in used_new:
            continue
        used_old.add(id(old))
        used_new.add(id(line))
        pairs.append((old, line))
    return pairs


def compare(legacy_text: str, new_text: str, *, start: datetime, hours: int = DEFAULT_HOURS,
            tolerance: int = DEFAULT_TOLERANCE_MINUTES, aliases: dict | None = None,
            legacy_path: Path = Path("legacy"), new_path: Path = Path("new")) -> Report:
    """
    旧と新の mfa.txt を、start から hours 時間の間について見比べる。旧を先に読む（同じ並びの位置の行は旧が先に並ぶ）。
    別名の表は旧の名前を新の名前に読み替える（新の行は読み替えない）
    """
    end = start + timedelta(hours=hours)
    report = Report(legacy_path=legacy_path, new_path=new_path, start=start, end=end, tolerance=tolerance)
    legacy = _messages_in_window(report, legacy_text, "旧", aliases)
    new = _messages_in_window(report, new_text, "新", None)
    _match_messages(report, legacy, new)
    report.rows.sort(key=_row_order)
    return report


def _messages_in_window(report: Report, text: str, side: str, aliases: dict | None) -> list[Line]:
    """
    片方のファイルを読み、時間帯の中の Msg の行を返す（aliases があれば作業を読み替えて）。ほかの行はここで一覧に入れる:
    読めない行・同じ分に Msg の無い音だけの行・Mplay 以外の命令（対象外）。時間帯の中の命令ごとの行数と、外の行数も数える
    """
    messages: list[Line] = []
    counts: dict[str, int] = {}
    outside = 0
    lines = parse(text, side)
    msg_minutes = {line.at for line in lines if line.command == "Msg" and line.at}
    for line in lines:
        if line.at is None:
            report.rows.append(_one_side_row(UNREADABLE, line, "「YY/MM/DD HH:MM 命令 引数」の形ではありません"))
            continue
        if not (report.start <= line.at < report.end):
            outside += 1
            continue
        counts[line.command] = counts.get(line.command, 0) + 1
        if line.command != "Msg":
            row = _other_line_row(line, msg_minutes)
            if row is not None:
                report.rows.append(row)
            continue
        if aliases:
            line.client, line.task = aliases.get(line.key, line.key)
        messages.append(line)
    report.counts_in[side] = counts
    report.outside[side] = outside
    return messages


def _other_line_row(line: Line, msg_minutes: set[datetime]) -> Row | None:
    """Msg 以外の行の一覧の行。Mplay は同じ分に Msg があればその音なので出さない（音だけなら要確認）。ほかの命令は対象外"""
    if line.command == "Mplay":
        return None if line.at in msg_minutes else _one_side_row(SOUND_ONLY, line, "同じ分に Msg の行が無い音だけの行")
    return _one_side_row(OUT_OF_SCOPE, line, f"{line.command} の行は新しいシステムが書かない（mfa がそのまま動かす）")


def _one_side_row(kind: str, line: Line, note: str) -> Row:
    """片方のファイルだけの行（旧の行なら legacy、新の行なら new に入れる）"""
    return Row(kind, legacy=line, note=note) if line.side == "旧" else Row(kind, new=line, note=note)


def _match_messages(report: Report, legacy: list[Line], new: list[Line]) -> None:
    """
    Msg の行を結ぶ: 許容（分）以内は一致、同じ日でそれより離れていれば時刻ずれ、旧だけは欠け、新だけは余り。
    一致を先に決め、残りから時刻ずれを決める（どちらも 1 対 1）
    """
    tolerance = report.tolerance
    matched = _pair(legacy, new, tolerance)
    for old, line in matched:
        report.rows.append(Row(MATCHED, old, line, _deadline_note(old, line)))
    done_old = {id(o) for o, _ in matched}
    done_new = {id(n) for _, n in matched}
    rest_old = [o for o in legacy if id(o) not in done_old]
    rest_new = [n for n in new if id(n) not in done_new]
    shifted = _pair(rest_old, rest_new, None)
    for old, line in shifted:
        report.rows.append(Row(SHIFTED, old, line, f"同じ作業が {tolerance} 分を超えてずれています"))
    done_old |= {id(o) for o, _ in shifted}
    done_new |= {id(n) for _, n in shifted}
    extras = [n for n in new if id(n) not in done_new]
    for old in legacy:
        if id(old) not in done_old:
            report.rows.append(Row(MISSING, old, None, _near_hint(old, extras, tolerance)))
    for line in extras:
        report.rows.append(Row(EXTRA, None, line))


def _deadline_note(old: Line, new: Line) -> str:
    """一致した行の締切の言い方が違えば、参考として書く"""
    if old.deadline and new.deadline and old.deadline != new.deadline:
        return f"締切の言い方が違う: 旧「{old.deadline}」/ 新「{new.deadline}」（参考）"
    return ""


def _near_hint(old: Line, extras: list[Line], tolerance: int) -> str:
    """欠けの行の候補: 同じ顧客で、許容（分）以内の余りの行（3 つまで）。名前が違うだけなら別名の表に足せばよい"""
    assert old.at
    near = [n for n in extras if n.client == old.client and n.at and abs((n.at - old.at).total_seconds()) <= tolerance * 60]
    if not near:
        return ""
    return "同じ顧客・同じ時刻の新の行: " + " / ".join(f"「{n.argument}」" for n in near[:3]) + "（名前の違いなら別名の表へ）"


def _row_order(row: Row) -> tuple:
    line = row.legacy or row.new
    at = line.at if line and line.at else datetime.max.replace(tzinfo=JST)
    return (at, KIND_ORDER.index(row.kind), line.number if line else 0)


# ---------------------------------------------------------------------------
# 書く
# ---------------------------------------------------------------------------
CSV_HEADER = ["判定", "種類", "旧の時刻", "新の時刻", "ずれ（分）", "顧客", "旧の行（何行目）", "旧の内容", "新の行（何行目）", "新の内容", "備考"]


def _at(line: Line | None) -> str:
    return f"{line.at:%Y-%m-%d %H:%M}" if line and line.at else ""


def render_csv(report: Report) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(CSV_HEADER)
    for row in report.rows:
        minutes = row.minutes
        line = row.legacy or row.new
        writer.writerow([
            VERDICT[row.kind], row.kind, _at(row.legacy), _at(row.new), "" if minutes is None else f"{minutes:+d}",
            line.client if line else "", row.legacy.number if row.legacy else "", row.legacy.raw if row.legacy else "",
            row.new.number if row.new else "", row.new.raw if row.new else "", row.note,
        ])
    return buffer.getvalue()


SUMMARY_KINDS = [MATCHED, SHIFTED, MISSING, EXTRA, SOUND_ONLY, UNREADABLE, OUT_OF_SCOPE]


def _padded(text: str, width: int = 18) -> str:
    """全角を 2 桁として数え、右を空白で width 桁にそろえる（メモ帳で列がそろう）"""
    used = sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)
    return text + " " * max(1, width - used)


def render_summary(report: Report) -> str:
    legacy_counts, new_counts = report.counts_in.get("旧", {}), report.counts_in.get("新", {})

    def counts(c: dict[str, int]) -> str:
        return "、".join(f"{k} {v} 行" for k, v in sorted(c.items())) or "0 行"

    deadline_notes = sum(1 for r in report.rows if r.kind == MATCHED and r.note)
    lines = [
        f"新旧 mfa.txt の照合（{report.start:%Y-%m-%d %H:%M} ～ {report.end:%Y-%m-%d %H:%M}、開始の差 ±{report.tolerance} 分まで一致）",
        f"旧: {report.legacy_path}（{counts(legacy_counts)}。時間帯の外 {report.outside.get('旧', 0)} 行）",
        f"新: {report.new_path}（{counts(new_counts)}。時間帯の外 {report.outside.get('新', 0)} 行）",
        "",
    ]
    for kind in SUMMARY_KINDS:
        lines.append(f"  {_padded(kind)}{report.count(kind):>5}  {VERDICT[kind]}")
    if deadline_notes:
        lines.append(f"  （一致のうち、締切の言い方が違う {deadline_notes} 行。参考）")
    lines.append("")
    if report.passed:
        lines.append(f"判定: OK（旧の Msg の行すべてに、同じ作業で開始の差が {report.tolerance} 分以内の新の行があります）")
    else:
        bad = report.count(MISSING) + report.count(SHIFTED)
        lines.append(f"判定: NG（旧の Msg の行のうち {bad} 行に、{report.tolerance} 分以内の新の行がありません）")
    if report.count(EXTRA):
        lines.append("余り（新だけ）は、旧の入力漏れか、新のテンプレートの設定（分類・「開始時刻に知らせる」）の違いです。1 行ずつ確かめてください")
    return "\r\n".join(lines) + "\r\n"


def write_report(report: Report, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"parity_{report.start:%Y%m%d_%H%M}"
    csv_path, summary_path = out_dir / f"{stem}.csv", out_dir / f"{stem}_summary.txt"
    csv_path.write_bytes(render_csv(report).encode("utf-8-sig"))
    summary_path.write_bytes(render_summary(report).encode("utf-8-sig"))
    return csv_path, summary_path


# ---------------------------------------------------------------------------
# 毎晩の写しと、期間のまとめ
# ---------------------------------------------------------------------------
LEGACY_PREFIX = "legacy_"  # 旧の写しの名前: legacy_mfa_20261117_020000.txt（隣の新の写し mfa_20261117_020000.txt と同じ日時）
DEFAULT_MAX_AGE_HOURS = 26  # アダプターの写しがこれより古ければ、昨夜は動かなかった


@dataclass(frozen=True)
class Config:
    """mfa_adapter.ini の [output] archive_dir・mfa_file と [parity] を読む（API キーは要らない）"""
    archive_dir: Path
    mfa_file: str
    legacy_file: Path | None
    out_dir: Path
    aliases: Path | None
    tolerance: int
    hours: int
    max_age_hours: int


def load_config(path: Path) -> Config:
    parser = configparser.ConfigParser(interpolation=None)
    if not parser.read(path, encoding="utf-8-sig"):
        raise ParityError(f"設定ファイルを読めません: {path}")
    try:
        out = parser["output"]
        par = parser["parity"] if parser.has_section("parity") else {}
        archive = out.get("archive_dir", "").strip()
        if not archive:
            raise ParityError("[output] archive_dir がありません（照合はアダプターの写しを使います）")
        legacy = (par.get("legacy_file", "") or "").strip()
        aliases = (par.get("aliases", "") or "").strip()
        base = path.resolve().parent  # 相対のパスは設定ファイルの場所から
        return Config(
            archive_dir=base / archive,
            mfa_file=out.get("mfa_file", "mfa.txt"),
            legacy_file=base / legacy if legacy else None,
            out_dir=base / ((par.get("out_dir", "") or "").strip() or "parity"),
            aliases=base / aliases if aliases else None,
            tolerance=int(par.get("tolerance_minutes", DEFAULT_TOLERANCE_MINUTES)),
            hours=int(par.get("hours", DEFAULT_HOURS)),
            max_age_hours=int(par.get("max_age_hours", DEFAULT_MAX_AGE_HOURS)),
        )
    except ParityError:
        raise
    except (KeyError, ValueError) as error:
        raise ParityError(f"設定ファイルの誤り: {error}") from error


def _copy_pattern(name: str) -> re.Pattern[str]:
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    return re.compile(rf"^{re.escape(stem)}_(\d{{8}})_(\d{{6}}){re.escape('.' + ext) if ext else ''}$")


def new_copies(cfg: Config) -> list[tuple[datetime, Path]]:
    """アダプターの写し（mfa_YYYYMMDD_HHMMSS.txt）を日時の順に。旧の写し（legacy_…）は入らない"""
    pattern = _copy_pattern(cfg.mfa_file)
    found: list[tuple[datetime, Path]] = []
    if cfg.archive_dir.is_dir():
        for path in cfg.archive_dir.iterdir():
            if path.is_file() and pattern.match(path.name):
                stamp = stamp_of(path)
                if stamp is not None:
                    found.append((stamp, path))
    return sorted(found)


def legacy_copy_of(new_copy: Path) -> Path:
    return new_copy.with_name(LEGACY_PREFIX + new_copy.name)


def _write_atomically(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def _config_aliases(cfg: Config) -> dict:
    """設定の別名の表。まだ作っていなければ空（最初の別名が要るまで、置き場所だけ決めておける）"""
    return load_aliases(cfg.aliases if cfg.aliases is not None and cfg.aliases.exists() else None)


def _compare_pair(cfg: Config, legacy: Path, new: Path, aliases: dict) -> Report:
    start = stamp_of(new)
    assert start is not None
    return compare(read_text(legacy), read_text(new), start=start, hours=cfg.hours, tolerance=cfg.tolerance,
                   aliases=aliases, legacy_path=legacy, new_path=new)


def nightly(cfg: Config, now: datetime | None = None) -> Report:
    """アダプターの最新の写しの隣に、いまの旧の mfa.txt を同じ日時の名前で写し（すでにあれば写さない）、見比べる"""
    now = now or datetime.now(JST)
    if cfg.legacy_file is None:
        raise ParityError("[parity] legacy_file（mfa がいま読んでいる旧の mfa.txt）がありません")
    copies = new_copies(cfg)
    if not copies:
        raise ParityError(f"アダプターの写しがありません: {cfg.archive_dir}")
    stamp, new = copies[-1]
    if now - stamp > timedelta(hours=cfg.max_age_hours):
        raise ParityError(f"アダプターの最新の写しが古すぎます（{stamp:%Y-%m-%d %H:%M}）。昨夜アダプターが動いたか確かめてください")
    legacy = legacy_copy_of(new)
    if not legacy.exists():  # 昼間にもう一度動かしても、その晩の旧のファイルは置き換えない
        try:
            data = cfg.legacy_file.read_bytes()
        except OSError as error:
            raise ParityError(f"旧の mfa.txt を読めません: {cfg.legacy_file}（{error}）") from error
        _write_atomically(legacy, data)
    return _compare_pair(cfg, legacy, new, _config_aliases(cfg))


@dataclass
class Day:
    day: date
    report: Report | None  # None = その日の照合の材料（旧と新の写しの組）が無い

    @property
    def passed(self) -> bool:
        return self.report is not None and self.report.passed


def rollup(cfg: Config, first: date, last: date) -> list[Day]:
    """期間の毎日を、いまの別名の表で見比べ直す（日ごとの結果も書き直す）。その日の最初の組（夜の定時の実行）を使う"""
    if last < first:
        raise ParityError(f"期間の終わり（{last}）が始め（{first}）より前です")
    aliases = _config_aliases(cfg)
    pairs: dict[date, tuple[Path, Path]] = {}
    for stamp, new in new_copies(cfg):
        legacy = legacy_copy_of(new)
        if first <= stamp.date() <= last and legacy.exists() and stamp.date() not in pairs:
            pairs[stamp.date()] = (legacy, new)
    days: list[Day] = []
    current = first
    while current <= last:
        pair = pairs.get(current)
        report = _compare_pair(cfg, *pair, aliases) if pair else None
        if report is not None:
            write_report(report, cfg.out_dir)
        days.append(Day(current, report))
        current += timedelta(days=1)
    return days


ROLLUP_HEADER = ["日付", "判定", "見比べた時間帯", *SUMMARY_KINDS, "旧の写し", "新の写し"]


def render_rollup_csv(days: list[Day]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(ROLLUP_HEADER)
    for d in days:
        r = d.report
        if r is None:
            writer.writerow([f"{d.day}", "NG", "照合なし（旧と新の写しの組がありません）", *[""] * len(SUMMARY_KINDS), "", ""])
        else:
            writer.writerow([f"{d.day}", "OK" if r.passed else "NG", f"{r.start:%m/%d %H:%M} ～ {r.end:%m/%d %H:%M}",
                             *[r.count(k) for k in SUMMARY_KINDS], r.legacy_path.name, r.new_path.name])
    return buffer.getvalue()


def render_rollup_details(days: list[Day]) -> str:
    """一致以外の行を、日付をつけて 1 つの表に（NG と要確認を 1 か所で確かめる）"""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(["日付", *CSV_HEADER])
    for d in days:
        if d.report is None:
            continue
        rows = list(csv.reader(io.StringIO(render_csv(d.report))))[1:]
        for row in rows:
            if row[1] != MATCHED:
                writer.writerow([f"{d.day}", *row])
    return buffer.getvalue()


def render_rollup_summary(days: list[Day], tolerance: int) -> str:
    reports = [d.report for d in days if d.report is not None]
    no_pair = [d for d in days if d.report is None]
    failed = [d for d in days if d.report is not None and not d.report.passed]
    lines = [
        f"並行稼働の照合のまとめ（{days[0].day} ～ {days[-1].day}、{len(days)} 日。開始の差 ±{tolerance} 分まで一致）",
        f"照合した日: {len(reports)} 日、照合の無い日: {len(no_pair)} 日、NG の日: {len(failed)} 日",
        "",
    ]
    for kind in SUMMARY_KINDS:
        lines.append(f"  {_padded(kind)}{sum(r.count(kind) for r in reports):>6}  {VERDICT[kind]}")
    lines.append("")
    for d in no_pair:
        lines.append(f"  {d.day}  照合なし（その晩の旧と新の写しの組がありません）")
    for d in failed:
        assert d.report is not None
        lines.append(f"  {d.day}  NG（欠け {d.report.count(MISSING)}、時刻ずれ {d.report.count(SHIFTED)}）")
    if no_pair or failed:
        lines.append("")
    if not no_pair and not failed:
        lines.append(f"判定: OK（{len(days)} 日すべてで、旧の Msg の行すべてに {tolerance} 分以内の新の行があります）")
    else:
        lines.append("判定: NG（上の日を確かめてください。別名の表を直したら、もう一度まとめると見比べ直します）")
    extras = sum(r.count(EXTRA) for r in reports)
    if extras:
        lines.append(f"余り（新だけ）{extras} 行は NG ではありません。旧の入力漏れか、テンプレートの設定の違いかを確かめてください")
    return "\r\n".join(lines) + "\r\n"


def write_rollup(days: list[Day], cfg: Config) -> tuple[Path, Path, Path]:
    cfg.out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"parity_rollup_{days[0].day:%Y%m%d}_{days[-1].day:%Y%m%d}"
    paths = (cfg.out_dir / f"{stem}.csv", cfg.out_dir / f"{stem}_details.csv", cfg.out_dir / f"{stem}_summary.txt")
    texts = (render_rollup_csv(days), render_rollup_details(days), render_rollup_summary(days, cfg.tolerance))
    for path, text in zip(paths, texts, strict=True):
        path.write_bytes(text.encode("utf-8-sig"))
    return paths


# ---------------------------------------------------------------------------
# 実行
# ---------------------------------------------------------------------------
def _datetime(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"日時の形が違います（例: 2026-11-16T02:00）: {value}") from error


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"日付の形が違います（例: 2026-11-17）: {value}") from error


def _print(text: str, log_file: Path | None) -> None:
    text = text.replace("\r\n", "\n")
    try:
        sys.stdout.write(text)
    except UnicodeEncodeError:  # 画面の文字コードで書けない文字があっても、結果のファイルは書けている
        encoding = sys.stdout.encoding or "ascii"
        sys.stdout.write(text.encode(encoding, errors="replace").decode(encoding))
    _log(text, log_file)


def _log(text: str, log_file: Path | None) -> None:
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a", encoding="utf-8") as f:
            f.write(f"--- {datetime.now(JST):%Y-%m-%d %H:%M:%S}\n{text}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="新旧 mfa.txt の照合（並行稼働）")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--nightly", action="store_true", help="毎晩: 旧の mfa.txt をアダプターの最新の写しの隣に写し、見比べる（--config）")
    mode.add_argument("--rollup", action="store_true", help="期間のまとめ: --from ～ --to の毎日を見比べ直す（--config）")
    mode.add_argument("--legacy", type=Path, help="1 回だけ: 旧の mfa.txt（--new と一緒に）")
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("mfa_adapter.ini"))
    parser.add_argument("--from", dest="first", type=_date, help="まとめの最初の日（例: 2026-11-17）")
    parser.add_argument("--to", dest="last", type=_date, help="まとめの最後の日（その日を含む）")
    parser.add_argument("--new", type=Path, help="1 回だけ: 新の mfa.txt（アダプターのアーカイブの写し）")
    parser.add_argument("--out", type=Path, default=Path("."), help="1 回だけ: 結果を書くフォルダー")
    parser.add_argument("--aliases", type=Path, help="1 回だけ: 別名の表（CSV: 旧の顧客,旧の作業,新の顧客,新の作業）")
    parser.add_argument("--window-start", type=_datetime, help="1 回だけ: 見比べる時間帯の始め（省略時は新のファイル名の日時）")
    parser.add_argument("--hours", type=int, default=DEFAULT_HOURS, help="1 回だけ: 見比べる時間帯の長さ（時間）")
    parser.add_argument("--tolerance", type=int, default=DEFAULT_TOLERANCE_MINUTES, help="1 回だけ: 一致とみなす開始の差（分）")
    parser.add_argument("--log-file", type=Path, help="要約を追記するファイル（UTF-8）")
    args = parser.parse_args(argv)
    if args.legacy and not args.new:
        parser.error("--legacy には --new が要ります")
    if args.rollup and not (args.first and args.last):
        parser.error("--rollup には --from と --to が要ります")
    try:
        if args.rollup:
            cfg = load_config(args.config)
            days = rollup(cfg, args.first, args.last)
            paths = write_rollup(days, cfg)
            _print(render_rollup_summary(days, cfg.tolerance) + "".join(f"結果: {p}\n" for p in paths), args.log_file)
            return 0 if all(d.passed for d in days) else 1
        if args.nightly:
            cfg = load_config(args.config)
            report = nightly(cfg)
            out_dir = cfg.out_dir
        else:
            start = window_start(args.new, args.window_start)
            report = compare(read_text(args.legacy), read_text(args.new), start=start, hours=args.hours, tolerance=args.tolerance,
                             aliases=load_aliases(args.aliases), legacy_path=args.legacy, new_path=args.new)
            out_dir = args.out
        csv_path, summary_path = write_report(report, out_dir)
    except (ParityError, OSError) as error:
        sys.stderr.write(f"入力の誤り: {error}\n")
        _log(f"入力の誤り: {error}\n", args.log_file)
        return 2
    _print(render_summary(report) + f"結果: {csv_path}\n要約: {summary_path}\n", args.log_file)
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
