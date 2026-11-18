# tests/test_sheet_import_rules.py
"""
年間計画シートの取り込み（import_sheet）の決まりのうち、test_plan.py が確かめていないものを固定する
（R.6 で関数を分ける前に。分けた後も同じ結果になること）。
- 断るときの文面: 期間を狭めると残る日付が外に出る（件数と最初の 10 件）・生成マーカーの日付をファイルで渡した
- ファイルに無いマーカーは消さない。replace_events なら、その人の手でない日付だけ消す（人が足した日付は残る）
- 生成マーカーのルール由来の日付は、期間を狭めても断る理由にならない（新しい期間で作り直す）
- 新しいシートはテンプレートに参照されていないので、作り直すテンプレートは 0
"""
import re
import uuid
from datetime import date, timedelta

import pytest

from tests.test_plan import _trading_day_setup, active_events, biz_id, client_code_of, db  # noqa: F401  (fixture は名前で使う)
from src.core.exceptions import BadRequestError
from src.domain_plan import services
from src.domain_plan.schemas import ReferenceSheetEventsCreate, ReferenceSheetImport


def sheet_import(client_code: str, name: str, period: tuple[date, date], events: dict[str, list[date]]) -> ReferenceSheetImport:
    return ReferenceSheetImport.model_validate({
        "client_code": client_code, "sheet_name": name,
        "period_start": period[0].isoformat(), "period_end": period[1].isoformat(),
        "markers": [{"marker": marker} for marker in events],
        "events": [{"event_date": d.isoformat(), "marker": marker} for marker, dates in events.items() for d in dates],
    })


YEAR = (date(2030, 1, 1), date(2030, 12, 31))
UNTIL_OCTOBER = (date(2030, 1, 1), date(2030, 10, 31))


def test_narrowing_the_period_refuses_with_the_count_and_ten_examples(db, biz_id):
    code, name = client_code_of(db, biz_id), "Narrow " + uuid.uuid4().hex[:6]
    december = [date(2030, 12, day) for day in range(1, 13)]  # 12 件が新しい期間の外に出る
    services.import_sheet(db, sheet_import(code, name, YEAR, {"A": [date(2030, 1, 5), *december]}))

    narrowed = sheet_import(code, name, UNTIL_OCTOBER, {"A": [date(2030, 1, 5)]})
    examples = [d.isoformat() for d in december[:10]]
    message = (f"12 existing event date(s) fall outside the new period (e.g. {examples}). "
               "Use replace_events=true to remove them.")
    with pytest.raises(BadRequestError, match=f"^{re.escape(message)}$"):
        services.import_sheet(db, narrowed)
    result = services.import_sheet(db, narrowed, replace_events=True)
    assert (result.events_created, result.events_deleted) == (0, 12)


def test_dates_for_a_generated_marker_are_refused_with_the_marker_names(db, biz_id):
    _calendar, sheet, marker = _trading_day_setup(db, biz_id)
    data = sheet_import(client_code_of(db, biz_id), "FY", (sheet.period_start, sheet.period_end),
                        {marker.marker: [sheet.period_start]})
    message = (f"Markers {[marker.marker]} are generated from a rule; "
               "edit their dates with the events API instead of importing.")
    with pytest.raises(BadRequestError, match=f"^{re.escape(message)}$"):
        services.import_sheet(db, data)
    db.rollback()


def test_rule_dates_outside_a_narrower_period_do_not_block_a_merge(db, biz_id):
    _calendar, sheet, marker = _trading_day_setup(db, biz_id)
    narrower = (sheet.period_start, sheet.period_end - timedelta(days=120))
    assert any(e.event_date > narrower[1] for e in active_events(db, sheet.id, marker.marker))  # 外に出る日付がある
    data = ReferenceSheetImport.model_validate({
        "client_code": client_code_of(db, biz_id), "sheet_name": "FY",
        "period_start": narrower[0].isoformat(), "period_end": narrower[1].isoformat(),
        "markers": [{"marker": marker.marker, "description": marker.description}], "events": [],
    })
    result = services.import_sheet(db, data)  # replace_events なしでも断らない（ルールの日付は作り直す）
    assert (result.markers_created, result.markers_updated, result.events_created, result.events_deleted) == (0, 0, 0, 0)
    assert all(narrower[0] <= e.event_date <= narrower[1] for e in active_events(db, sheet.id, marker.marker))


def test_markers_missing_from_the_file_stay_and_replace_clears_only_their_imported_dates(db, biz_id):
    code, name = client_code_of(db, biz_id), "Missing " + uuid.uuid4().hex[:6]
    a_dates, b_date, b_human = [date(2030, 3, 1), date(2030, 4, 1)], date(2030, 5, 1), date(2030, 6, 1)
    first = services.import_sheet(db, sheet_import(code, name, YEAR, {"A": a_dates, "B": [b_date]}))
    assert first.created and first.templates_regenerated == 0  # 新しいシートはどのテンプレートも参照していない
    services.add_sheet_events(db, first.sheet.id, "B", ReferenceSheetEventsCreate(dates=[b_human]))  # 人が足した日付

    only_a = sheet_import(code, name, YEAR, {"A": a_dates})
    merged = services.import_sheet(db, only_a)  # マージ: B はそのまま
    assert (merged.markers_created, merged.markers_updated, merged.events_created, merged.events_deleted) == (0, 0, 0, 0)
    assert dates_of(merged.sheet, "B") == [b_date, b_human]

    replaced = services.import_sheet(db, only_a, replace_events=True)  # 置き換え: B のマーカーは残り、取り込んだ日付だけ消える
    assert (replaced.events_created, replaced.events_deleted) == (0, 1)
    assert dates_of(replaced.sheet, "B") == [b_human]
    assert dates_of(replaced.sheet, "A") == a_dates


def dates_of(sheet, marker: str) -> list[date]:
    [m] = [m for m in sheet.markers if m.marker == marker]
    return sorted(e.event_date for e in m.events if not e.is_cancelled)
