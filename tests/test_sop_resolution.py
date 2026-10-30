# tests/test_sop_resolution.py
"""
オーダーの SOP バージョン（4.6）のテスト: 予定日時での解決・指定（pin）と解除・リリース時の固定。
オーダーは plan の予定から自動で作られる（4.3。受け手は conftest.py で登録済み）。

※ テストごとに一意な顧客コードを使ってデータを分離しています。
"""
import uuid
from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import select

from src.core.exceptions import BadRequestError, NotFoundError
from src.domain_biz.models import SOPStatus, SOPVersion
from src.domain_biz.schemas import BizMasterCreate
from src.domain_client.schemas import ClientCreate
from src.domain_ope import services as ope
from src.domain_ope.models import OpeOrder, OrderEventType, OrderStatus
from src.domain_plan import services as plan_services
from src.domain_plan.models import TemplateType
from src.domain_plan.schemas import PlanInstanceOverride, RRuleTemplateCreate
from src.domain_staff import services as staff_services
from src.domain_staff.models import RoleCode, StaffStatus
from src.domain_staff.schemas import StaffCreate
from src.infrastructure.database import SessionLocal
from src.infrastructure.repositories import biz_master_repo, client_repo

JST = timezone(timedelta(hours=9))


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def business_with_orders(db):
    code = "V" + uuid.uuid4().hex[:3].upper()  # 他のテストの顧客とコードが衝突しないよう、別の先頭文字を使う
    client_repo.create(db, obj_in=ClientCreate(code=code, name="sop client"))
    biz = biz_master_repo.create(db, obj_in=BizMasterCreate(client_code=code, name="sop biz", business_seq=1, manager_email=None, description=None))
    template = plan_services.create_template(db, RRuleTemplateCreate(
        biz_master_id=biz.id, name="Daily", template_type=TemplateType.RRULE, timezone="Asia/Tokyo",
        start_time=time(9, 0), duration_minutes=60, anchor_date=date(2026, 1, 1), rrule="FREQ=DAILY",
    ))
    db.expire_all()
    orders = list(db.scalars(select(OpeOrder).where(OpeOrder.template_id == template.id).order_by(OpeOrder.scheduled_start)))
    return biz.id, orders


def versions(db, biz_id, switch_day: date):
    """v1（〜switch_day の前日まで有効）、v2（switch_day 0:00 から有効）、草稿 v3"""
    switch = datetime.combine(switch_day, time(0), tzinfo=JST)
    v1 = SOPVersion(biz_master_id=biz_id, version_number=1, version_tag="v1.0.0", status=SOPStatus.ACTIVE,
                    effective_at=switch - timedelta(days=400), expired_at=switch - timedelta(seconds=1))
    v2 = SOPVersion(biz_master_id=biz_id, version_number=2, version_tag="v2.0.0", status=SOPStatus.FUTURE,
                    effective_at=switch, expired_at=None)
    v3 = SOPVersion(biz_master_id=biz_id, version_number=3, version_tag="v3.0.0-draft", status=SOPStatus.DRAFT)
    db.add_all([v1, v2, v3])
    db.commit()
    return v1, v2, v3


@pytest.fixture
def releaser(db):
    return staff_services.create_staff(
        db, StaffCreate(login_id="v-" + uuid.uuid4().hex[:10], name="releaser", roles=[RoleCode.RELEASER]),
        actor=None, status=StaffStatus.ACTIVE,
    ).id


def day_of(order) -> date:
    return order.scheduled_start.astimezone(JST).date()


def test_version_is_resolved_by_the_scheduled_date(db):
    biz_id, orders = business_with_orders(db)
    before, after = orders[4], orders[6]
    v1, v2, _ = versions(db, biz_id, switch_day=day_of(orders[5]))

    assert ope.get_order(db, before.id).sop_version.model_dump() == {"id": v1.id, "version_tag": "v1.0.0", "source": "effective"}  # type: ignore[union-attr]
    assert ope.get_order(db, after.id).sop_version.model_dump() == {"id": v2.id, "version_tag": "v2.0.0", "source": "effective"}  # type: ignore[union-attr]


