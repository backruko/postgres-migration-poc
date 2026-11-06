# tests/test_client_events.py
"""
顧客側の出来事の設定（7.1）のテスト: 業務の execution_type / completion_mode / signal_type。
- 既定は SOP・MANUAL・信号なし。PATCH で LIGHTWEIGHT・AUTOMATIC・信号の種類を設定できる
- 信号での完了は LIGHTWEIGHT のみ・信号の種類が必須（MANUAL では持たない）・同じ顧客の業務どうしで一意
- LIGHTWEIGHT の業務のオーダーは、SOP バージョンにステップがあってもステップなし

※ テストごとに一意な顧客コードを使ってデータを分離しています。
"""
import uuid
from datetime import date, datetime, time, timedelta, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from tests.client_codes import new_client_code
from src.core.exceptions import BadRequestError, DuplicatedRecordError
from src.domain_biz import services as biz_services
from src.domain_biz.models import BizMaster, CompletionMode, ExecutionType, SOPStatus, SOPStepTemplate, SOPVersion
from src.domain_biz.schemas import BizMasterCreate, BizMasterUpdate
from src.domain_client.schemas import ClientCreate
from src.domain_ope import services as ope
from src.domain_ope.models import OpeOrder
from src.domain_plan import services as plan_services
from src.domain_plan.models import TemplateType
from src.domain_plan.schemas import RRuleTemplateCreate
from src.domain_staff import services as staff_services
from src.domain_staff.models import RoleCode, StaffStatus
from src.domain_staff.schemas import StaffCreate
from src.infrastructure.database import SessionLocal
from src.infrastructure.repositories import biz_master_repo, client_repo

JST = timezone(timedelta(hours=9))
LIGHT, SOP = ExecutionType.LIGHTWEIGHT, ExecutionType.SOP
AUTO, MANUAL = CompletionMode.AUTOMATIC, CompletionMode.MANUAL


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def client_with_businesses(db, count: int) -> tuple[str, list[int]]:
    code = new_client_code("E")  # 他のテストの顧客とコードが衝突しないよう、別の先頭文字を使う
    client_repo.create(db, obj_in=ClientCreate(code=code, name="client event client"))
    ids = [
        biz_master_repo.create(db, obj_in=BizMasterCreate(client_code=code, name=f"biz {n}", business_seq=n, manager_email=None, description=None)).id
        for n in range(1, count + 1)
    ]
    return code, ids


def patch(db, biz_id: int, **values) -> BizMaster:
    return biz_services.update_biz(db, biz_id, BizMasterUpdate(**values))


def test_defaults_and_setting_a_client_event(db):
    _, (biz_id,) = client_with_businesses(db, 1)
    biz = biz_services.get_biz(db, biz_id)
    assert (biz.execution_type, biz.completion_mode, biz.signal_type) == (SOP, MANUAL, None)

    biz = patch(db, biz_id, execution_type=LIGHT, completion_mode=AUTO, signal_type="  data_received  ")
    assert (biz.execution_type, biz.completion_mode, biz.signal_type) == (LIGHT, AUTO, "data_received")
    # 手動に戻すときは信号の種類も外す
    biz = patch(db, biz_id, completion_mode=MANUAL, signal_type=None)
    assert (biz.execution_type, biz.completion_mode, biz.signal_type) == (LIGHT, MANUAL, None)


def test_refused_combinations(db):
    code, (a, b) = client_with_businesses(db, 2)
    _, (other,) = client_with_businesses(db, 1)

    with pytest.raises(BadRequestError, match="only for LIGHTWEIGHT"):
        patch(db, a, completion_mode=AUTO, signal_type="x")
    with pytest.raises(BadRequestError, match="required"):
        patch(db, a, execution_type=LIGHT, completion_mode=AUTO)
    with pytest.raises(BadRequestError, match="only for automatic"):
        patch(db, a, execution_type=LIGHT, signal_type="x")
    with pytest.raises(ValidationError):
        BizMasterUpdate.model_validate({"execution_type": None})  # 明示的な null は受け付けない（signal_type は null で外せる）

    patch(db, a, execution_type=LIGHT, completion_mode=AUTO, signal_type="data_received")
    with pytest.raises(DuplicatedRecordError, match="already used"):
        patch(db, b, execution_type=LIGHT, completion_mode=AUTO, signal_type="data_received")
    # 別の顧客なら同じ信号の種類を使える
    assert patch(db, other, execution_type=LIGHT, completion_mode=AUTO, signal_type="data_received").signal_type == "data_received"
    # SOP に戻すには、先に信号での完了をやめる
    with pytest.raises(BadRequestError, match="only for LIGHTWEIGHT"):
        patch(db, a, execution_type=SOP)


def test_db_backs_up_the_rules(db):
    _, (a, b) = client_with_businesses(db, 2)
    for values, constraint in (
        (dict(completion_mode=AUTO, signal_type="x"), "chk_biz_automatic_completion_lightweight"),
        (dict(execution_type=LIGHT, completion_mode=AUTO), "chk_biz_signal_type"),
    ):
        biz = db.get(BizMaster, a)
        for k, v in values.items():
            setattr(biz, k, v)
        with pytest.raises(IntegrityError, match=constraint):
            db.flush()
        db.rollback()
    for biz_id in (a, b):
        biz = db.get(BizMaster, biz_id)
        biz.execution_type, biz.completion_mode, biz.signal_type = LIGHT, AUTO, "same"  # type: ignore[union-attr]
    with pytest.raises(IntegrityError, match="uq_biz_client_signal_type_alive"):
        db.flush()
    db.rollback()


def test_lightweight_orders_get_no_steps(db):
    _, (biz_id,) = client_with_businesses(db, 1)
    version = SOPVersion(biz_master_id=biz_id, version_number=1, version_tag="v1.0.0", status=SOPStatus.APPROVED,
                         effective_at=datetime(2026, 1, 1, tzinfo=JST))
    db.add(version)
    db.flush()
    db.add(SOPStepTemplate(sop_version_id=version.id, step_num=1, title="手順1", content=None))
    db.commit()
    patch(db, biz_id, execution_type=LIGHT)
    template = plan_services.create_template(db, RRuleTemplateCreate(
        biz_master_id=biz_id, name="Data received", template_type=TemplateType.RRULE, timezone="Asia/Tokyo",
        start_time=time(9, 0), duration_minutes=60, anchor_date=date(2026, 1, 1), rrule="FREQ=DAILY",
    ))
    db.expire_all()
    order = db.scalars(select(OpeOrder).where(OpeOrder.template_id == template.id).order_by(OpeOrder.scheduled_start)).first()
    assert order is not None
    assert ope.get_order(db, order.id).steps == []  # プレビューも空
    releaser = staff_services.create_staff(
        db, StaffCreate(login_id="e-" + uuid.uuid4().hex[:10], name="releaser", roles=[RoleCode.RELEASER]),
        actor=None, status=StaffStatus.ACTIVE,
    ).id
    r = ope.release_order(db, order.id, releaser)
    assert r.steps == [] and r.events[-1].detail is not None and r.events[-1].detail["steps"] == {"created": [], "skipped": []}
