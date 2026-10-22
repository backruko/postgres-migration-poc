# tests/test_plan.py
"""
domain_plan のテスト。
- エンジン（純粋関数）のテスト
- Service層のテスト（テスト用DB。conftest.py が create_all でテーブルと不変トリガーを作成します）

※ Service層は内部で commit するため、テストごとに一意な顧客コードを使ってデータを分離しています。
"""
import json
import uuid
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import text

from src.core.exceptions import BadRequestError, DuplicatedRecordError, NotFoundError
from src.domain_biz.schemas import BizMasterCreate
from src.domain_client.schemas import ClientCreate
from src.domain_plan import engine, services
from src.domain_plan.models import InstanceOrigin, TemplateType, VoidReason
from src.domain_plan.schemas import (
    MarkerRule,
    ReferenceSheetCreate,
    ReferenceSheetEventsCreate,
    ReferenceSheetEventUpdate,
    ReferenceSheetMarkerCreate,
    ReferenceSheetMarkerUpdate,
    BusinessCalendarCreate,
    BusinessCalendarUpdate,
    HolidayIn,
    HolidaysAdd,
    ExplicitDatesTemplateCreate,
    PlanInstanceCancel,
    PlanInstanceOverride,
    PlanInstanceRevert,
    PlanOneOffCreate,
    PlanTemplateUpdate,
    ReferenceSheetImport,
    ReferenceSheetTemplateCreate,
    RRuleTemplateCreate,
    UIMetadata,
)
from src.infrastructure.database import SessionLocal
from src.infrastructure.repositories import biz_master_repo, client_repo

UTC = timezone.utc
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


@pytest.fixture
def biz_id(db):
    code = "T" + uuid.uuid4().hex[:3].upper()
    client_repo.create(db, obj_in=ClientCreate(code=code, name="test client"))
    biz = biz_master_repo.create(db, obj_in=BizMasterCreate(client_code=code, name="test biz", business_seq=1, manager_email=None, description=None))
    return biz.id


def daily_template(db, biz_id, **kwargs):
    data = dict(
        biz_master_id=biz_id, name="Daily", template_type=TemplateType.RRULE, timezone="Asia/Tokyo",
        start_time=time(9, 0), duration_minutes=60, anchor_date=date(2026, 1, 1), rrule="FREQ=DAILY",
    )
    data.update(kwargs)
    return services.create_template(db, RRuleTemplateCreate.model_validate(data))


def live(db, template_id, origin=None):
    return sorted(
        (
            i for i in services.get_instances(db, template_id, limit=1000)
            if not i.voided and (origin is None or i.origin == origin)
        ),
        key=lambda i: i.scheduled_start,
    )


def by_id(db, template_id, instance_id):
    return next(i for i in services.get_instances(db, template_id, limit=1000) if i.id == instance_id)


def cache_rows(db, biz_id, days=120):
    now = datetime.now(UTC)
    return services.get_occurrences(db, biz_id, now, now + timedelta(days=days))


# ==========================================
# Engine（純粋関数）
# ==========================================
WINDOW = (datetime(2026, 10, 1, tzinfo=UTC), datetime(2026, 12, 1, tzinfo=UTC))


def test_engine_rrule_keeps_wall_clock_across_dst():
    occs = engine.expand_rrule(
        rrule="FREQ=DAILY", anchor_date=date(2026, 10, 30), start_time=time(9, 0), tz_name="America/New_York",
        duration_minutes=60, window_start=WINDOW[0], window_end=WINDOW[1],
    )
    by_day = {o.occurrence_date: o.start.hour for o in occs}
    assert by_day[date(2026, 10, 31)] == 13  # EDT (UTC-4)
    assert by_day[date(2026, 11, 2)] == 14   # EST (UTC-5)


def test_engine_sheet_offset_is_local_and_slot_is_event_date():
    [occ] = engine.expand_sheet_events(
        events=[(7, date(2026, 11, 10))], start_time=time(9, 0), offset_minutes=-1440, tz_name="Asia/Tokyo",
        duration_minutes=30, window_start=WINDOW[0], window_end=WINDOW[1],
    )
    assert occ.start.astimezone(JST) == datetime(2026, 11, 9, 9, 0, tzinfo=JST)
    assert occ.occurrence_date == date(2026, 11, 10)
    assert occ.source_sheet_event_id == 7


# 2026年11月: 3日(火)=文化の日、23日(月)=勤労感謝の日、土日=週末
JP_CAL = engine.BusinessCalendar(weekend_days=frozenset({6, 7}), holidays=frozenset({date(2026, 11, 3), date(2026, 11, 23)}))


def explicit(dates, policy, calendar: engine.BusinessCalendar | None = JP_CAL, window=WINDOW):
    return engine.expand_explicit_dates(
        dates=dates, start_time=time(0, 0), tz_name="UTC", duration_minutes=60,
        window_start=window[0], window_end=window[1], calendar=calendar, holiday_policy=policy,
    )


def test_engine_skip_and_window_is_half_open():
    occs = explicit([date(2026, 11, 3), date(2026, 11, 4), date(2026, 11, 7), date(2026, 12, 1)], engine.HolidayPolicy.SKIP)
    # 11/3 は休日、11/7 は土曜（スキップ）、12/1 00:00 は窓の上端（含まない）
    assert [o.occurrence_date for o in occs] == [date(2026, 11, 4)]


