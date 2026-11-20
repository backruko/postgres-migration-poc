# tests/test_template_update_rules.py
"""
テンプレートの変更（_apply_template_update）の決まりのうち、ほかのテストが確かめていないものを固定する
（C901（複雑さの上限）で関数を分ける前に。分けた後も同じ結果になること）。
- 参照するシート・マーカーを変える: 新しいマーカーの日付で作り直す（改訂が 1 つ上がる）
- 断るときの文面: 無いシート・ほかの顧客のシート・シートに無いマーカー（断ったときは何も変わらない）
- 確かめる順番: 型に合わない項目 → null → シートの参照
- 生成に影響する変更かどうか（2 つ目の戻り値。保存の前の確認が使う）: 名前・開始時刻に知らせる・締切は影響しない
"""
import re
from datetime import date, time, timedelta

import pytest

from tests.client_codes import new_client_code
from tests.test_plan import biz_id, client_code_of, daily_template, db, live  # noqa: F401  (fixture は名前で使う)
from src.core.clock import local_date_of, utc_now
from src.core.exceptions import BadRequestError, NotFoundError
from src.domain_client.schemas import ClientCreate
from src.domain_plan import services
from src.domain_plan.models import TemplateType
from src.domain_plan.schemas import PlanTemplateUpdate, ReferenceSheetImport, ReferenceSheetTemplateCreate
from src.infrastructure.repositories import biz_master_repo, client_repo

# 変更の前後の決まりを、今日（日本時間）の枠に左右されずに固定するため
pytestmark = pytest.mark.usefixtures("frozen_clock")




def _two_marker_sheet(db, client_code: str, name: str = "Two markers"):
    """マーカー A と B に、今日より後の日付が 2 件ずつあるシート"""
    today = local_date_of(utc_now())
    dates = {"A": [today + timedelta(days=10), today + timedelta(days=40)],
             "B": [today + timedelta(days=20), today + timedelta(days=50)]}
    data = {
        "client_code": client_code, "sheet_name": name,
        "period_start": (today - timedelta(days=30)).isoformat(), "period_end": (today + timedelta(days=300)).isoformat(),
        "markers": [{"marker": m} for m in dates],
        "events": [{"event_date": d.isoformat(), "marker": m} for m, ds in dates.items() for d in ds],
    }
    return services.import_sheet(db, ReferenceSheetImport.model_validate(data)).sheet, dates


def _sheet_template(db, biz_id, sheet_id: int, marker: str = "A"):
    return services.create_template(db, ReferenceSheetTemplateCreate(
        biz_master_id=biz_id, name="On marker", template_type=TemplateType.REFERENCE_SHEET_BASED,
        start_time=time(9, 0), duration_minutes=60, reference_sheet_id=sheet_id, reference_event_marker=marker,
        offset_minutes=0,
    ))


def _update(**fields) -> PlanTemplateUpdate:
    return PlanTemplateUpdate.model_validate(fields)


def _exact(message: str) -> str:
    return f"^{re.escape(message)}$"


def test_changing_the_marker_regenerates_dates_from_the_new_marker(db, biz_id):
    sheet, dates = _two_marker_sheet(db, client_code_of(db, biz_id))
    template = _sheet_template(db, biz_id, sheet.id)
    assert {i.occurrence_date for i in live(db, template.id)} == set(dates["A"])
    revision = template.revision

    updated = services.update_template(db, template.id, _update(reference_event_marker="B"))

    assert updated.reference_event_marker == "B"
    assert updated.revision == revision + 1
    assert {i.occurrence_date for i in live(db, template.id)} == set(dates["B"])


def test_a_marker_missing_from_the_sheet_is_refused_and_nothing_changes(db, biz_id):
    sheet, dates = _two_marker_sheet(db, client_code_of(db, biz_id))
    template = _sheet_template(db, biz_id, sheet.id)

    with pytest.raises(NotFoundError, match=_exact(f"Marker 'Z' not found on ReferenceSheet(id={sheet.id}).")):
        services.update_template(db, template.id, _update(reference_event_marker="Z", name="Renamed"))
    db.rollback()
    again = services.get_template(db, template.id)
    assert (again.reference_event_marker, again.name, again.revision) == ("A", "On marker", template.revision)
    assert {i.occurrence_date for i in live(db, template.id)} == set(dates["A"])


