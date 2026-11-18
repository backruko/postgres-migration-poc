"""
並行稼働の事前確認（P.5a、2026-11-16）。読むだけで、何も変えない。並行稼働の初日の前と、期間中いつでも動かす。

    docker compose exec worker python scripts/parallel_run_check.py --qa-address ops-qa-team@corp.com
    docker compose exec worker python scripts/parallel_run_check.py --qa-address ops-qa-team@corp.com \\
        --templates-csv /tmp/templates_review.csv      # 不定期・スペシャルのテンプレートの見直し用の CSV も書く

メールの設定はワーカーが読む環境変数なので、worker のコンテナの中で動かす（app のコンテナには SMTP の設定が無い）。

確かめること（それぞれ OK / NG。2026-11-16 の決定）:
1. メール: SMTP のサーバーが設定されている（--smtp-host・--smtp-port・--smtp-from を渡せば、その値と同じか）
2. 知らせない時間帯: 有効なルールすべてが --quiet（既定 22:00-07:00）で「終わってから知らせる」
3. 宛先: 有効なルールが使う有効な宛先が、すべて --qa-address だけ（ほかのアドレスがあれば NG。並行稼働の間、顧客に届かないように）
4. 読み取り専用の API キー: システム連携（読み取り）の有効な職員の、取り消していないキーが --key-max-age-hours（既定 26）時間以内に
   使われた（ゆうべ、外の仕組みが時刻表を読んだ）
5. ワーカー: いま動いていて、この 24 時間に承認に数える問題（エラー・5 分を超える途切れ・きれいに止まらなかった実行）が無い

--templates-csv: 分類が不定期・スペシャルの予定テンプレートの一覧（「開始時刻に知らせる」の見直し用。Excel で開ける UTF-8 BOM）。
確かめるだけで、直すのは画面（ルール・宛先は「通知の設定」、テンプレートは「予定テンプレート設定」）。誰がいつ直したかが残るように。

終了コード: 0 = すべて OK、1 = NG がある、2 = 入力の誤り・データベースに接続できない
このシステムは、外の古い仕組みの名前を知らない（期待する値はすべて引数で渡す）。
"""
from __future__ import annotations

import argparse
import csv
import io
import sys
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path
from src.core.clock import LOCAL_TIMEZONE, local_date_of, utc_now

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))



@dataclass
class Check:
    name: str
    ok: bool
    summary: str
    details: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Expect:
    qa_address: str
    quiet_start: time = time(22, 0)
    quiet_end: time = time(7, 0)
    key_max_age: timedelta = timedelta(hours=26)
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_from: str | None = None


def parse_quiet(value: str) -> tuple[time, time]:
    try:
        start, end = value.split("-")
        h1, m1 = start.strip().split(":")
        h2, m2 = end.strip().split(":")
        return time(int(h1), int(m1)), time(int(h2), int(m2))
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"知らせない時間帯の形が違います（例: 22:00-07:00）: {value}") from error


def _hm(t: time | None) -> str:
    return t.strftime("%H:%M") if t else "なし"


# ---------------------------------------------------------------------------
# 1. メール
# ---------------------------------------------------------------------------
def check_smtp(smtp, expect: Expect) -> Check:
    """smtp: src.infrastructure.config.settings（smtp_host・smtp_port・smtp_user・smtp_from_address・smtp_starttls を持つもの）"""
    if not smtp.smtp_host:
        return Check("メール（SMTP）", False, "SMTP_HOST が空です（本番ではメールを送らず、配信は失敗になります）",
                     ["worker のコンテナの中で動かしていますか（app のコンテナには SMTP の設定がありません）"])
    starttls = {None: "サーバーが対応していれば使う", True: "必ず使う", False: "使わない"}[smtp.smtp_starttls]
    now = f"{smtp.smtp_host}:{smtp.smtp_port}、ログイン {'あり（' + smtp.smtp_user + '）' if smtp.smtp_user else 'なし'}、" \
          f"送り主 {smtp.smtp_from_address}、STARTTLS {starttls}"
    wrong = []
    if expect.smtp_host and smtp.smtp_host != expect.smtp_host:
        wrong.append(f"サーバーが {expect.smtp_host} ではありません")
    if expect.smtp_port and smtp.smtp_port != expect.smtp_port:
        wrong.append(f"ポートが {expect.smtp_port} ではありません")
    if expect.smtp_from and smtp.smtp_from_address.lower() != expect.smtp_from.lower():
        wrong.append(f"送り主が {expect.smtp_from} ではありません")
    return Check("メール（SMTP）", not wrong, now, wrong)