@pytest.mark.parametrize("policy, rule_date, expected", [
    (engine.HolidayPolicy.AS_IS, date(2026, 11, 3), date(2026, 11, 3)),
    (engine.HolidayPolicy.FOLLOWING, date(2026, 11, 3), date(2026, 11, 4)),            # 休日 -> 翌営業日
    (engine.HolidayPolicy.PRECEDING, date(2026, 11, 3), date(2026, 11, 2)),            # 休日 -> 前営業日
    (engine.HolidayPolicy.FOLLOWING, date(2026, 11, 21), date(2026, 11, 24)),          # 土 -> 日 -> 祝 -> 火
    (engine.HolidayPolicy.PRECEDING, date(2026, 11, 8), date(2026, 11, 6)),            # 日 -> 金
    (engine.HolidayPolicy.MODIFIED_FOLLOWING, date(2026, 10, 31), date(2026, 10, 30)), # 翌営業日が11月 -> 前営業日
    (engine.HolidayPolicy.MODIFIED_FOLLOWING, date(2026, 11, 7), date(2026, 11, 9)),   # 月内なら通常の翌営業日
    (engine.HolidayPolicy.MODIFIED_PRECEDING, date(2026, 11, 1), date(2026, 11, 2)),   # 前営業日が10月 -> 翌営業日
])
def test_engine_holiday_policies(policy, rule_date, expected):
    [occ] = explicit([rule_date], policy, window=(datetime(2026, 10, 1, tzinfo=UTC), datetime(2026, 12, 31, tzinfo=UTC)))
    assert occ.start.date() == expected
    assert occ.occurrence_date == rule_date  # 発生枠の識別子は元の日付のまま
    assert occ.rolled == (expected != rule_date)


def test_engine_business_day_offset():
    def sheet(offset_days, unit, policy=engine.HolidayPolicy.AS_IS):
        [occ] = engine.expand_sheet_events(
            events=[(1, date(2026, 11, 24))], start_time=time(9, 0), offset_minutes=0, tz_name="Asia/Tokyo",
            duration_minutes=60, window_start=WINDOW[0], window_end=WINDOW[1],
            offset_days=offset_days, offset_day_unit=unit, calendar=JP_CAL, holiday_policy=policy,
        )
        return occ.start.astimezone(JST).date()

    # 11/24(火) の3営業日前: 23(祝) 22(日) 21(土) を数えず 20(金)=1, 19(木)=2, 18(水)=3
    assert sheet(-3, engine.DayUnit.BUSINESS) == date(2026, 11, 18)
    assert sheet(-3, engine.DayUnit.CALENDAR) == date(2026, 11, 21)                                  # 暦日なら土曜
    assert sheet(-3, engine.DayUnit.CALENDAR, engine.HolidayPolicy.FOLLOWING) == date(2026, 11, 24)   # 土曜 -> 翌営業日
    assert sheet(2, engine.DayUnit.BUSINESS) == date(2026, 11, 26)


def test_engine_collapses_rolled_occurrences_on_same_day():
    occs = engine.expand_rrule(
        rrule="FREQ=DAILY", anchor_date=date(2026, 11, 6), start_time=time(9, 0), tz_name="UTC", duration_minutes=60,
        window_start=datetime(2026, 11, 6, tzinfo=UTC), window_end=datetime(2026, 11, 11, tzinfo=UTC),
        calendar=JP_CAL, holiday_policy=engine.HolidayPolicy.FOLLOWING,
    )
    # 土(7)・日(8)は月(9)へ振替されるが、月曜はその日本来の回1つにまとめる
    assert [(o.start.date(), o.occurrence_date, o.rolled) for o in occs] == [
        (date(2026, 11, 6), date(2026, 11, 6), False),
        (date(2026, 11, 9), date(2026, 11, 9), False),
        (date(2026, 11, 10), date(2026, 11, 10), False),
    ]


def test_engine_rolls_occurrences_into_the_window():
    # 毎月1日。11/1(日) は PRECEDING で 10/30(金) に繰り上がり、窓 [10/25, 10/31) に入る
    occs = engine.expand_rrule(
        rrule="FREQ=MONTHLY;BYMONTHDAY=1", anchor_date=date(2026, 1, 1), start_time=time(9, 0), tz_name="UTC",
        duration_minutes=60, window_start=datetime(2026, 10, 25, tzinfo=UTC), window_end=datetime(2026, 10, 31, tzinfo=UTC),
        calendar=JP_CAL, holiday_policy=engine.HolidayPolicy.PRECEDING,
    )
    assert [(o.start.date(), o.occurrence_date) for o in occs] == [(date(2026, 10, 30), date(2026, 11, 1))]


def test_engine_requires_calendar_for_rolling_and_business_offsets():
    with pytest.raises(engine.CalendarError):
        explicit([date(2026, 11, 3)], engine.HolidayPolicy.FOLLOWING, calendar=None)
    with pytest.raises(engine.CalendarError):
        engine.expand_sheet_events(
            events=[(1, date(2026, 11, 24))], start_time=time(9, 0), offset_minutes=0, tz_name="UTC",
            duration_minutes=60, window_start=WINDOW[0], window_end=WINDOW[1],
            offset_days=-1, offset_day_unit=engine.DayUnit.BUSINESS,
        )


# ==========================================
# Override / Cancel / Revert
# ==========================================
def test_override_survives_template_revision(db, biz_id):
    t = daily_template(db, biz_id)
    target = live(db, t.id)[3]

    ov = services.override_instance(
        db, target.id, PlanInstanceOverride(scheduled_start=target.scheduled_start + timedelta(hours=2))
    )
    assert ov.origin == InstanceOrigin.OVERRIDE and ov.overrides_instance_id == target.id

    services.update_template(db, t.id, PlanTemplateUpdate(start_time=time(10, 0)))
    generated_dates = [i.occurrence_date for i in live(db, t.id, InstanceOrigin.GENERATED)]
    assert target.occurrence_date not in generated_dates
    assert any(i.id == ov.id for i in live(db, t.id))


