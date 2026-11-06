# tests/test_worker.py
"""定期処理のワーカー（scripts/worker.py）の時刻の決め方のテスト（DB は使わない）"""
import importlib.util
from datetime import date, datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
_spec = importlib.util.spec_from_file_location("worker", Path(__file__).resolve().parent.parent / "scripts" / "worker.py")
assert _spec and _spec.loader
worker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(worker)


def test_release_runs_just_after_each_minute():
    assert worker.seconds_until_next_run(datetime(2026, 11, 6, 9, 0, 0, tzinfo=timezone.utc)) == 61
    assert worker.seconds_until_next_run(datetime(2026, 11, 6, 9, 0, 30, tzinfo=timezone.utc)) == 31
    assert worker.seconds_until_next_run(datetime(2026, 11, 6, 9, 0, 59, 999999, tzinfo=timezone.utc)) >= 1.0


def test_plan_extension_once_a_day_after_0005_jst():
    at = worker.parse_time("00:05")
    assert at == time(0, 5)
    before = datetime(2026, 11, 7, 0, 4, tzinfo=JST)
    after = datetime(2026, 11, 7, 0, 5, tzinfo=JST)
    assert not worker.extension_due(before, None, at)
    assert worker.extension_due(after, None, at)                      # 起動したとき、その日の分がまだなら
    assert worker.extension_due(after, date(2026, 11, 6), at)         # 前の日にしただけなら
    assert not worker.extension_due(after, date(2026, 11, 7), at)     # その日はもうした
    # 日本時間で判定する（UTC では前の日の 15:05）
    assert worker.extension_due(datetime(2026, 11, 6, 15, 5, tzinfo=timezone.utc), date(2026, 11, 6), at)