def test_a_missing_sheet_is_refused(db, biz_id):
    sheet, _dates = _two_marker_sheet(db, client_code_of(db, biz_id))
    template = _sheet_template(db, biz_id, sheet.id)

    with pytest.raises(NotFoundError, match=_exact("ReferenceSheet(id=999999999) not found.")):
        services.update_template(db, template.id, _update(reference_sheet_id=999999999))
    db.rollback()


def test_a_sheet_of_another_client_is_refused(db, biz_id):
    sheet, _dates = _two_marker_sheet(db, client_code_of(db, biz_id))
    template = _sheet_template(db, biz_id, sheet.id)
    other_code = new_client_code("T")
    client_repo.create(db, obj_in=ClientCreate(code=other_code, name="other client"))
    other_sheet, _ = _two_marker_sheet(db, other_code, name="Other client's sheet")
    biz = biz_master_repo.get(db, id=biz_id)
    assert biz is not None

    message = (f"ReferenceSheet(id={other_sheet.id}) belongs to client '{other_code}', "
               f"but BizMaster(id={biz_id}) belongs to client '{biz.client_code}'.")
    with pytest.raises(BadRequestError, match=_exact(message)):
        services.update_template(db, template.id, _update(reference_sheet_id=other_sheet.id))
    db.rollback()


def test_fields_of_another_type_are_refused_before_nulls_and_the_sheet(db, biz_id):
    sheet, _dates = _two_marker_sheet(db, client_code_of(db, biz_id))
    template = _sheet_template(db, biz_id, sheet.id)

    # 型に合わない項目が先（null の rrule も、まず型に合わない項目として断る）
    message = "Fields ['anchor_date', 'rrule'] are not applicable to template type 'reference_sheet_based'."
    with pytest.raises(BadRequestError, match=_exact(message)):
        services.update_template(db, template.id, _update(rrule=None, anchor_date=date(2030, 1, 1), start_time=None))
    db.rollback()
    rrule_template = daily_template(db, biz_id)
    message = "Fields ['offset_days', 'reference_event_marker'] are not applicable to template type 'rrule'."
    with pytest.raises(BadRequestError, match=_exact(message)):
        services.update_template(db, rrule_template.id, _update(reference_event_marker="A", offset_days=1))
    db.rollback()

    # null がシートの参照より先（マーカーがシートに無くても、null の文面になる）
    with pytest.raises(BadRequestError, match=_exact("Fields ['duration_minutes', 'start_time'] must not be null.")):
        services.update_template(db, template.id, _update(start_time=None, duration_minutes=None, reference_event_marker="Z"))
    db.rollback()


@pytest.mark.parametrize(("fields", "regenerates"), [
    ({"name": "Renamed"}, False),
    ({"start_prompt": False}, False),
    ({"deadline_mode": "fixed_time", "deadline_time": "18:00"}, False),
    ({"warn_before_minutes": 30}, False),
    ({"start_time": "10:00"}, True),
    ({"name": "Renamed", "start_time": "10:00"}, True),
    ({"start_time": "09:00"}, False),  # 今と同じ値は変更にならない
    ({"reference_event_marker": "B"}, True),
    ({"reference_event_marker": "A"}, False),
])
def test_which_changes_count_as_affecting_generation(db, biz_id, fields, regenerates):
    sheet, _dates = _two_marker_sheet(db, client_code_of(db, biz_id))
    template = _sheet_template(db, biz_id, sheet.id)
    revision = template.revision

    _template, changed = services._apply_template_update(db, template.id, _update(**fields))

    assert changed is regenerates
    assert _template.revision == revision + (1 if regenerates else 0)
    db.rollback()