def test_revert_override_puts_slot_back_under_rule(db, biz_id):
    t = daily_template(db, biz_id)
    target = live(db, t.id)[3]
    ov = services.override_instance(
        db, target.id, PlanInstanceOverride(scheduled_start=target.scheduled_start + timedelta(hours=2))
    )
    others_before = {i.id for i in live(db, t.id)} - {ov.id}

    result = services.revert_instance(db, ov.id, PlanInstanceRevert(note="mistake"))

    assert result.voided_instance is not None and result.voided_instance.id == ov.id
    assert result.voided_instance.void_reason == VoidReason.REVERTED
    assert result.voided_instance.void_note == "mistake"
    restored = result.restored_instance
    assert restored is not None
    assert restored.origin == InstanceOrigin.GENERATED
    assert restored.occurrence_date == target.occurrence_date
    assert restored.scheduled_start == target.scheduled_start
    # 他の発生枠はそのまま（作り直されていない）
    assert {i.id for i in live(db, t.id)} - {restored.id} == others_before
    cached = {c.instance_id for c in cache_rows(db, biz_id)}
    assert restored.id in cached and ov.id not in cached


def test_revert_cancel_and_revert_from_middle_of_chain(db, biz_id):
    t = daily_template(db, biz_id)
    first, second = live(db, t.id)[2], live(db, t.id)[4]

    # 取消 -> 解除
    services.cancel_instance(db, first.id, PlanInstanceCancel())
    result = services.revert_instance(db, first.id, PlanInstanceRevert())
    assert result.voided_instance is None
    assert result.restored_instance is not None and result.restored_instance.occurrence_date == first.occurrence_date

    # 連鎖（元 -> ov1 -> ov2）の途中のIDを指定しても連鎖全体が解除される
    ov1 = services.override_instance(db, second.id, PlanInstanceOverride(scheduled_start=second.scheduled_start + timedelta(hours=1)))
    ov2 = services.override_instance(db, ov1.id, PlanInstanceOverride(scheduled_start=second.scheduled_start + timedelta(hours=3)))
    result = services.revert_instance(db, ov1.id, PlanInstanceRevert())
    assert result.voided_instance is not None and result.voided_instance.id == ov2.id
    assert result.restored_instance is not None and result.restored_instance.occurrence_date == second.occurrence_date

    # 解除後に再び手動変更でき、その判断も再生成で維持される
    again = services.cancel_instance(db, result.restored_instance.id, PlanInstanceCancel())
    assert again.voided
    services.update_template(db, t.id, PlanTemplateUpdate(duration_minutes=45))
    assert second.occurrence_date not in [i.occurrence_date for i in live(db, t.id, InstanceOrigin.GENERATED)]


def test_revert_rejections(db, biz_id, monkeypatch):
    t = daily_template(db, biz_id)
    untouched, target = live(db, t.id)[1], live(db, t.id)[5]

    with pytest.raises(BadRequestError):
        services.revert_instance(db, untouched.id, PlanInstanceRevert())  # 手動変更されていない

    services.cancel_instance(db, target.id, PlanInstanceCancel())
    services.revert_instance(db, target.id, PlanInstanceRevert())
    with pytest.raises(DuplicatedRecordError):
        services.revert_instance(db, target.id, PlanInstanceRevert())  # 解除済み

    # 元の発生枠が過去になっている場合は戻せない
    other = live(db, t.id)[7]
    services.cancel_instance(db, other.id, PlanInstanceCancel())
    future_now = other.scheduled_start + timedelta(hours=1)
    monkeypatch.setattr(services, "_now", lambda: future_now)
    with pytest.raises(BadRequestError):
        services.revert_instance(db, other.id, PlanInstanceRevert())


# ==========================================
# One-off（単発追加）
# ==========================================
def test_one_off_instance_lifecycle(db, biz_id):
    t = daily_template(db, biz_id, duration_minutes=90)
    start = datetime.now(JST).replace(hour=15, minute=0, second=0, microsecond=0) + timedelta(days=2)

    one = services.add_one_off_instance(db, t.id, PlanOneOffCreate(scheduled_start=start))
    assert one.origin == InstanceOrigin.ONE_OFF and one.overrides_instance_id is None
    assert one.scheduled_end - one.scheduled_start == timedelta(minutes=90)  # テンプレートの所要時間
    assert one.occurrence_date == start.date()
    assert any(c.instance_id == one.id and c.origin == InstanceOrigin.ONE_OFF for c in cache_rows(db, biz_id))

    # 同時刻にルール生成分があっても作成できる（一意制約はルール生成分のみ）
    gen = live(db, t.id, InstanceOrigin.GENERATED)[0]
    services.add_one_off_instance(db, t.id, PlanOneOffCreate(scheduled_start=gen.scheduled_start))

    # テンプレートを変更しても単発追加は維持される
    services.update_template(db, t.id, PlanTemplateUpdate(start_time=time(11, 0)))
    assert any(i.id == one.id for i in live(db, t.id))

    # 単発追加はルールに戻せない（取消は可能）
    with pytest.raises(BadRequestError):
        services.revert_instance(db, one.id, PlanInstanceRevert())
    moved = services.override_instance(db, one.id, PlanInstanceOverride(scheduled_start=start + timedelta(hours=1)))
    with pytest.raises(BadRequestError):
        services.revert_instance(db, moved.id, PlanInstanceRevert())
    assert services.cancel_instance(db, moved.id, PlanInstanceCancel()).void_reason == VoidReason.MANUAL

    # テンプレート削除で単発追加も無効化される
    services.delete_template(db, t.id)
    assert all(i.voided for i in services.get_instances(db, t.id, limit=1000) if i.scheduled_start >= datetime.now(UTC))