def test_pin_and_unpin(db, releaser):
    biz_id, orders = business_with_orders(db)
    target = orders[8]
    v1, v2, draft = versions(db, biz_id, switch_day=day_of(orders[5]))
    other_biz_id, _ = business_with_orders(db)
    other_v, _, _ = versions(db, other_biz_id, switch_day=day_of(orders[5]))

    r = ope.pin_sop_version(db, target.id, releaser, v1.id, reason="新手順の研修が未完了")
    assert r.pinned_sop_version_id == v1.id and r.sop_version is not None
    assert (r.sop_version.id, r.sop_version.source) == (v1.id, "pinned")  # 古いバージョンも指定できる
    pinned = r.events[-1]
    assert pinned.event_type == OrderEventType.PINNED and pinned.actor_staff_id == releaser
    assert pinned.detail == {"sop_version_id": v1.id, "version_tag": "v1.0.0", "previous_pin": None}

    with pytest.raises(BadRequestError, match="already pinned"):
        ope.pin_sop_version(db, target.id, releaser, v1.id)
    with pytest.raises(BadRequestError, match="draft"):
        ope.pin_sop_version(db, target.id, releaser, draft.id)
    with pytest.raises(NotFoundError):
        ope.pin_sop_version(db, target.id, releaser, other_v.id)  # 別の業務のバージョン

    r = ope.unpin_sop_version(db, target.id, releaser)
    assert r.pinned_sop_version_id is None and r.sop_version is not None and (r.sop_version.id, r.sop_version.source) == (v2.id, "effective")
    assert r.events[-1].event_type == OrderEventType.UNPINNED and r.events[-1].detail == {"previous_pin": v1.id}
    with pytest.raises(BadRequestError, match="no pinned"):
        ope.unpin_sop_version(db, target.id, releaser)


def test_release_freezes_the_version(db, releaser, monkeypatch):
    biz_id, orders = business_with_orders(db)
    target, pinned_order = orders[7], orders[9]
    v1, v2, _ = versions(db, biz_id, switch_day=day_of(orders[5]))
    monkeypatch.setattr(ope, "_now", lambda: datetime.combine(day_of(orders[0]), time(8), tzinfo=JST))

    r = ope.release_order(db, target.id, releaser)
    assert r.frozen_sop_version_id == v2.id and r.sop_version is not None and r.sop_version.source == "frozen"
    assert r.events[-1].detail == {"sop_version": {"id": v2.id, "version_tag": "v2.0.0", "source": "effective"}}
    with pytest.raises(BadRequestError):
        ope.pin_sop_version(db, target.id, releaser, v1.id)  # リリース後は指定できない

    # リリース後に予定が v1 の期間へ動いても、固定したバージョンは変わらない
    moved_to = datetime.combine(day_of(orders[2]), time(15), tzinfo=JST)
    plan_services.override_instance(db, target.current_instance_id, PlanInstanceOverride(scheduled_start=moved_to), actor_staff_id=releaser)
    r = ope.get_order(db, target.id)
    assert r.scheduled_start == moved_to and r.status == OrderStatus.READY
    assert r.sop_version is not None and (r.sop_version.id, r.sop_version.source) == (v2.id, "frozen")

    # 指定したバージョンがリリースで固定される
    ope.pin_sop_version(db, pinned_order.id, releaser, v1.id)
    r = ope.release_order(db, pinned_order.id, releaser)
    assert r.frozen_sop_version_id == v1.id
    assert r.events[-1].detail == {"sop_version": {"id": v1.id, "version_tag": "v1.0.0", "source": "pinned"}}


def test_release_without_any_approved_version(db, releaser):
    _, orders = business_with_orders(db)  # 承認済みのバージョンが無い業務
    target = orders[3]
    assert ope.get_order(db, target.id).sop_version is None

    r = ope.release_order(db, target.id, releaser)
    assert r.status == OrderStatus.READY and r.frozen_sop_version_id is None and r.sop_version is None
    assert r.events[-1].detail == {"sop_version": None, "sop_note": f"no SOP version effective on {day_of(target).isoformat()}"}


def test_pin_endpoints_are_guarded():
    from main import app

    paths = app.openapi()["paths"]
    for action in ("pin", "unpin"):
        op = paths[f"/ope/orders/{{order_id}}/{action}"]["post"]
        assert "x-staff-id" in {p["name"] for p in op.get("parameters", [])}
        assert {"401", "403"} <= set(op["responses"])