# ---------------------------------------------------------------------------
# 2・3. ルールと宛先
# ---------------------------------------------------------------------------
def _active_rules(db):
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from src.domain_notification.models import NotificationRule

    return list(db.scalars(
        select(NotificationRule).where(NotificationRule.enabled.is_(True))
        .options(selectinload(NotificationRule.destinations)).order_by(NotificationRule.id)
    ))


def check_quiet_hours(db, expect: Expect) -> Check:
    from src.domain_notification.models import QuietMode

    rules = _active_rules(db)
    name = "知らせない時間帯"
    want = f"{_hm(expect.quiet_start)}〜{_hm(expect.quiet_end)}・終わってから知らせる"
    if not rules:
        return Check(name, False, "有効なルールがありません（遅れ・まもなくのメールが誰にも届きません）")
    mode_label = {QuietMode.HOLD: "終わってから知らせる", QuietMode.DROP: "知らせない", None: "設定なし"}
    bad = [
        f"ルール {r.id}「{r.name}」: {_hm(r.quiet_start)}〜{_hm(r.quiet_end)}・{mode_label[r.quiet_mode]}"
        for r in rules
        if (r.quiet_start, r.quiet_end, r.quiet_mode) != (expect.quiet_start, expect.quiet_end, QuietMode.HOLD)
    ]
    summary = f"有効なルール {len(rules)} 件すべてが {want}" if not bad else f"有効なルール {len(rules)} 件のうち {len(bad)} 件が {want} ではありません"
    return Check(name, not bad, summary, bad)


def check_destinations(db, expect: Expect) -> Check:
    rules = _active_rules(db)
    name = "宛先（QA だけに届くか）"
    qa = expect.qa_address.strip().lower()
    bad: list[str] = []
    no_destination: list[str] = []
    used: set[int] = set()
    for r in rules:
        usable = [d for d in r.destinations if d.enabled and not d.is_deleted]
        if not usable:
            no_destination.append(f"ルール {r.id}「{r.name}」: 使える宛先がありません（だれにも届きません）")
        for d in usable:
            used.add(d.id)
            if d.address.strip().lower() != qa:
                bad.append(f"ルール {r.id}「{r.name}」→ 宛先 {d.id}「{d.name}」: {d.address}")
    details = bad + no_destination
    if bad:
        summary = f"QA 以外の宛先に届くルールがあります（{len(bad)} 件）。並行稼働の間は {expect.qa_address} だけにしてください"
    elif no_destination:
        summary = "使える宛先の無いルールがあります"
    elif not rules:
        summary = "有効なルールがありません"
    else:
        summary = f"有効なルールの宛先（{len(used)} 件）はすべて {expect.qa_address}"
    return Check(name, not details and bool(rules), summary, details)


# ---------------------------------------------------------------------------
# 4. 読み取り専用の API キー
# ---------------------------------------------------------------------------
def check_agent_key(db, expect: Expect, now: datetime) -> Check:
    from sqlalchemy import select

    from src.domain_staff.models import Role, RoleCode, StaffApiKey, StaffRole, StaffStatus, StaffUser

    name = "読み取り専用の API キー"
    rows = db.execute(
        select(StaffApiKey, StaffUser)
        .join(StaffUser, StaffUser.id == StaffApiKey.staff_user_id)
        .join(StaffRole, StaffRole.staff_user_id == StaffUser.id)
        .join(Role, Role.id == StaffRole.role_id)
        .where(Role.code == RoleCode.SYSTEM_AGENT.value, StaffUser.status == StaffStatus.ACTIVE, StaffApiKey.revoked_at.is_(None))
        .order_by(StaffApiKey.last_used_at.desc().nulls_last())
    ).all()
    if not rows:
        return Check(name, False, "システム連携（読み取り）の有効な職員に、取り消していないキーがありません",
                     ["「職員」画面でロール「システム連携（読み取り）」の職員を作り、API キーを発行してください"])
    details = [
        f"{u.login_id}「{k.label}」（{k.key_prefix}…）: 最後に使った日時 "
        + (k.last_used_at.astimezone(LOCAL_TIMEZONE).strftime("%Y-%m-%d %H:%M") if k.last_used_at else "まだ使っていない")
        for k, u in rows
    ]
    recent = [k for k, _ in rows if k.last_used_at and now - k.last_used_at <= expect.key_max_age]
    hours = expect.key_max_age.total_seconds() / 3600
    if recent:
        return Check(name, True, f"{hours:g} 時間以内に使われたキーがあります（ゆうべ、時刻表が読まれた）", details)
    return Check(name, False, f"{hours:g} 時間以内に使われたキーがありません（外の仕組みが時刻表を読んでいない）", details)