# ==========================================
# ui_metadata
# ==========================================
def test_ui_metadata_merge_and_propagation(db, biz_id):
    t = daily_template(db, biz_id, ui_metadata=UIMetadata(bg_color="#FF0000", text_color="#FFFFFF", icon="money-bag"))
    assert t.ui_metadata == {"bg_color": "#FF0000", "text_color": "#FFFFFF", "icon": "money-bag"}
    assert all(c.ui_metadata == t.ui_metadata for c in cache_rows(db, biz_id))

    # インスタンス側の上書きはキー単位でマージ（未知のキーもそのまま保存）
    target, other = live(db, t.id)[2], live(db, t.id)[4]
    ov = services.override_instance(
        db, target.id,
        PlanInstanceOverride(
            scheduled_start=target.scheduled_start + timedelta(hours=1),
            ui_metadata=UIMetadata(bg_color="#00FF00", is_bold=True, badge="urgent"),  # type: ignore[call-arg]
        ),
    )
    assert ov.ui_metadata == {"bg_color": "#00FF00", "is_bold": True, "badge": "urgent"}
    cache = {c.instance_id: c for c in cache_rows(db, biz_id)}
    assert cache[ov.id].ui_metadata == {
        "bg_color": "#00FF00", "text_color": "#FFFFFF", "icon": "money-bag", "is_bold": True, "badge": "urgent",
    }

    # 再オーバーライドで ui_metadata を省略すると元の値を引き継ぐ
    ov2 = services.override_instance(db, ov.id, PlanInstanceOverride(scheduled_start=target.scheduled_start + timedelta(hours=2)))
    assert ov2.ui_metadata == ov.ui_metadata

    # テンプレートの変更はリビジョンを上げずにキャッシュの実効値へ反映
    revision = t.revision
    t = services.update_template(db, t.id, PlanTemplateUpdate(ui_metadata=UIMetadata(bg_color="#0000FF")))
    assert t.revision == revision
    cache = {c.instance_id: c for c in cache_rows(db, biz_id)}
    assert cache[other.id].ui_metadata == {"bg_color": "#0000FF"}
    assert cache[ov2.id].ui_metadata == {"bg_color": "#00FF00", "is_bold": True, "badge": "urgent"}

    # null でクリア
    t = services.update_template(db, t.id, PlanTemplateUpdate(ui_metadata=None))
    assert t.ui_metadata is None
    cache = {c.instance_id: c for c in cache_rows(db, biz_id)}
    assert cache[other.id].ui_metadata is None

    # キャッシュ再構築でも同じ実効値になる
    services.rebuild_cache(db, t.id)
    assert {c.instance_id: c.ui_metadata for c in cache_rows(db, biz_id)} == {k: v.ui_metadata for k, v in cache.items()}


def test_ui_metadata_validation():
    with pytest.raises(ValidationError):
        UIMetadata(bg_color="red")
    with pytest.raises(ValidationError):
        UIMetadata(is_bold="maybe")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        UIMetadata(note="x" * 3000)  # type: ignore[call-arg]
    assert UIMetadata(bg_color="#abc", icon=None).to_dict() == {"bg_color": "#abc"}


# ==========================================
# 不変性
# ==========================================
def test_instances_are_immutable_in_db(db, biz_id):
    t = services.create_template(db, ExplicitDatesTemplateCreate(
        biz_master_id=biz_id, name="Dates", template_type=TemplateType.EXPLICIT_DATES,
        start_time=time(9, 0), duration_minutes=30, dates=[date.today() + timedelta(days=3)],
    ))
    [instance] = live(db, t.id)
    for sql in ["UPDATE plan_instance SET ui_metadata = '{}' WHERE id = :id", "DELETE FROM plan_instance WHERE id = :id"]:
        with pytest.raises(Exception, match="immutable"):
            db.execute(text(sql), {"id": instance.id})
        db.rollback()


# ==========================================
# ReferenceSheet 一括インポート
# ==========================================
MOCK_SHEET_PATH = Path(__file__).resolve().parent.parent / "mock_data" / "desensitized_mock_data.json"


def mock_sheet(client_code: str) -> dict:
    data = json.loads(MOCK_SHEET_PATH.read_text(encoding="utf-8"))
    data["client_code"] = client_code
    return data


def client_code_of(db, biz_id) -> str:
    biz = biz_master_repo.get(db, id=biz_id)
    assert biz is not None
    return biz.client_code


def test_import_mock_sheet_creates_then_is_idempotent(db, biz_id):
    data = mock_sheet(client_code_of(db, biz_id))

    result = services.import_sheet(db, ReferenceSheetImport.model_validate(data))
    assert result.created
    assert result.markers_created == len(data["markers"]) == 8
    assert result.events_created == len(data["events"]) == 679
    descriptions = {m.marker: m.description for m in result.sheet.markers}
    assert descriptions["送付"] == "決済代行会社への送付"
    assert sum(len(m.events) for m in result.sheet.markers) == 679

    again = services.import_sheet(db, ReferenceSheetImport.model_validate(data))
    assert not again.created and again.sheet.id == result.sheet.id
    assert (again.markers_created, again.markers_updated, again.events_created, again.events_deleted) == (0, 0, 0, 0)


