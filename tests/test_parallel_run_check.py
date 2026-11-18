# tests/test_parallel_run_check.py
"""
並行稼働の事前確認（scripts/parallel_run_check.py。P.5a）のテスト。読むだけ（何も変えない）。
テスト用 DB のほかのルール・キーと混ざらないよう、同じトランザクションの中で無効にしてから確かめる（終わりに取り消す）。
- メール: SMTP_HOST が空なら NG。期待する値と違えば NG
- 知らせない時間帯: 有効なルールすべてが 22:00〜07:00・終わってから知らせる
- 宛先: 有効なルールが使う有効な宛先が QA のアドレスだけ（大文字・小文字は区別しない）。使える宛先が無いルールも NG
- API キー: システム連携（読み取り）の有効な職員の、取り消していないキーが 26 時間以内に使われた
- ワーカー: いま動いていて、この 24 時間に問題が無い
- テンプレートの見直しの CSV: 不定期・スペシャルだけ、顧客側の出来事の業務の印、次の開始
"""
import importlib.util
import sys
from datetime import datetime, time, timedelta, UTC
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import insert, update

from tests.test_deadline_watch import biz_id, db, make_order  # noqa: F401  同じ準備
from src.domain_biz.models import BizMaster, ExecutionType
from src.domain_notification.models import NotificationChannel, NotificationDestination, NotificationRule, QuietMode
from src.domain_plan.models import PlanTemplate
from src.domain_staff import credentials, services
from src.domain_staff.models import RoleCode, StaffApiKey, StaffStatus
from src.domain_staff.schemas import StaffCreate
from src.worker_health.models import WorkerBeat, WorkerError, WorkerRun

_spec = importlib.util.spec_from_file_location("parallel_run_check", Path(__file__).resolve().parent.parent / "scripts" / "parallel_run_check.py")
assert _spec and _spec.loader
check = importlib.util.module_from_spec(_spec)
sys.modules["parallel_run_check"] = check  # dataclass は sys.modules のモジュールを引く
_spec.loader.exec_module(check)

QA = "ops-qa-team@corp.com"
EXPECT = check.Expect(qa_address=QA)
NOW = datetime(2031, 6, 1, 3, 0, tzinfo=UTC)  # 12:00 JST（ほかのテストのワーカーの行と混ざらない）


@pytest.fixture
def clean(db):
    """ほかのテストのルール・キーを、このトランザクションの中だけ無効にする"""
    db.execute(update(NotificationRule).values(enabled=False))
    db.execute(update(StaffApiKey).values(revoked_at=datetime.now(UTC)))
    db.flush()
    return db


def destination(db, address: str, enabled=True) -> NotificationDestination:
    d = NotificationDestination(name=f"d {address} {id(object())}", channel=NotificationChannel.EMAIL, address=address, enabled=enabled)
    db.add(d)
    db.flush()
    return d


def rule(db, destinations, *, quiet=(time(22, 0), time(7, 0), QuietMode.HOLD), enabled=True, name="遅れ") -> NotificationRule:
    r = NotificationRule(name=name, event_types=["OrderLate"], template_code="order.late", enabled=enabled,
                         quiet_start=quiet[0], quiet_end=quiet[1], quiet_mode=quiet[2])
    r.destinations = list(destinations)
    db.add(r)
    db.flush()
    return r


def test_smtp():
    smtp = SimpleNamespace(smtp_host="smtp.internal.corp", smtp_port=25, smtp_user=None, smtp_from_address="ops-system-noreply@corp.com",
                           smtp_starttls=None)
    ok = check.check_smtp(smtp, check.Expect(qa_address=QA, smtp_host="smtp.internal.corp", smtp_port=25, smtp_from="OPS-system-noreply@corp.com"))
    assert ok.ok and "smtp.internal.corp:25、ログイン なし、送り主 ops-system-noreply@corp.com" in ok.summary
    wrong = check.check_smtp(smtp, check.Expect(qa_address=QA, smtp_port=587))
    assert not wrong.ok and wrong.details == ["ポートが 587 ではありません"]
    missing = check.check_smtp(SimpleNamespace(**{**vars(smtp), "smtp_host": None}), EXPECT)
    assert not missing.ok and "SMTP_HOST が空" in missing.summary and "worker のコンテナ" in missing.details[0]


def test_quiet_hours_on_every_active_rule(clean):
    db = clean
    assert not check.check_quiet_hours(db, EXPECT).ok  # 有効なルールが無い
    qa = destination(db, QA)
    good = rule(db, [qa])
    rule(db, [qa], enabled=False, quiet=(None, None, None))  # 無効なルールは見ない
    assert check.check_quiet_hours(db, EXPECT).ok
    drop = rule(db, [qa], quiet=(time(22, 0), time(7, 0), QuietMode.DROP), name="落とす")
    none = rule(db, [qa], quiet=(None, None, None), name="なし")
    result = check.check_quiet_hours(db, EXPECT)
    assert not result.ok and "3 件のうち 2 件" in result.summary
    assert result.details == [f"ルール {drop.id}「落とす」: 22:00〜07:00・知らせない", f"ルール {none.id}「なし」: なし〜なし・設定なし"]
    assert not any(f"ルール {good.id}「" in d for d in result.details)
    # 引数で時間帯を変えれば、それに合わせて確かめる
    assert not check.check_quiet_hours(db, check.Expect(qa_address=QA, quiet_start=time(21, 0))).ok


