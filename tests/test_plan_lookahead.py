# tests/test_plan_lookahead.py
"""
予定を作る先の上限（今 + 730 日）より先の日付を、何度開いても作り直さない（回帰テスト）。
上限が 1 秒ごとに進んでいたとき、上限より先の月のカレンダーや日次ダッシュボードを開くたびに、どのテンプレートも
「数秒足りない」と見なされ、行ロック・作り直し・書き込みをしていた（1 回の表示で約 180 の文、約 0.4 秒）。
今は上限を UTC の翌 0 時に切り上げるので、同じ日の 2 回目からは読むだけになる。
顧客のカレンダーで確かめる（日次ダッシュボードと同じ作り方で、この顧客のテンプレートだけを見るので、ほかのテストの
テンプレートに左右されない）。
"""
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import event

from tests.client_codes import new_client_code
from src.coordination import order_sync
from src.core.clock import utc_now
from src.domain_biz.schemas import BizMasterCreate
from src.domain_client.schemas import ClientCreate
from src.domain_plan import services as plan_services
from src.domain_plan.models import TemplateType
from src.domain_plan.schemas import RRuleTemplateCreate
from src.infrastructure.database import SessionLocal, engine
from src.infrastructure.repositories import biz_master_repo, client_repo


class Clock:
    """止めた時計。advance で進める（本当の時計だと、上限が動くかどうかが実行の時刻で変わる）"""

    def __init__(self, at: datetime):
        self.at = at

    def __call__(self) -> datetime:
        return self.at

    def advance(self, **kw) -> None:
        self.at += timedelta(**kw)


@pytest.fixture
def clock(monkeypatch):
    # 日付の境目（UTC の 0 時）から遠い時刻: 今日の 12:00 UTC
    c = Clock(datetime.combine(utc_now().date(), time(12, 0), tzinfo=UTC))
    monkeypatch.setattr(plan_services, "_now", c)
    monkeypatch.setattr(order_sync, "_now", c)
    return c


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _client_with_daily_template(db) -> str:
    code = new_client_code("I")
    client_repo.create(db, obj_in=ClientCreate(code=code, name="lookahead client"))
    biz = biz_master_repo.create(
        db, obj_in=BizMasterCreate(client_code=code, name="lookahead biz", business_seq=1, manager_email=None, description=None)
    )
    plan_services.create_template(db, RRuleTemplateCreate.model_validate(dict(
        biz_master_id=biz.id, name="Daily", template_type=TemplateType.RRULE, timezone="Asia/Tokyo",
        start_time=time(9, 0), duration_minutes=60, anchor_date=date(2026, 1, 1), rrule="FREQ=DAILY",
    )))
    return code


def _statements(fn) -> list[str]:
    seen: list[str] = []

    def grab(_conn, _cur, statement, *_a, **_k):
        seen.append(statement)

    event.listen(engine, "before_cursor_execute", grab)
    try:
        fn()
    finally:
        event.remove(engine, "before_cursor_execute", grab)
    return seen


def test_the_cap_moves_once_a_day_not_every_second():
    noon = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
    cap = plan_services._lookahead_cap(noon)
    assert cap == datetime(2028, 11, 20, 0, 0, tzinfo=UTC)  # 今 + 730 日の日の、翌 0 時（UTC）
    assert cap >= noon + plan_services.MAX_LOOKAHEAD  # 切り上げなので、上限が 730 日より短くなることはない
    assert plan_services._lookahead_cap(noon + timedelta(hours=11, minutes=59)) == cap  # 同じ日の中では動かない
    assert plan_services._lookahead_cap(noon + timedelta(hours=12)) == cap + timedelta(days=1)  # 翌日に 1 日進む
    # 日本時間で渡されても、UTC の日付で切り上げる
    assert plan_services._lookahead_cap(noon.astimezone(ZoneInfo("Asia/Tokyo"))) == cap


def test_a_month_past_the_cap_is_only_read_on_the_second_view(db, clock):
    code = _client_with_daily_template(db)
    far = (clock() + plan_services.MAX_LOOKAHEAD + timedelta(days=40)).strftime("%Y-%m")

    def view():
        plan_services.get_client_calendar(db, client_code=code, month=far, tz_name="Asia/Tokyo")

    view()  # 1 回目: 上限まで作る
    clock.advance(seconds=5)  # 同じ日の少しあと（以前はこれだけで、もう一度すべて作り直した）
    second = _statements(view)

    writes = [s for s in second if s.lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE")) or "FOR UPDATE" in s.upper()]
    assert writes == []  # ロックも書き込みもしない
    assert len(second) == 4  # 読むだけ: 顧客・テンプレート・予定・凡例（以前はテンプレート 1 つにつき約 10 増えた）