def test_import_merge_and_replace_propagate_to_templates(db, biz_id):
    data = mock_sheet(client_code_of(db, biz_id))
    sheet = services.import_sheet(db, ReferenceSheetImport.model_validate(data)).sheet

    template = services.create_template(db, ReferenceSheetTemplateCreate(
        biz_master_id=biz_id, name="Task on 本締", template_type=TemplateType.REFERENCE_SHEET_BASED,
        start_time=time(9, 0), duration_minutes=60, reference_sheet_id=sheet.id, reference_event_marker="本締",
        offset_minutes=0,
    ))
    before = {i.occurrence_date for i in live(db, template.id)}
    assert before  # 90日以内に「本締」の日付がある

    # 1件の日付を後ろへずらす（replace_events=True: 元の日付は削除）+ 説明を変更 + マーカーを追加
    moved_from = min(before)
    moved_to = moved_from + timedelta(days=1)
    for e in data["events"]:
        if e["marker"] == "本締" and e["event_date"] == moved_from.isoformat():
            e["event_date"] = moved_to.isoformat()
    data["markers"].append({"marker": "⑥", "description": "new"})
    data["markers"][0]["description"] = "仮締（更新）"

    result = services.import_sheet(db, ReferenceSheetImport.model_validate(data), replace_events=True)
    assert (result.markers_created, result.markers_updated) == (1, 1)
    assert (result.events_created, result.events_deleted) == (1, 1)
    assert result.templates_regenerated == 1
    after = {i.occurrence_date for i in live(db, template.id)}
    assert moved_from not in after and moved_to in after

    # 既定（マージ）では、ファイルに無い日付は削除されない
    data["events"] = [e for e in data["events"] if e["marker"] != "本締"]
    merged = services.import_sheet(db, ReferenceSheetImport.model_validate(data))
    assert merged.events_deleted == 0 and merged.templates_regenerated == 0
    assert {i.occurrence_date for i in live(db, template.id)} == after


def test_import_validation_errors(db, biz_id):
    code = client_code_of(db, biz_id)

    data = mock_sheet(code)
    data["events"].append({"event_date": "2024-01-01", "marker": "XX"})
    data["events"].append({"event_date": "2030-01-01", "marker": "本締"})
    data["markers"].append({"marker": "本締"})
    with pytest.raises(ValidationError) as exc:
        ReferenceSheetImport.model_validate(data)
    message = str(exc.value)
    assert "undeclared markers: ['XX']" in message
    assert "outside the sheet period" in message
    assert "duplicated markers: ['本締']" in message

    with pytest.raises(NotFoundError):
        # テスト用の顧客コードは常に "T" 始まりのため "ZZZZ" は存在しない
        services.import_sheet(db, ReferenceSheetImport.model_validate(mock_sheet("ZZZZ")))

    # 期間を狭めると既存の日付が外に出る場合、マージでは拒否（replace_events=True なら削除して取り込める）
    data = mock_sheet(code)
    services.import_sheet(db, ReferenceSheetImport.model_validate(data))
    data["period_end"] = "2027-03-31"
    data["events"] = [e for e in data["events"] if e["event_date"] <= "2027-03-31"]
    with pytest.raises(BadRequestError):
        services.import_sheet(db, ReferenceSheetImport.model_validate(data))
    result = services.import_sheet(db, ReferenceSheetImport.model_validate(data), replace_events=True)
    assert result.events_deleted > 0
    assert all(e.event_date <= date(2027, 3, 31) for m in result.sheet.markers for e in m.events)


# ==========================================
# 休日ポリシー（Service層）
# ==========================================
def make_calendar(db, holidays=()):
    calendar = services.create_calendar(db, BusinessCalendarCreate(code="CAL_" + uuid.uuid4().hex[:8], name="test calendar"))
    if holidays:
        services.add_holidays(db, calendar.id, HolidaysAdd(holidays=[HolidayIn(holiday_date=d) for d in holidays]))
    return calendar


def next_weekday(weekday: int, weeks_ahead: int = 2) -> date:
    """今日から weeks_ahead 週以上先の、指定曜日（ISO: 1=月）の日付"""
    d = date.today() + timedelta(weeks=weeks_ahead)
    return d + timedelta(days=(weekday - d.isoweekday()) % 7)


def test_holiday_added_later_rolls_future_instances(db, biz_id):
    calendar = make_calendar(db)
    t = daily_template(db, biz_id, rrule="FREQ=WEEKLY;BYDAY=MO", business_calendar_id=calendar.id,
                       holiday_policy=engine.HolidayPolicy.FOLLOWING)
    monday = next_weekday(1)
    assert monday in [i.scheduled_start.astimezone(JST).date() for i in live(db, t.id)]

    # その月曜を休日にすると、翌営業日（火曜）へ振替される。発生枠は月曜のまま
    result = services.add_holidays(db, calendar.id, HolidaysAdd(holidays=[HolidayIn(holiday_date=monday, name="臨時休業")]))
    assert (result.holidays_added, result.templates_regenerated) == (1, 1)
    [moved] = [i for i in live(db, t.id) if i.occurrence_date == monday]
    assert moved.scheduled_start.astimezone(JST).date() == monday + timedelta(days=1)
    voided = [i for i in services.get_instances(db, t.id, limit=1000) if i.voided and i.occurrence_date == monday]
    assert voided and voided[0].void_reason == VoidReason.CALENDAR_CHANGED

    # 休日を取り消すと元に戻る
    services.delete_holiday(db, calendar.id, monday)
    [back] = [i for i in live(db, t.id) if i.occurrence_date == monday]
    assert back.scheduled_start.astimezone(JST).date() == monday