# ---------------------------------------------------------------------------
# 5. ワーカー
# ---------------------------------------------------------------------------
def check_worker(db, now: datetime) -> Check:
    from sqlalchemy import func, select

    from src.worker_health.models import WorkerError
    from src.worker_health.summary import current_status, summarize

    name = "ワーカー"
    status = current_status(db, now)
    since = now - timedelta(hours=24)
    period = summarize(db, local_date_of(now - timedelta(days=1)), local_date_of(now), now=now)
    errors = db.scalar(select(func.count(WorkerError.id)).where(WorkerError.occurred_at >= since, WorkerError.occurred_at <= now)) or 0
    gaps = [g for g in period.gaps if g.end > since]
    unclean = [u for d in period.days for u in d.unclean_ends or [] if u.ended_at >= since]
    details = []
    if status.state != "ok":
        last = status.last_beat_at.astimezone(LOCAL_TIMEZONE).strftime("%Y-%m-%d %H:%M") if status.last_beat_at else "なし"
        details.append(f"いま動いていません（状態 {status.state}、最後の心拍 {last}）")
    if errors:
        details.append(f"この 24 時間のエラー: {errors} 件（画面「ワーカーの状態」で中身を見る）")
    for g in gaps:
        details.append(f"5 分を超える途切れ: {g.start.astimezone(LOCAL_TIMEZONE):%m/%d %H:%M} 〜 "
                       + ("今も続いている" if g.ongoing else f"{g.end.astimezone(LOCAL_TIMEZONE):%m/%d %H:%M}") + f"（{g.minutes:.0f} 分）")
    for u in unclean:
        details.append(f"きれいに止まらなかった実行: {u.ended_at.astimezone(LOCAL_TIMEZONE):%m/%d %H:%M}（{u.reason}・{u.host}）")
    if details:
        return Check(name, False, "動いていないか、この 24 時間に承認に数える問題があります", details)
    last = status.last_beat_at.astimezone(LOCAL_TIMEZONE).strftime("%H:%M") if status.last_beat_at else ""
    return Check(name, True, f"動いています（最後の心拍 {last}）。この 24 時間に承認に数える問題はありません")


# ---------------------------------------------------------------------------
# テンプレートの見直しの CSV
# ---------------------------------------------------------------------------
TEMPLATE_CSV_HEADER = [
    "顧客コード", "顧客名", "業務コード", "業務名", "顧客側の出来事の業務", "テンプレートID", "予定テンプレート", "分類",
    "開始時刻に知らせる", "次の開始（日本時間）", "見直した（○）", "直した内容",
]
CATEGORY_LABEL = {"adhoc": "不定期", "special": "スペシャル"}


def template_review_rows(db, now: datetime) -> list[list[str]]:
    from sqlalchemy import func, select

    from src.domain_biz.models import BizMaster, ExecutionType
    from src.domain_client.models import ClientMaster
    from src.domain_ope.models import OpeOrder, OrderStatus
    from src.domain_plan.category import category_of
    from src.domain_plan.models import PlanTemplate

    next_start = (
        select(OpeOrder.template_id, func.min(OpeOrder.scheduled_start).label("next_start"))
        .where(OpeOrder.scheduled_start >= now, OpeOrder.status.in_((OrderStatus.PENDING, OrderStatus.READY)))
        .group_by(OpeOrder.template_id).subquery()
    )
    rows = db.execute(
        select(PlanTemplate, BizMaster, ClientMaster.name, next_start.c.next_start)
        .join(BizMaster, BizMaster.id == PlanTemplate.biz_master_id)
        .join(ClientMaster, ClientMaster.code == BizMaster.client_code)
        .outerjoin(next_start, next_start.c.template_id == PlanTemplate.id)
        .order_by(BizMaster.client_code, BizMaster.business_code, PlanTemplate.name, PlanTemplate.id)
    ).all()
    out = []
    for t, biz, client_name, nxt in rows:
        category = category_of(t.ui_metadata)
        if category not in CATEGORY_LABEL:
            continue
        out.append([
            biz.client_code, client_name, biz.business_code, biz.name,
            "はい" if biz.execution_type == ExecutionType.LIGHTWEIGHT else "", str(t.id), t.name, CATEGORY_LABEL[category],
            "知らせる" if t.start_prompt else "知らせない",
            nxt.astimezone(LOCAL_TIMEZONE).strftime("%Y-%m-%d %H:%M") if nxt else "", "", "",
        ])
    return out


