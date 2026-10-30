# tests/test_release_job.py
"""
自動リリースのジョブ（4.5: domain_ope.services.release_due_orders）のテスト。
オーダーは plan の予定から自動で作られる（4.3。受け手は conftest.py で登録済み）。

※ テストごとに一意な顧客コードを使ってデータを分離しています。
"""
import uuid
from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import select

from src.domain_biz import services as biz_services
from src.domain_biz.models import ReleaseMode
from src.domain_biz.schemas import BizMasterCreate, BizMasterUpdate
from src.domain_client.schemas import ClientCreate
from src.domain_ope import services as ope
from src.domain_ope.models import EventSource, OpeOrder, OrderEventType, OrderStatus
from src.domain_plan import services as plan_services
from src.domain_plan.models import TemplateType
from src.domain_plan.schemas import RRuleTemplateCreate
from src.infrastructure.database import SessionLocal
from src.infrastructure.repositories import client_repo, ope_order_repo

JST = timezone(timedelta(hours=9))


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def business(db, mode: ReleaseMode, release_time=time(3, 0), days_before=0):
    """業務と毎日 9:00 の予定（オーダーは自動で作られる）。オーダーを予定日順に返す"""
    code = "R" + uuid.uuid4().hex[:3].upper()  # 他のテストの顧客とコードが衝突しないよう、別の先頭文字を使う
    client_repo.create(db, obj_in=ClientCreate(code=code, name="release client"))
    biz = biz_services.create_biz(db, BizMasterCreate(client_code=code, name="release biz", business_seq=1, manager_email=None, description=None))
    biz_services.update_biz(db, biz.id, BizMasterUpdate.model_validate(
        {"release_mode": mode, "release_time": release_time, "release_days_before": days_before}
    ))
    template = plan_services.create_template(db, RRuleTemplateCreate(
        biz_master_id=biz.id, name="Daily", template_type=TemplateType.RRULE, timezone="Asia/Tokyo",
        start_time=time(9, 0), duration_minutes=60, anchor_date=date(2026, 1, 1), rrule="FREQ=DAILY",
    ))
    db.expire_all()
    return list(db.scalars(select(OpeOrder).where(OpeOrder.template_id == template.id).order_by(OpeOrder.scheduled_start)))


def at(monkeypatch, day: date, hour: int, minute: int = 0):
    monkeypatch.setattr(ope, "_now", lambda: datetime.combine(day, time(hour, minute), tzinfo=JST))


def statuses(db, orders):
    db.expire_all()
    return [db.get(OpeOrder, o.id).status for o in orders]


def day_of(order) -> date:
    return order.scheduled_start.astimezone(JST).date()


def test_releases_at_the_business_time_on_the_scheduled_day(db, monkeypatch):
    orders = business(db, ReleaseMode.AUTOMATIC)  # 既定: 予定日当日 03:00
    target, next_day = orders[5], orders[6]
    d = day_of(target)

    at(monkeypatch, d, 2, 59)
    assert target.id not in ope.release_due_orders(db).released
    assert statuses(db, [target]) == [OrderStatus.PENDING]

    at(monkeypatch, d, 3, 0)
    result = ope.release_due_orders(db)
    assert target.id in result.released and next_day.id not in result.released
    assert statuses(db, [target, next_day]) == [OrderStatus.READY, OrderStatus.PENDING]
    # それより前の日のオーダーも（止まっていた間の分として）まとめてリリース済み
    assert set(statuses(db, orders[:5])) == {OrderStatus.READY}

    event = ope_order_repo.get_events(db, order_id=target.id)[-1]
    assert (event.event_type, event.from_status, event.to_status, event.source, event.actor_staff_id) == (
        OrderEventType.STATUS_CHANGED, OrderStatus.PENDING, OrderStatus.READY, EventSource.SYSTEM, None,
    )
    # 承認済みの SOP バージョンが無い業務のため「バージョンなし」で固定される（4.6）
    assert event.detail == {
        "cause": "automatic release", "release_at": datetime.combine(d, time(3, 0), tzinfo=JST).isoformat(),
        "sop_version": None, "sop_note": f"no SOP version effective on {d.isoformat()}",
    }
    assert db.get(OpeOrder, target.id).released_at is not None

    # 何度実行しても同じ（リリース済みは対象外）
    assert all(o.id not in ope.release_due_orders(db).released for o in orders[:6])


def test_days_before_and_time_of_day(db, monkeypatch):
    orders = business(db, ReleaseMode.AUTOMATIC, release_time=time(21, 30), days_before=1)
    target = orders[8]
    eve = day_of(target) - timedelta(days=1)

    at(monkeypatch, eve, 21, 29)
    ope.release_due_orders(db)
    assert statuses(db, [target]) == [OrderStatus.PENDING]
    at(monkeypatch, eve, 21, 30)
    ope.release_due_orders(db)
    assert statuses(db, [target]) == [OrderStatus.READY]
    assert ope.release_moment(db.get(OpeOrder, target.id), _biz(db, target)) == datetime.combine(eve, time(21, 30), tzinfo=JST)


def _biz(db, order):
    from src.domain_biz.models import BizMaster
    return db.get(BizMaster, order.biz_master_id)


def test_manual_businesses_conflicts_and_already_released_are_skipped(db, monkeypatch):
    manual = business(db, ReleaseMode.MANUAL_APPROVAL)
    auto = business(db, ReleaseMode.AUTOMATIC)
    conflicted, released_by_hand, other = auto[3], auto[4], auto[2]

    # 矛盾のあるオーダーは自動ではリリースしない
    o = db.get(OpeOrder, conflicted.id)
    o.conflict, o.conflict_reason = True, "テスト用の矛盾"
    db.commit()

    at(monkeypatch, day_of(auto[0]), 8)  # 手動リリースの「今」（予定日当日）
    ope.release_order(db, released_by_hand.id, actor_staff_id=_any_staff(db))

    at(monkeypatch, day_of(auto[5]), 3)
    result = ope.release_due_orders(db)
    assert other.id in result.released
    assert conflicted.id not in result.released and released_by_hand.id not in result.released
    assert statuses(db, [conflicted, released_by_hand]) == [OrderStatus.PENDING, OrderStatus.READY]
    # 手動承認の業務は何もしない
    assert set(statuses(db, manual[:6])) == {OrderStatus.PENDING}
    assert not set(o.id for o in manual) & set(result.released)


def _any_staff(db) -> int:
    from src.domain_staff import services as staff_services
    from src.domain_staff.models import RoleCode, StaffStatus
    from src.domain_staff.schemas import StaffCreate
    return staff_services.create_staff(
        db, StaffCreate(login_id="r-" + uuid.uuid4().hex[:10], name="releaser", roles=[RoleCode.RELEASER]),
        actor=None, status=StaffStatus.ACTIVE,
    ).id
