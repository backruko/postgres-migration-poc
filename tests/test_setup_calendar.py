# tests/test_setup_calendar.py
"""最初の営業日カレンダーを作るスクリプト（scripts/setup_calendar.py）のテスト"""
import importlib.util
import uuid
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.domain_plan.schemas import BusinessCalendarCreate

_spec = importlib.util.spec_from_file_location(
    "setup_calendar", Path(__file__).resolve().parent.parent / "scripts" / "setup_calendar.py")
assert _spec and _spec.loader
setup_calendar_script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(setup_calendar_script)


def _code() -> str:
    return f"T_CAL_{uuid.uuid4().hex[:8].upper()}"


def test_creates_the_calendar_once_and_leaves_an_existing_one_alone(db_session):
    code = _code()
    calendar, created = setup_calendar_script.setup_calendar(
        db_session, BusinessCalendarCreate(code=code, name="銀行営業日"))
    assert created
    assert calendar.code == code
    assert sorted(calendar.weekend_days) == [6, 7]  # 既定は土日

    # 2 回目: 何も変えずに同じものを返す（別の週末を渡しても上書きしない）
    again, created_again = setup_calendar_script.setup_calendar(
        db_session, BusinessCalendarCreate(code=code, name="別の名前", weekend_days=[7]))
    assert not created_again
    assert again.id == calendar.id
    assert again.name == "銀行営業日"
    assert sorted(again.weekend_days) == [6, 7]


def test_weekend_days_are_checked_before_anything_is_written():
    with pytest.raises(ValidationError):
        BusinessCalendarCreate(code=_code(), name="x", weekend_days=[0])
    with pytest.raises(ValidationError):
        BusinessCalendarCreate(code=_code(), name="x", weekend_days=[1, 2, 3, 4, 5, 6, 7])
