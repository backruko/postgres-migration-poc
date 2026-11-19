"""
生成済みの範囲の上の境目（materialized_until）をまたぐ時刻の変更（_reconcile の「上の境目をまたいで動いた枠」）。

ふつうのホライズンの境目は UTC の 0 時（日本時間 9:00）。8:30 の毎日の予定を 9:30 に変えると、範囲の最後の日の
新しい開始は境目より後になる。以前はその枠だけ「無くなった」になり、オーダーが取り消された
（取り消されたオーダーは「直前の上流」（L.2）として下流を止め続ける）。今は動いた枠として、同じオーダーの予定を動かす。

いまは今日の 0:30（日本時間）に決める（今日の枠がもう始まっていると、始まった枠は動かさない決まりが混ざるため）。
"""
import uuid
from datetime import UTC, datetime, time, timedelta

import pytest
from sqlalchemy import select

from tests.test_deadline_watch import biz_id, db  # noqa: F401  同じ準備（顧客・業務）
from src.coordination import order_sync
from src.coordination.template_preview import preview_template_update
from src.core.clock import LOCAL_TIMEZONE, local_date_of, utc_now
from src.domain_ope.models import OpeOrder, OpeOrderEvent, OrderEventType, OrderStatus
from src.domain_plan import services as plan_services
from src.domain_plan.models import TemplateType
from src.domain_plan.schemas import PlanTemplateUpdate, RRuleTemplateCreate
from src.infrastructure.repositories import plan_instance_repo

FAR = datetime.max.replace(tzinfo=UTC)


@pytest.fixture
def now(monkeypatch):
    early = datetime.combine(local_date_of(utc_now()), time(0, 30), tzinfo=LOCAL_TIMEZONE)
    monkeypatch.setattr(plan_services, "_now", lambda: early)
    monkeypatch.setattr(order_sync, "_now", lambda: early)
    return early


@pytest.fixture
def template_id(db, biz_id, now):
    """毎日 8:30。作成時のホライズンの境目は日本時間 9:00 なので、最後の日の 8:30 の枠は範囲の中"""
    t = plan_services.create_template(db, RRuleTemplateCreate.model_validate(dict(
        biz_master_id=biz_id, name="edge " + uuid.uuid4().hex[:4], template_type=TemplateType.RRULE, timezone="Asia/Tokyo",
        start_time=time(8, 30), duration_minutes=30, anchor_date=now.date(), rrule="FREQ=DAILY",
    )))
    edge = t.materialized_until
    assert edge is not None and edge.astimezone(LOCAL_TIMEZONE).time() == time(9, 0)  # ふつうのホライズンの境目
    return t.id


def last_day(db, template_id: int):
    """範囲の最後の日（境目 9:00 の日。その日の 8:30 は範囲の中）"""
    edge = plan_services._get_locked_template(db, template_id).materialized_until
    assert edge is not None
    return edge.astimezone(LOCAL_TIMEZONE).date()


def live_by_date(db, template_id: int, now) -> dict:
    by_date: dict = {}
    for i in plan_instance_repo.get_live_in_window(db, template_id=template_id, lower=now, upper=FAR, generated_only=True):
        by_date.setdefault(i.occurrence_date, []).append(i.scheduled_start.astimezone(LOCAL_TIMEZONE).time())
    return by_date


def orders_of(db, template_id: int) -> list[OpeOrder]:
    db.expire_all()
    return list(db.scalars(select(OpeOrder).where(OpeOrder.template_id == template_id).order_by(OpeOrder.scheduled_start)))


def test_a_time_change_across_the_range_edge_moves_the_last_day_instead_of_removing_it(db, now, template_id):
    day = last_day(db, template_id)
    edge = plan_services._get_locked_template(db, template_id).materialized_until
    assert live_by_date(db, template_id, now)[day] == [time(8, 30)]

    preview = preview_template_update(db, template_id, PlanTemplateUpdate(start_time=time(9, 30)), months=4)
    assert not preview.removed and not preview.added  # 確認の画面にも「無くなる」が出ない

    before = {o.occurrence_date: o.id for o in orders_of(db, template_id)}
    plan_services.update_template(db, template_id, PlanTemplateUpdate(start_time=time(9, 30)))

    by_date = live_by_date(db, template_id, now)
    assert by_date[day] == [time(9, 30)]  # 最後の日も動いた（境目 9:00 より後でも作る）
    assert all(times == [time(9, 30)] for times in by_date.values())  # どの日も 1 件だけ
    orders = orders_of(db, template_id)
    assert [o.occurrence_date for o in orders if o.status == OrderStatus.CANCELED] == []  # 取消は 1 件も無い
    last = next(o for o in orders if o.occurrence_date == day)
    assert last.id == before[day] and last.scheduled_start.astimezone(LOCAL_TIMEZONE).time() == time(9, 30)  # 同じオーダーを動かした
    assert db.scalar(select(OpeOrderEvent.id).where(
        OpeOrderEvent.order_id == last.id, OpeOrderEvent.event_type == OrderEventType.RESCHEDULED,
    ))
    assert plan_services._get_locked_template(db, template_id).materialized_until == edge  # 範囲の境目は変えない


def test_after_the_range_grows_or_the_time_moves_back_each_day_still_has_exactly_one_instance(db, now, template_id):
    day = last_day(db, template_id)
    plan_services.update_template(db, template_id, PlanTemplateUpdate(start_time=time(9, 30)))

    # 範囲を延ばす（日次の延長と同じ）: 境目の先に作った予定はそのまま。次の日からは新しく作る
    template = plan_services._get_locked_template(db, template_id)
    assert template.materialized_until is not None
    plan_services._ensure_materialized(db, template, template.materialized_until + timedelta(days=3))
    db.commit()
    by_date = live_by_date(db, template_id, now)
    assert by_date[day] == [time(9, 30)] and by_date[day + timedelta(days=1)] == [time(9, 30)]
    assert all(len(times) == 1 for times in by_date.values())

    # 境目の中へ戻す（9:30 → 8:30）: 境目の先にあった予定は無効になり、どの枠も 8:30 の予定が 1 件だけ
    plan_services.update_template(db, template_id, PlanTemplateUpdate(start_time=time(8, 30)))
    assert all(times == [time(8, 30)] for times in live_by_date(db, template_id, now).values())
    assert [o.occurrence_date for o in orders_of(db, template_id) if o.status == OrderStatus.CANCELED] == []


def test_moving_back_inside_the_edge_before_the_range_grows_leaves_one_instance(db, now, template_id):
    """9:30 にした直後（範囲を延ばす前）に 8:30 へ戻す: 境目の先に作った 9:30 の予定は無効になり、二重にならない"""
    day = last_day(db, template_id)
    plan_services.update_template(db, template_id, PlanTemplateUpdate(start_time=time(9, 30)))
    plan_services.update_template(db, template_id, PlanTemplateUpdate(start_time=time(8, 30)))
    by_date = live_by_date(db, template_id, now)
    assert by_date[day] == [time(8, 30)] and all(times == [time(8, 30)] for times in by_date.values())
    assert [o.occurrence_date for o in orders_of(db, template_id) if o.status == OrderStatus.CANCELED] == []
