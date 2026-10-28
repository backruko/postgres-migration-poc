# tests/test_ops.py
"""
coordination（日次ダッシュボード）のテスト。

※ ダッシュボードは全顧客横断のため、テストごとに一意な顧客コードを作り、その顧客の行だけを検証します。
"""
import uuid
from datetime import date, datetime, time, timedelta, timezone

import pytest

from src.coordination import services as ops_services
from src.core.exceptions import BadRequestError
from src.domain_biz.schemas import BizMasterCreate
from src.domain_client.schemas import ClientCreate
from src.domain_plan import services
from src.domain_plan.models import InstanceOrigin, TemplateType, VoidReason
from src.domain_plan.schemas import (
    BusinessCalendarCreate,
    HolidayIn,
    HolidaysAdd,
    MarkerRule,
    PlanInstanceCancel,
    PlanInstanceOverride,
    ReferenceSheetCreate,
    ReferenceSheetEventsCreate,
    ReferenceSheetMarkerCreate,
    RRuleTemplateCreate,
)
from src.infrastructure.database import SessionLocal
from src.infrastructure.repositories import biz_master_repo, client_repo

JST = timezone(timedelta(hours=9))


# ==========================================
# Fixtures / Helpers
# ==========================================
@pytest.fixture
def db():
    # 論理削除の全局scopeは SessionLocal に登録されているため、アプリと同じ SessionLocal を使う
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def make_client(db, name="test client") -> str:
    # test_plan.py の顧客（"T" で始まる）とコードが衝突しないよう、別の先頭文字を使う
    code = "D" + uuid.uuid4().hex[:3].upper()
    client_repo.create(db, obj_in=ClientCreate(code=code, name=name))
    return code


def make_biz(db, client_code, seq=1, name="test biz"):
    return biz_master_repo.create(
        db, obj_in=BizMasterCreate(client_code=client_code, name=name, business_seq=seq, manager_email=None, description=None)
    )


def make_template(db, biz_id, name="Daily", rrule="FREQ=DAILY", start_time=time(9, 0)):
    return services.create_template(db, RRuleTemplateCreate(
        biz_master_id=biz_id, name=name, template_type=TemplateType.RRULE, timezone="Asia/Tokyo",
        start_time=start_time, duration_minutes=60, anchor_date=date(2026, 1, 1), rrule=rrule,
    ))


def make_calendar(db, holidays=()):
    calendar = services.create_calendar(db, BusinessCalendarCreate(code="CAL_" + uuid.uuid4().hex[:8], name="test calendar"))
    if holidays:
        services.add_holidays(db, calendar.id, HolidaysAdd(holidays=list(holidays)))
    return calendar


def next_weekday(weekday: int, weeks_ahead: int = 2) -> date:
    """今日から weeks_ahead 週以上先の、指定曜日（ISO: 1=月）の日付"""
    d = date.today() + timedelta(weeks=weeks_ahead)
    return d + timedelta(days=(weekday - d.isoweekday()) % 7)


def live_on(db, template_id, day):
    return [
        i for i in services.get_instances(db, template_id, limit=1000)
        if not i.voided and i.scheduled_start.astimezone(JST).date() == day
    ]


def mine(dashboard, *codes):
    return {c.client_code: c for c in dashboard.clients if c.client_code in codes}


def pretend_now(monkeypatch, moment: datetime):
    """ダッシュボードの「今」だけをずらす（plan の生成は実際の今のまま）"""
    monkeypatch.setattr(ops_services, "_now", lambda: moment)