def test_skip_policy_and_weekend_change(db, biz_id):
    calendar = make_calendar(db)
    t = daily_template(db, biz_id, business_calendar_id=calendar.id, holiday_policy=engine.HolidayPolicy.SKIP)
    weekdays = {i.scheduled_start.astimezone(JST).isoweekday() for i in live(db, t.id)}
    assert weekdays == {1, 2, 3, 4, 5}

    # 週末を「金土」に変更すると、日曜が営業日・金曜が非営業日になる
    services.update_calendar(db, calendar.id, BusinessCalendarUpdate(weekend_days=[5, 6]))
    now = datetime.now(UTC)
    weekdays = {i.scheduled_start.astimezone(JST).isoweekday() for i in live(db, t.id) if i.scheduled_start > now}
    assert weekdays == {1, 2, 3, 4, 7}


def test_manual_override_survives_calendar_change(db, biz_id):
    calendar = make_calendar(db)
    t = daily_template(db, biz_id, rrule="FREQ=WEEKLY;BYDAY=WE", business_calendar_id=calendar.id,
                       holiday_policy=engine.HolidayPolicy.PRECEDING)
    wednesday = next_weekday(3)
    [target] = [i for i in live(db, t.id) if i.occurrence_date == wednesday]
    ov = services.override_instance(db, target.id, PlanInstanceOverride(scheduled_start=target.scheduled_start + timedelta(hours=3)))

    services.add_holidays(db, calendar.id, HolidaysAdd(holidays=[HolidayIn(holiday_date=wednesday)]))
    slot = [i for i in live(db, t.id) if i.occurrence_date == wednesday]
    assert [i.id for i in slot] == [ov.id]  # 手動変更は維持され、ルールからの振替分は作られない


def test_sheet_business_day_offset_end_to_end(db, biz_id):
    friday = next_weekday(5, weeks_ahead=3)
    holiday = friday - timedelta(days=1)  # 木曜を休日に
    calendar = make_calendar(db, holidays=[holiday])
    monday = friday + timedelta(days=3)
    sheet = services.import_sheet(db, ReferenceSheetImport.model_validate({
        "client_code": client_code_of(db, biz_id), "sheet_name": "S", "period_start": "2026-01-01", "period_end": "2030-12-31",
        "markers": [{"marker": "本締"}], "events": [{"event_date": monday.isoformat(), "marker": "本締"}],
    })).sheet
    t = services.create_template(db, ReferenceSheetTemplateCreate(
        biz_master_id=biz_id, name="3営業日前", template_type=TemplateType.REFERENCE_SHEET_BASED,
        start_time=time(9, 0), duration_minutes=60, reference_sheet_id=sheet.id, reference_event_marker="本締",
        offset_days=-3, offset_day_unit=engine.DayUnit.BUSINESS, business_calendar_id=calendar.id,
    ))
    # 本締=月曜。3営業日前: 金(1)、木は休日、水(2)、火(3)
    [occ] = live(db, t.id)
    assert occ.scheduled_start.astimezone(JST).date() == friday - timedelta(days=3)
    assert occ.occurrence_date == monday


def test_holiday_policy_validation(db, biz_id):
    with pytest.raises(ValidationError):
        RRuleTemplateCreate(
            biz_master_id=biz_id, name="x", template_type=TemplateType.RRULE, start_time=time(9, 0),
            duration_minutes=60, anchor_date=date(2026, 1, 1), rrule="FREQ=DAILY",
            holiday_policy=engine.HolidayPolicy.FOLLOWING,
        )
    with pytest.raises(NotFoundError):
        daily_template(db, biz_id, business_calendar_id=999999)
    with pytest.raises(ValidationError):
        BusinessCalendarCreate(code="X", name="x", weekend_days=[1, 2, 3, 4, 5, 6, 7])

    calendar = make_calendar(db)
    t = daily_template(db, biz_id, business_calendar_id=calendar.id, holiday_policy=engine.HolidayPolicy.FOLLOWING)
    with pytest.raises(BadRequestError):
        services.update_template(db, t.id, PlanTemplateUpdate(business_calendar_id=None))  # FOLLOWING のまま解除は不可
    db.rollback()
    t = services.update_template(db, t.id, PlanTemplateUpdate(business_calendar_id=None, holiday_policy=engine.HolidayPolicy.AS_IS))
    assert t.business_calendar_id is None and t.holiday_policy == engine.HolidayPolicy.AS_IS


# ==========================================
# 生成マーカー（ルールから基準日を生成）
# ==========================================
def test_engine_expand_rule_dates_trading_day():
    # 毎月27日、休日なら翌営業日。2026/11/27(金)、2026/12/27(日) -> 12/28(月)、2027/2/27(土) -> 3/1(月)
    dates = engine.expand_rule_dates(
        rrule="FREQ=MONTHLY;BYMONTHDAY=27", anchor_date=date(2026, 1, 1),
        period_start=date(2026, 11, 1), period_end=date(2027, 3, 31),
        calendar=JP_CAL, holiday_policy=engine.HolidayPolicy.FOLLOWING,
    )
    assert [(r.rule_date, r.actual_date) for r in dates] == [
        (date(2026, 11, 27), date(2026, 11, 27)),
        (date(2026, 12, 27), date(2026, 12, 28)),
        (date(2027, 1, 27), date(2027, 1, 27)),
        (date(2027, 2, 27), date(2027, 3, 1)),
        (date(2027, 3, 27), date(2027, 3, 29)),
    ]
    # 振替後の日付が期間外になるものは含めない（2027/3/27 -> 3/29 は期間 3/28 までだと外）
    clipped = engine.expand_rule_dates(
        rrule="FREQ=MONTHLY;BYMONTHDAY=27", anchor_date=date(2026, 1, 1),
        period_start=date(2027, 3, 1), period_end=date(2027, 3, 28),
        calendar=JP_CAL, holiday_policy=engine.HolidayPolicy.FOLLOWING,
    )
    assert [r.actual_date for r in clipped] == [date(2027, 3, 1)]  # 2/27(土) の振替分は期間内


