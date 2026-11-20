# tests/test_frozen_clock.py
"""止めた時計（tests/frozen_clock.py）が、アプリのどの「いま」にも届き、テストが終われば本物に戻ること"""
from datetime import UTC, date, datetime, timedelta

import pytest

from src.coordination import services as ops_services
from src.core import clock
from src.core.clock import utc_now
from src.domain_plan import services as plan_services
from src.worker_health import recorder
from tests.frozen_clock import FROZEN_AT, FrozenClock


def test_every_name_for_the_clock_is_frozen(frozen_clock):
    expected = FROZEN_AT.astimezone(UTC)
    # 自分の名前で持つモジュール（_now / _utcnow）・clock そのもの・このテストのファイルの import
    for now in (plan_services._now, ops_services._now, recorder._utcnow, clock.utc_now, utc_now):
        assert now() == expected
    assert frozen_clock.today == date(2026, 11, 18)  # 日本時間の今日

    frozen_clock.advance(hours=13)  # 日本時間の 0 時をまたぐ
    assert plan_services._now() == expected + timedelta(hours=13) and frozen_clock.today == date(2026, 11, 19)


def test_the_clock_needs_a_timezone():
    with pytest.raises(ValueError):
        FrozenClock(datetime(2026, 11, 18, 12, 0))


def test_without_the_fixture_the_clock_is_real():
    # 前のテストで止めても、終われば monkeypatch が本物に戻す（順番に関係なく成り立つ）
    assert abs(plan_services._now() - datetime.now(UTC)) < timedelta(seconds=5)
    assert abs(utc_now() - datetime.now(UTC)) < timedelta(seconds=5)