# ==========================================
# 日次ダッシュボード
# ==========================================
def test_daily_dashboard_groups_by_client_then_business(db):
    day = next_weekday(3)  # 水曜
    a, b = make_client(db, "client A"), make_client(db, "client B")
    a2 = make_biz(db, a, seq=2, name="A second")
    a1 = make_biz(db, a, seq=1, name="A first")
    a3 = make_biz(db, a, seq=3, name="A deleted")
    b1 = make_biz(db, b, seq=1, name="B first")
    make_template(db, a1.id, name="A daily")
    make_template(db, a2.id, name="A wednesday", rrule="FREQ=WEEKLY;BYDAY=WE", start_time=time(15, 0))
    make_template(db, a2.id, name="A thursday", rrule="FREQ=WEEKLY;BYDAY=TH")
    make_template(db, a3.id, name="A gone")
    make_template(db, b1.id, name="B daily")
    biz_master_repo.remove(db, id=a3.id)  # 論理削除した業務の予定は表示されない

    dash = ops_services.get_daily_dashboard(db, day, "Asia/Tokyo")

    assert dash.date == day and dash.timezone == "Asia/Tokyo"
    assert dash.start == datetime.combine(day, time(0), tzinfo=JST) and dash.end - dash.start == timedelta(days=1)
    clients = mine(dash, a, b)
    assert list(clients) == sorted([a, b])  # 顧客コード順
    assert clients[a].client_name == "client A" and clients[a].milestones == []

    # 業務コード順、予定はその日に開始するものだけ
    assert [x.biz_name for x in clients[a].businesses] == ["A first", "A second"]
    assert [[o.title for o in x.occurrences] for x in clients[a].businesses] == [["A daily"], ["A wednesday"]]
    assert [[o.title for o in x.occurrences] for x in clients[b].businesses] == [["B daily"]]
    for x in clients[a].businesses + clients[b].businesses:
        assert x.voided == []  # 未来の日付では無効化された予定は返さない
        for o in x.occurrences:
            assert o.starts_at.astimezone(JST).date() == day and o.occurrence_date == day
            assert o.origin == InstanceOrigin.GENERATED and not o.is_override and o.source_sheet_event_id is None
    assert clients[a].businesses[1].biz_code == a2.business_code
    assert dash.total >= 3

    # 予定の無い日には顧客自体が出てこない（a の木曜は A daily と A thursday）
    thursday = ops_services.get_daily_dashboard(db, day + timedelta(days=1), "Asia/Tokyo")
    assert [[o.title for o in x.occurrences] for x in mine(thursday, a)[a].businesses] == [["A daily"], ["A thursday"]]


def test_daily_dashboard_holidays_per_calendar_and_sheet_milestones(db, monkeypatch):
    day = next_weekday(3)
    holiday_cal = make_calendar(db, holidays=[HolidayIn(holiday_date=day, name="臨時休業")])
    plain_cal = make_calendar(db)

    dash = ops_services.get_daily_dashboard(db, day, "Asia/Tokyo")
    by_id = {c.calendar_id: c for c in dash.calendars}
    assert not by_id[holiday_cal.id].is_business_day and by_id[holiday_cal.id].holiday_name == "臨時休業"
    assert by_id[plain_cal.id].is_business_day and by_id[plain_cal.id].holiday_name is None
    saturday = ops_services.get_daily_dashboard(db, day + timedelta(days=3), "Asia/Tokyo")
    assert {c.calendar_id: c.is_business_day for c in saturday.calendars}[plain_cal.id] is False

    # 予定が無くてもシートの日付がある顧客は表示する（業務は空）
    code = make_client(db)
    make_biz(db, code)
    sheet = services.create_sheet(db, ReferenceSheetCreate(
        client_code=code, name="FY", period_start=date.today() - timedelta(days=30), period_end=date.today() + timedelta(days=330),
    ))
    services.add_marker(db, sheet.id, ReferenceSheetMarkerCreate(marker="振込日", description="顧客の振込日"))
    services.add_sheet_events(db, sheet.id, "振込日", ReferenceSheetEventsCreate(dates=[day]))
    sheet = services.add_marker(db, sheet.id, ReferenceSheetMarkerCreate(
        marker="本締", rule=MarkerRule(rrule="FREQ=WEEKLY;BYDAY=WE", anchor_date=date(2026, 1, 7)),
    ))
    [rule_marker] = [m for m in sheet.markers if m.marker == "本締"]
    rule_event = next(e for e in rule_marker.events if e.event_date == day)

    client = mine(ops_services.get_daily_dashboard(db, day, "Asia/Tokyo"), code)[code]
    assert client.businesses == []
    assert [(m.marker, m.sheet_name, m.is_generated, m.is_manual) for m in client.milestones] == [
        ("振込日", "FY", False, True),  # 人が追加
        ("本締", "FY", True, False),    # ルール
    ]
    assert client.milestones[0].marker_description == "顧客の振込日"
    assert client.milestones[1].rule_date == day and client.milestones[1].event_id == rule_event.id

    # 取り消したシート日付: 未来の日付では出さず、過去の日付では取消として出す
    services.delete_sheet_event(db, rule_event.id)
    client = mine(ops_services.get_daily_dashboard(db, day, "Asia/Tokyo"), code)[code]
    assert [m.marker for m in client.milestones] == ["振込日"]
    pretend_now(monkeypatch, datetime.combine(day + timedelta(days=1), time(10), tzinfo=JST))
    client = mine(ops_services.get_daily_dashboard(db, day, "Asia/Tokyo"), code)[code]
    assert [(m.marker, m.is_cancelled, m.original_date) for m in client.milestones] == [
        ("振込日", False, None), ("本締", True, day),
    ]