def _trading_day_setup(db, biz_id, holidays=()):
    calendar = make_calendar(db, holidays=holidays)
    today = date.today()
    sheet = services.create_sheet(db, ReferenceSheetCreate(
        client_code=client_code_of(db, biz_id), name="FY", period_start=today - timedelta(days=30),
        period_end=today + timedelta(days=330),
    ))
    sheet = services.add_marker(db, sheet.id, ReferenceSheetMarkerCreate(
        marker="取引日", description="毎月27日（休日なら翌営業日）",
        rule=MarkerRule(rrule="FREQ=MONTHLY;BYMONTHDAY=27", anchor_date=date(2026, 1, 1),
                        business_calendar_id=calendar.id, holiday_policy=engine.HolidayPolicy.FOLLOWING),
    ))
    [marker] = [m for m in sheet.markers if m.marker == "取引日"]
    return calendar, sheet, marker


def active_events(db, sheet_id, marker="取引日"):
    sheet = services.get_sheet(db, sheet_id)
    [m] = [m for m in sheet.markers if m.marker == marker]
    return sorted((e for e in m.events if not e.is_cancelled), key=lambda e: e.event_date)


def test_generated_marker_dates_and_jobs_offset_from_them(db, biz_id):
    calendar, sheet, marker = _trading_day_setup(db, biz_id)
    assert marker.is_generated and len(marker.events) >= 11
    for e in marker.events:
        assert e.rule_date is not None and e.rule_date.day == 27 and not e.is_manual
        assert e.event_date.isoweekday() < 6 and e.event_date >= e.rule_date  # 週末なら翌営業日

    def job(name, offset_days):
        return services.create_template(db, ReferenceSheetTemplateCreate(
            biz_master_id=biz_id, name=name, template_type=TemplateType.REFERENCE_SHEET_BASED, start_time=time(9, 0),
            duration_minutes=60, reference_sheet_id=sheet.id, reference_event_marker="取引日",
            offset_days=offset_days, offset_day_unit=engine.DayUnit.BUSINESS, business_calendar_id=calendar.id,
        ))

    before, after = job("2営業日前", -2), job("1営業日後", 1)
    cal = engine.BusinessCalendar(weekend_days=frozenset({6, 7}), holidays=frozenset())
    events = {e.id: e.event_date for e in active_events(db, sheet.id)}
    for t, n in ((before, -2), (after, 1)):
        for i in live(db, t.id):
            # 振替後の取引日（実際の日付）から数える
            assert i.source_sheet_event_id is not None
            assert i.scheduled_start.astimezone(JST).date() == cal.add_business_days(events[i.source_sheet_event_id], n)


def test_holiday_on_27th_rolls_generated_date_and_cascades_to_jobs(db, biz_id):
    calendar, sheet, marker = _trading_day_setup(db, biz_id)
    target = next(e for e in active_events(db, sheet.id) if e.event_date > date.today() + timedelta(days=7)
                  and e.event_date == e.rule_date)
    # ORMオブジェクトは再生成で更新されるため、変更前の値を控えておく
    target_id, target_date, target_rule_date = target.id, target.event_date, target.rule_date
    t = services.create_template(db, ReferenceSheetTemplateCreate(
        biz_master_id=biz_id, name="取引日当日", template_type=TemplateType.REFERENCE_SHEET_BASED, start_time=time(9, 0),
        duration_minutes=60, reference_sheet_id=sheet.id, reference_event_marker="取引日",
    ))

    # 27日を休日にすると、マーカーの日付が翌営業日へ動き（同じ行のまま）、ジョブも追従する
    result = services.add_holidays(db, calendar.id, HolidaysAdd(holidays=[HolidayIn(holiday_date=target_date)]))
    assert result.templates_regenerated == 1
    moved = next(e for e in active_events(db, sheet.id) if e.id == target_id)
    assert moved.event_date > target_date and moved.rule_date == target_rule_date and not moved.is_manual
    assert moved.event_date.isoweekday() < 6
    [inst] = [i for i in live(db, t.id) if i.source_sheet_event_id == target_id]
    assert inst.scheduled_start.astimezone(JST).date() == moved.event_date


