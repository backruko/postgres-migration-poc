# tests/test_sop_impact.py
"""
SOP バージョンの影響（M2.3: GET /ope/sop-impact）のテスト: その日の 0:00（日本時間）以降に予定されたオーダーを、
新しい版を使うもの（未リリース・指定なし）/ 指定した版のまま / リリースで固定した版のまま に分ける。
取消・完了と、その日より前のオーダーは含まない。件数は正確で、一覧は早い順に limit 件まで。

※ テストごとに一意な顧客コードを使ってデータを分離しています。
"""
from tests.openapi_auth import needs_login
import uuid
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from tests.client_codes import new_client_code
from src.core.exceptions import NotFoundError
from src.domain_biz import services as biz_services
from src.domain_biz.models import SOPStatus, SOPVersion
from src.domain_biz.schemas import BizMasterCreate
from src.domain_client import services as client_services
from src.domain_client.schemas import ClientCreate
from src.domain_ope import services as ope
from src.domain_ope import sop_impact
from src.domain_ope.models import OpeOrder, OrderStatus
from src.domain_plan import services as plan_services
from src.domain_plan.models import TemplateType
from src.domain_plan.schemas import RRuleTemplateCreate
from src.domain_staff import services as staff_services
from src.domain_staff.models import RoleCode, StaffStatus
from src.domain_staff.schemas import StaffCreate
from src.infrastructure.database import SessionLocal

JST = ZoneInfo("Asia/Tokyo")


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def test_impact_splits_upcoming_orders_by_what_decides_their_version(db):
    code = new_client_code("Z")  # 他のテストの顧客とコードが衝突しないよう、別の先頭文字を使う
    client_services.create_client(db, ClientCreate(code=code, name="impact client"))
    biz = biz_services.create_biz(db, BizMasterCreate(client_code=code, name="impact biz", business_seq=None, manager_email=None, description=None))
    today = datetime.now(timezone.utc).astimezone(JST).date()
    current = SOPVersion(biz_master_id=biz.id, version_number=1, version_tag="v1.0.0", status=SOPStatus.APPROVED,
                         effective_at=datetime.combine(today - timedelta(days=30), time(0), tzinfo=JST))
    db.add(current)
    db.commit()
    template = plan_services.create_template(db, RRuleTemplateCreate.model_validate(dict(
        biz_master_id=biz.id, name="毎日の照合", template_type=TemplateType.RRULE, timezone="Asia/Tokyo",
        start_time=time(9, 0), duration_minutes=60, anchor_date=date(2026, 1, 1), rrule="FREQ=DAILY",
    )))
    staff = staff_services.create_staff(
        db, StaffCreate(login_id="z-" + uuid.uuid4().hex[:10], name="impact tester", roles=[RoleCode.RELEASER]),
        actor=None, status=StaffStatus.ACTIVE,
    )

    effective = today + timedelta(days=5)
    start = datetime.combine(effective, time(0), tzinfo=JST)
    orders = list(db.scalars(select(OpeOrder).where(OpeOrder.template_id == template.id).order_by(OpeOrder.scheduled_start)))
    after = [o for o in orders if o.scheduled_start >= start]
    assert len(after) > 10 and any(o.scheduled_start < start for o in orders)

    pinned, released, canceled = after[1], after[2], after[3]
    ope.pin_sop_version(db, pinned.id, staff.id, current.id)
    ope.release_order(db, released.id, staff.id)
    ope.cancel_order(db, canceled.id, staff.id, "not needed")

    r = sop_impact.sop_impact(db, biz.id, effective, limit=3)
    assert r.effective_at == start
    assert r.pinned.total == 1 and [(o.id, o.pinned_sop_version_tag) for o in r.pinned.orders] == [(pinned.id, "v1.0.0")]
    assert r.released.total == 1 and r.released.orders[0].id == released.id
    assert r.released.orders[0].frozen_sop_version_tag == "v1.0.0" and r.released.orders[0].status == OrderStatus.READY
    assert r.will_switch.total == len(after) - 3
    assert [o.id for o in r.will_switch.orders] == [after[0].id, after[4].id, after[5].id]  # 早い順に limit 件
    assert r.will_switch.orders[0].template_name == "毎日の照合" and r.will_switch.orders[0].scheduled_start >= start

    # 一日遅らせると、その日のオーダーが外れる
    later = sop_impact.sop_impact(db, biz.id, effective + timedelta(days=1))
    assert later.will_switch.total == r.will_switch.total - 1

    with pytest.raises(NotFoundError):
        sop_impact.sop_impact(db, -1, effective)


def test_impact_endpoint_needs_an_active_staff():
    from main import app

    op = app.openapi()["paths"]["/ope/sop-impact"]["get"]
    names = {p["name"] for p in op["parameters"]}
    assert {"biz_id", "effective_date", "limit"} <= names and needs_login(op)