def test_destinations_only_the_qa_address(clean):
    db = clean
    qa, client_box, off = destination(db, "OPS-QA-team@corp.com "), destination(db, "someone@client.example"), destination(db, "x@client.example", enabled=False)
    rule(db, [qa, off])  # 無効な宛先は届かないので見ない
    result = check.check_destinations(db, EXPECT)
    assert result.ok and f"すべて {QA}" in result.summary
    leak = rule(db, [qa, client_box], name="顧客にも")
    result = check.check_destinations(db, EXPECT)
    assert not result.ok and result.details == [f"ルール {leak.id}「顧客にも」→ 宛先 {client_box.id}「{client_box.name}」: someone@client.example"]
    leak.enabled = False
    nobody = rule(db, [off], name="宛先なし")
    result = check.check_destinations(db, EXPECT)
    assert not result.ok and result.details == [f"ルール {nobody.id}「宛先なし」: 使える宛先がありません（だれにも届きません）"]


def test_a_read_only_key_used_since_last_night(clean):
    db = clean
    assert not check.check_agent_key(db, EXPECT, NOW).ok  # キーが無い
    agent = services.create_staff(db, StaffCreate(login_id=f"prc-agent-{id(db)}", name="adapter", roles=[RoleCode.SYSTEM_AGENT]),
                                  actor=None, status=StaffStatus.ACTIVE)
    key = credentials.issue_api_key(db, agent, "nightly schedule", actor=None, now=NOW - timedelta(days=3)).api_key
    result = check.check_agent_key(db, EXPECT, NOW)
    assert not result.ok and "まだ使っていない" in result.details[0]
    key.last_used_at = NOW - timedelta(hours=30)
    db.flush()
    assert not check.check_agent_key(db, EXPECT, NOW).ok
    key.last_used_at = NOW - timedelta(hours=2)
    db.flush()
    result = check.check_agent_key(db, EXPECT, NOW)
    assert result.ok and "2031-06-01 10:00" in result.details[0]
    agent.status = StaffStatus.SUSPENDED
    db.flush()
    assert not check.check_agent_key(db, EXPECT, NOW).ok  # 停止した職員のキーは数えない


def test_worker_running_without_problems_for_24_hours(db):
    run = WorkerRun(host="prc", pid=1, started_at=NOW - timedelta(days=2), last_beat_at=NOW - timedelta(minutes=1), passes=0)
    db.add(run)
    db.flush()
    start = NOW - timedelta(days=2)
    db.execute(insert(WorkerBeat), [{"run_id": run.id, "at": start + timedelta(minutes=i), "duration_ms": 100} for i in range(2 * 1440)])
    db.flush()
    assert check.check_worker(db, NOW).ok
    db.add(WorkerError(run_id=run.id, occurred_at=NOW - timedelta(hours=30), job="release", error_type="X", message="m", traceback=""))
    db.flush()
    assert check.check_worker(db, NOW).ok  # 24 時間より前のエラーは数えない
    db.add(WorkerError(run_id=run.id, occurred_at=NOW - timedelta(hours=1), job="release", error_type="X", message="m", traceback=""))
    db.flush()
    result = check.check_worker(db, NOW)
    assert not result.ok and result.details == ["この 24 時間のエラー: 1 件（画面「ワーカーの状態」で中身を見る）"]
    stale = check.check_worker(db, NOW + timedelta(minutes=10))
    assert not stale.ok and stale.details[0].startswith("いま動いていません（状態 stale") and any("今も続いている" in d for d in stale.details)


def test_template_review_csv(db, biz_id):
    adhoc = make_order(db, biz_id, ui_metadata={"category": "adhoc"}, name="臨時の送信")
    special = make_order(db, biz_id, ui_metadata={"category": "special"}, name="受領の確認")
    make_order(db, biz_id, name="定例の処理")  # 定例は出ない
    biz = db.get(BizMaster, biz_id)
    assert biz is not None
    biz.execution_type = ExecutionType.LIGHTWEIGHT
    db.get(PlanTemplate, special.template_id).start_prompt = False  # type: ignore[union-attr]
    db.flush()
    now = datetime.now(UTC)
    rows = [r for r in check.template_review_rows(db, now) if r[0] == biz.client_code]
    assert [(r[6], r[7], r[8], r[4]) for r in rows] == [("受領の確認", "スペシャル", "知らせない", "はい"), ("臨時の送信", "不定期", "知らせる", "はい")]
    assert rows[1][5] == str(adhoc.template_id) and rows[1][9].endswith("09:00") and rows[1][10:] == ["", ""]
    raw = check.render_template_csv(rows)
    assert raw.startswith(b"\xef\xbb\xbf") and raw.decode("utf-8-sig").splitlines()[0] == ",".join(check.TEMPLATE_CSV_HEADER)


def test_report_and_arguments():
    checks = [check.Check("メール（SMTP）", True, "ok"), check.Check("宛先（QA だけに届くか）", False, "QA 以外", ["ルール 1 → x@y"])]
    text = check.render_report(checks, NOW, [["A"] * 8 + ["知らせる"] + [""] * 3], Path("review.csv"))
    assert "OK  メール（SMTP）: ok" in text and "NG  宛先（QA だけに届くか）: QA 以外\n      - ルール 1 → x@y" in text
    assert "見直し: 不定期・スペシャルの予定テンプレート 1 件（開始時刻に知らせる 1・知らせない 0） → review.csv" in text
    assert "判定: NG 1 件（宛先（QA だけに届くか））" in text
    assert check.parse_quiet("21:30-06:00") == (time(21, 30), time(6, 0))
    with pytest.raises(SystemExit):
        check.main([])  # --qa-address は必ず要る