def test_manual_edits_on_generated_dates_always_win(db, biz_id):
    calendar, sheet, marker = _trading_day_setup(db, biz_id)
    events = [e for e in active_events(db, sheet.id) if e.event_date > date.today()]
    edited, cancelled = events[1], events[2]
    assert edited.rule_date is not None and cancelled.rule_date is not None
    edited_to = edited.rule_date + timedelta(days=1)

    # 人の編集: 28日に変更 / 取消 / 手動追加
    services.update_sheet_event(db, edited.id, ReferenceSheetEventUpdate(event_date=edited_to))
    services.delete_sheet_event(db, cancelled.id)
    extra = events[3].event_date + timedelta(days=3)
    services.add_sheet_events(db, sheet.id, "取引日", ReferenceSheetEventsCreate(dates=[extra]))

    def state():
        sheet_now = services.get_sheet(db, sheet.id)
        [m] = [m for m in sheet_now.markers if m.marker == "取引日"]
        return {e.id: (e.event_date, e.is_manual, e.is_cancelled) for e in m.events}

    s = state()
    assert s[edited.id] == (edited_to, True, False)
    assert s[cancelled.id][1:] == (True, True)
    assert any(v == (extra, True, False) for v in s.values())

    # ルール変更・カレンダー変更（その日を休日に）でも、人の判断はすべて維持される
    services.update_marker(db, sheet.id, "取引日", ReferenceSheetMarkerUpdate(
        rule=MarkerRule(rrule="FREQ=MONTHLY;BYMONTHDAY=27", anchor_date=date(2026, 1, 1),
                        business_calendar_id=calendar.id, holiday_policy=engine.HolidayPolicy.PRECEDING)))
    services.add_holidays(db, calendar.id, HolidaysAdd(holidays=[HolidayIn(holiday_date=edited_to)]))
    s2 = state()
    assert s2[edited.id] == s[edited.id] and s2[cancelled.id] == s[cancelled.id]
    assert any(v == (extra, True, False) for v in s2.values())
    # 取り消した枠はルールから作り直されない
    assert not any(e.rule_date == cancelled.rule_date and not e.is_cancelled for e in active_events(db, sheet.id))

    # 人の判断を取り消すとルールの日付に戻る
    reverted = services.revert_sheet_event(db, cancelled.id)
    assert not reverted.is_manual and not reverted.is_cancelled and reverted.rule_date == cancelled.rule_date


def test_generated_marker_rejects_import_and_can_stop_generating(db, biz_id):
    calendar, sheet, marker = _trading_day_setup(db, biz_id)
    data = {
        "client_code": client_code_of(db, biz_id), "sheet_name": "FY",
        "period_start": sheet.period_start.isoformat(), "period_end": sheet.period_end.isoformat(),
        "markers": [{"marker": "取引日"}], "events": [{"event_date": sheet.period_start.isoformat(), "marker": "取引日"}],
    }
    with pytest.raises(BadRequestError):
        services.import_sheet(db, ReferenceSheetImport.model_validate(data))
    db.rollback()

    # replace_events でも生成マーカーの日付は消えない
    data["events"] = []
    services.import_sheet(db, ReferenceSheetImport.model_validate(data), replace_events=True)
    count = len(active_events(db, sheet.id))
    assert count >= 11

    # 生成をやめると、現在の日付が通常の日付として残る
    m = services.update_marker(db, sheet.id, "取引日", ReferenceSheetMarkerUpdate(rule=None))
    assert not m.is_generated and len(m.events) == count
    assert all(e.rule_date is None and not e.is_manual for e in m.events)


# ==========================================
# 顧客単位のカレンダー（全業務）
# ==========================================
def test_client_calendar_includes_all_businesses_of_the_client(db, biz_id):
    code = client_code_of(db, biz_id)
    other_biz = biz_master_repo.create(
        db, obj_in=BizMasterCreate(client_code=code, name="second biz", business_seq=2, manager_email=None, description=None)
    )
    daily_template(db, biz_id, name="Daily A", rrule="FREQ=WEEKLY;BYDAY=MO")
    daily_template(db, other_biz.id, name="Daily B", rrule="FREQ=WEEKLY;BYDAY=FR", start_time=time(15, 0))

    # 別の顧客の予定は含まれない
    foreign_code = "T" + uuid.uuid4().hex[:3].upper()
    client_repo.create(db, obj_in=ClientCreate(code=foreign_code, name="other client"))
    foreign_biz = biz_master_repo.create(
        db, obj_in=BizMasterCreate(client_code=foreign_code, name="x", business_seq=1, manager_email=None, description=None)
    )
    daily_template(db, foreign_biz.id, name="Foreign")

    today = date.today()
    next_month = date(today.year + today.month // 12, today.month % 12 + 1, 1)
    month = f"{next_month.year:04d}-{next_month.month:02d}"
    cal = services.get_client_calendar(db, code.lower(), month, "Asia/Tokyo")  # 顧客コードは大文字小文字を区別しない

    assert cal.client_code == code and cal.month == month
    assert cal.days[0].date == next_month and len(cal.days) in (28, 29, 30, 31)
    assert cal.days[-1].date.month == next_month.month
    occs = [o for d in cal.days for o in d.occurrences]
    assert cal.total == len(occs) > 0
    assert {o.title for o in occs} == {"Daily A", "Daily B"}
    assert {o.biz_name for o in occs} == {"test biz", "second biz"}
    for day in cal.days:
        assert day.weekday == day.date.isoweekday()
        for o in day.occurrences:
            assert o.starts_at.astimezone(JST).date() == day.date
            assert day.weekday == (1 if o.title == "Daily A" else 5)
        assert [o.starts_at for o in day.occurrences] == sorted(o.starts_at for o in day.occurrences)

    # 論理削除した業務の予定は表示されない
    biz_master_repo.remove(db, id=other_biz.id)
    cal = services.get_client_calendar(db, code, month, "Asia/Tokyo")
    assert {o.title for d in cal.days for o in d.occurrences} == {"Daily A"}


def test_client_calendar_validation(db, biz_id):
    with pytest.raises(NotFoundError):
        services.get_client_calendar(db, "ZZZZ", "2026-11", "Asia/Tokyo")
    with pytest.raises(BadRequestError):
        services.get_client_calendar(db, client_code_of(db, biz_id), "2026-11", "Mars/Base")
    with pytest.raises(BadRequestError):
        services.get_client_occurrences(
            db, client_code_of(db, biz_id), datetime(2026, 11, 1), datetime(2026, 12, 1)  # タイムゾーン無し
        )