def render_template_csv(rows: list[list[str]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(TEMPLATE_CSV_HEADER)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8-sig")


# ---------------------------------------------------------------------------
# まとめ
# ---------------------------------------------------------------------------
def run_checks(db, smtp, expect: Expect, now: datetime) -> list[Check]:
    return [
        check_smtp(smtp, expect),
        check_quiet_hours(db, expect),
        check_destinations(db, expect),
        check_agent_key(db, expect, now),
        check_worker(db, now),
    ]


def render_report(checks: list[Check], now: datetime, templates: list[list[str]] | None = None, csv_path: Path | None = None) -> str:
    lines = [f"並行稼働の事前確認（{now.astimezone(LOCAL_TIMEZONE):%Y-%m-%d %H:%M} 日本時間。読むだけで何も変えていません）", ""]
    for c in checks:
        lines.append(f"{'OK' if c.ok else 'NG'}  {c.name}: {c.summary}")
        lines.extend(f"      - {d}" for d in c.details)
    if templates is not None:
        on = sum(1 for r in templates if r[8] == "知らせる")
        lines += ["", f"見直し: 不定期・スペシャルの予定テンプレート {len(templates)} 件（開始時刻に知らせる {on}・知らせない {len(templates) - on}）"
                  + (f" → {csv_path}" if csv_path else "")]
    ng = [c.name for c in checks if not c.ok]
    lines += ["", "判定: " + ("すべて OK" if not ng else f"NG {len(ng)} 件（{'・'.join(ng)}）。画面で直してから、もう一度動かしてください")]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="並行稼働の事前確認（読むだけ）")
    parser.add_argument("--qa-address", required=True, help="並行稼働の間、メールを届けてよい唯一のアドレス（例: QA チームのメールボックス）")
    parser.add_argument("--quiet", type=parse_quiet, default=(time(22, 0), time(7, 0)), help="知らせない時間帯（既定 22:00-07:00、終わってから知らせる）")
    parser.add_argument("--key-max-age-hours", type=float, default=26, help="API キーが最後に使われてからの許容時間（既定 26）")
    parser.add_argument("--smtp-host", help="期待する SMTP サーバー（省略時は設定があるかだけ）")
    parser.add_argument("--smtp-port", type=int, help="期待する SMTP のポート")
    parser.add_argument("--smtp-from", help="期待する送り主のアドレス")
    parser.add_argument("--templates-csv", type=Path, help="不定期・スペシャルのテンプレートの見直し用 CSV を書くファイル")
    args = parser.parse_args(argv)
    expect = Expect(qa_address=args.qa_address, quiet_start=args.quiet[0], quiet_end=args.quiet[1],
                    key_max_age=timedelta(hours=args.key_max_age_hours), smtp_host=args.smtp_host, smtp_port=args.smtp_port,
                    smtp_from=args.smtp_from)
    now = utc_now()
    try:
        from src.infrastructure.config import settings
        from src.infrastructure.database import SessionLocal, engine

        engine.echo = False
        with SessionLocal() as db:
            checks = run_checks(db, settings, expect, now)
            templates = template_review_rows(db, now)  # 件数は毎回の報告に出す。CSV は頼まれたときだけ
        if args.templates_csv:
            args.templates_csv.parent.mkdir(parents=True, exist_ok=True)
            args.templates_csv.write_bytes(render_template_csv(templates))
    except Exception as error:  # 接続できない・表が無いなど
        sys.stderr.write(f"確認できませんでした: {error}\n")
        return 2
    report = render_report(checks, now, templates, args.templates_csv)
    try:
        sys.stdout.write(report)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "ascii"
        sys.stdout.write(report.encode(encoding, errors="replace").decode(encoding))
    return 0 if all(c.ok for c in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
