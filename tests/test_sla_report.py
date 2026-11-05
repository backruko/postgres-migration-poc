# tests/test_sla_report.py
"""
SLA の集計（S.1）のテスト: 顧客 × 締切の月（日本時間）。
- 完了・遅れ（締切後の完了）・期限内率・平均遅れ（遅れたものだけ）・未完了の遅れ（締切を過ぎた未完了）
- 取消は数えない。月の区切りは日本時間（UTC の 1/31 15:30 は 2/1 00:30）
- 期間の指定（形式・順序・最大 36 か月）

締切・完了日時はオーダーの行に保存した値だけで計算するので、テストではそれを直接決める（2031 年の月を使い、ほかのデータと分ける）。
"""
from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import select

from src.core.exceptions import BadRequestError
from src.coordination import services as ops_services
from src.domain_biz.schemas import BizMasterCreate
from src.domain_client.schemas import ClientCreate
from src.domain_ope.models import OpeOrder, OrderStatus
from src.domain_plan import services as plan_services
from src.domain_plan.models import TemplateType
from src.domain_plan.schemas import RRuleTemplateCreate
from src.infrastructure.database import SessionLocal
from src.infrastructure.repositories import biz_master_repo, client_repo
from tests.client_codes import new_client_code

UTC = timezone.utc


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def client(db):
    code = new_client_code("U")
    client_repo.create(db, obj_in=ClientCreate(code=code, name="SLA client"))
    biz = biz_master_repo.create(db, obj_in=BizMasterCreate(client_code=code, name="sla biz", business_seq=1, manager_email=None, description=None))
    template = plan_services.create_template(db, RRuleTemplateCreate(
        biz_master_id=biz.id, name="Daily", template_type=TemplateType.RRULE, timezone="Asia/Tokyo",
        start_time=time(9, 0), duration_minutes=60, anchor_date=date(2026, 1, 1), rrule="FREQ=DAILY",
    ))
    db.expire_all()
    orders = list(db.scalars(select(OpeOrder).where(OpeOrder.template_id == template.id).order_by(OpeOrder.scheduled_start).limit(6)))
    return {"code": code, "orders": orders}


def set_order(db, order: OpeOrder, *, deadline: datetime, status: OrderStatus, finished: datetime | None = None) -> None:
    order.deadline_at = deadline
    order.warn_at = None
    order.status = status
    if status == OrderStatus.FINISHED:
        order.released_at = deadline - timedelta(days=1)
        order.finished_at = finished
    db.flush()


def test_client_month_figures(db, client, monkeypatch):
    on_time, late30, late90, open_late, canceled, feb = client["orders"]
    jan = datetime(2031, 1, 10, 3, 0, tzinfo=UTC)
    set_order(db, on_time, deadline=jan, status=OrderStatus.FINISHED, finished=jan - timedelta(minutes=10))
    set_order(db, late30, deadline=jan, status=OrderStatus.FINISHED, finished=jan + timedelta(minutes=30))
    set_order(db, late90, deadline=jan, status=OrderStatus.FINISHED, finished=jan + timedelta(minutes=90))
    set_order(db, open_late, deadline=jan, status=OrderStatus.PENDING)
    set_order(db, canceled, deadline=jan, status=OrderStatus.CANCELED)
    # UTC の 1/31 15:30 = 日本時間 2/1 00:30 → 2 月に数える
    feb_deadline = datetime(2031, 1, 31, 15, 30, tzinfo=UTC)
    set_order(db, feb, deadline=feb_deadline, status=OrderStatus.FINISHED, finished=feb_deadline - timedelta(minutes=1))
    db.commit()

    monkeypatch.setattr(ops_services, "_now", lambda: datetime(2031, 3, 1, tzinfo=UTC))  # 1 月の未完了は締切を過ぎている
    report = ops_services.get_sla_report(db, "2031-01", "2031-02", client["code"].lower())
    rows = {r.month: r for r in report.rows}
    assert (report.from_month, report.to_month) == ("2031-01", "2031-02")
    assert set(rows) == {"2031-01", "2031-02"} and rows["2031-01"].client_name == "SLA client"
    j, f = rows["2031-01"], rows["2031-02"]
    assert (j.finished, j.late, j.on_time_rate, j.avg_minutes_late, j.open_late) == (3, 2, 33.3, 60.0, 1)
    assert (f.finished, f.late, f.on_time_rate, f.avg_minutes_late, f.open_late) == (1, 0, 100.0, None, 0)

    # 未完了の締切がまだ先なら「未完了の遅れ」ではない（2031 年の締切は、今はまだ先）
    monkeypatch.setattr(ops_services, "_now", lambda: datetime(2031, 1, 1, tzinfo=UTC))
    j = {r.month: r for r in ops_services.get_sla_report(db, "2031-01", "2031-01", client["code"]).rows}["2031-01"]
    assert j.open_late == 0 and j.finished == 3


def test_period_defaults_and_validation(db, monkeypatch):
    monkeypatch.setattr(ops_services, "_now", lambda: datetime(2031, 5, 20, tzinfo=UTC))
    report = ops_services.get_sla_report(db, None, None, None)
    assert (report.from_month, report.to_month) == ("2030-06", "2031-05")  # 今月までの 12 か月
    with pytest.raises(BadRequestError, match="YYYY-MM"):
        ops_services.get_sla_report(db, "2031/01", None, None)
    with pytest.raises(BadRequestError, match="after"):
        ops_services.get_sla_report(db, "2031-03", "2031-01", None)
    with pytest.raises(BadRequestError, match="36 months"):
        ops_services.get_sla_report(db, "2027-01", "2031-01", None)


def test_sla_endpoint_needs_an_active_staff():
    from main import app

    op = app.openapi()["paths"]["/ops/sla"]["get"]
    params = {p["name"] for p in op.get("parameters", [])}
    assert {"x-staff-id", "from", "to", "client_code"} <= params and {"401", "403"} <= set(op["responses"])
