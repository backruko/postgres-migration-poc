# tests/test_marker_generation_rules.py
"""
生成マーカーの日付づくり（_generate_marker_events）の決まりのうち、test_plan.py が確かめていないものを固定する
（C901（複雑さの上限）で関数を分ける前に。分けた後も同じ結果になること）。
- 人が置いた日付と同じ日には、ルールの日付を置かない（その月はルールの日付が無く、人の日付だけが残る）
- カレンダーの誤り（エンジンの CalendarError）は、マーカーの名前を付けた 400 になり、何も保存されない
- 変更が無ければ、ルールを同じ値で保存し直しても日付は 1 件も変わらない
"""
import re
from datetime import date, timedelta

import pytest

from tests.test_plan import _trading_day_setup, active_events, biz_id, client_code_of, db  # noqa: F401  (fixture は名前で使う)
from src.core.clock import local_date_of, utc_now
from src.core.exceptions import BadRequestError
from src.domain_plan import engine, services
from src.domain_plan.schemas import MarkerRule, ReferenceSheetCreate, ReferenceSheetEventsCreate, ReferenceSheetMarkerCreate, ReferenceSheetMarkerUpdate

# マーカーの日付とシートの期間を、同じ「今日」から決めるため
pytestmark = pytest.mark.usefixtures("frozen_clock")




def _weekday_15th_ahead() -> date:
    """2 か月以上先の、平日の 15 日（休日の扱いで動かない日）"""
    today = local_date_of(utc_now())
    year, month = today.year, today.month
    for _ in range(12):
        month += 1
        if month > 12:
            year, month = year + 1, 1
        d = date(year, month, 15)
        if d > today + timedelta(days=45) and d.isoweekday() < 6:
            return d
    raise AssertionError("no weekday 15th found")


def _rule(calendar_id: int, monthday: int) -> MarkerRule:
    return MarkerRule(rrule=f"FREQ=MONTHLY;BYMONTHDAY={monthday}", anchor_date=date(2026, 1, 1),
                      business_calendar_id=calendar_id, holiday_policy=engine.HolidayPolicy.FOLLOWING)


def test_a_rule_date_on_a_day_a_person_placed_is_not_added(db, biz_id):
    calendar, sheet, _marker = _trading_day_setup(db, biz_id)
    human_day = _weekday_15th_ahead()
    services.add_sheet_events(db, sheet.id, "取引日", ReferenceSheetEventsCreate(dates=[human_day]))

    services.update_marker(db, sheet.id, "取引日", ReferenceSheetMarkerUpdate(rule=_rule(calendar.id, 15)))

    events = active_events(db, sheet.id)
    on_that_day = [e for e in events if e.event_date == human_day]
    assert [(e.is_manual, e.rule_date) for e in on_that_day] == [(True, None)]  # 人の日付だけ。ルールの日付は置かない
    assert not any(e.rule_date == human_day for e in services.get_sheet(db, sheet.id).markers[0].events)
    # ほかの月は、新しいルール（15 日）の日付ができている
    later = [e for e in events if e.event_date > human_day and not e.is_manual]
    assert later and all(e.rule_date is not None and e.rule_date.day == 15 for e in later)


def test_a_calendar_error_names_the_marker_and_saves_nothing(db, biz_id, monkeypatch):
    sheet =services.create_sheet(db, ReferenceSheetCreate(
        client_code=client_code_of(db, biz_id), name="FY", period_start=local_date_of(utc_now()),
        period_end=local_date_of(utc_now()) + timedelta(days=300),
    ))

    def fail(**_kwargs):
        raise engine.CalendarError("No business day found within 366 days of 2031-01-01.")

    monkeypatch.setattr(engine, "expand_rule_dates", fail)
    message = "Marker '取引日': No business day found within 366 days of 2031-01-01."
    with pytest.raises(BadRequestError, match=f"^{re.escape(message)}$"):
        services.add_marker(db, sheet.id, ReferenceSheetMarkerCreate(
            marker="取引日", rule=MarkerRule(rrule="FREQ=MONTHLY;BYMONTHDAY=27", anchor_date=date(2026, 1, 1)),
        ))
    db.rollback()
    assert services.get_sheet(db, sheet.id).markers == []


def test_saving_the_same_rule_again_changes_no_date(db, biz_id):
    calendar, sheet, _marker = _trading_day_setup(db, biz_id)
    before = {e.id: (e.event_date, e.rule_date) for e in services.get_sheet(db, sheet.id).markers[0].events}

    services.update_marker(db, sheet.id, "取引日", ReferenceSheetMarkerUpdate(rule=_rule(calendar.id, 27)))

    after = {e.id: (e.event_date, e.rule_date) for e in services.get_sheet(db, sheet.id).markers[0].events}
    assert after == before