def test_daily_dashboard_past_day_shows_voided_with_reasons(db, monkeypatch):
    day = next_weekday(3)
    code = make_client(db)
    biz = make_biz(db, code)
    kept = make_template(db, biz.id, name="Kept")
    moved = make_template(db, biz.id, name="Moved", start_time=time(11, 0))
    deleted = make_template(db, biz.id, name="Deleted", start_time=time(13, 0))
    [cancelled_instance] = live_on(db, kept.id, day)
    [moved_instance] = live_on(db, moved.id, day)
    [deleted_instance] = live_on(db, deleted.id, day)

    services.cancel_instance(db, cancelled_instance.id, PlanInstanceCancel(note="顧客都合"))
    override = services.override_instance(db, moved_instance.id, PlanInstanceOverride(
        scheduled_start=moved_instance.scheduled_start + timedelta(days=1), note="翌日に延期",
    ))
    services.delete_template(db, deleted.id)

    # 未来の日付: 有効な予定だけ（どれも残っていない）
    assert mine(ops_services.get_daily_dashboard(db, day, "Asia/Tokyo"), code) == {}

    # 過去の日付: 無効化・取消された予定を理由付きで返す（削除されたテンプレートの予定も）
    pretend_now(monkeypatch, datetime.combine(day + timedelta(days=2), time(10), tzinfo=JST))
    [business] = mine(ops_services.get_daily_dashboard(db, day, "Asia/Tokyo"), code)[code].businesses
    assert business.occurrences == []
    voided = {v.instance_id: v for v in business.voided}
    assert set(voided) == {cancelled_instance.id, moved_instance.id, deleted_instance.id}
    assert (voided[cancelled_instance.id].void_reason, voided[cancelled_instance.id].void_note) == (VoidReason.MANUAL, "顧客都合")
    assert voided[moved_instance.id].void_reason == VoidReason.MANUAL and voided[moved_instance.id].title == "Moved"
    assert (voided[deleted_instance.id].void_reason, voided[deleted_instance.id].title) == (VoidReason.TEMPLATE_DELETED, "Deleted")
    assert [v.starts_at for v in business.voided] == sorted(v.starts_at for v in business.voided)

    # 延期先の日には変更後の予定が表示される
    [next_business] = mine(ops_services.get_daily_dashboard(db, day + timedelta(days=1), "Asia/Tokyo"), code)[code].businesses
    occ = next(o for o in next_business.occurrences if o.instance_id == override.id)
    assert occ.origin == InstanceOrigin.OVERRIDE and occ.is_override and occ.occurrence_date == day


def test_daily_dashboard_defaults_and_validation(db):
    dash = ops_services.get_daily_dashboard(db, None, "Asia/Tokyo")
    assert dash.date == datetime.now(JST).date()
    with pytest.raises(BadRequestError):
        ops_services.get_daily_dashboard(db, None, "Mars/Olympus")

    # 生成済み区間（90日）より先の日付も、読むときに生成して返す
    code = make_client(db)
    t = make_template(db, make_biz(db, code).id, name="Far")
    far = date.today() + timedelta(days=200)
    [business] = mine(ops_services.get_daily_dashboard(db, far, "Asia/Tokyo"), code)[code].businesses
    assert [o.template_id for o in business.occurrences] == [t.id]
