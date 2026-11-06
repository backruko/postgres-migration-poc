# tests/test_master_data.py
"""
顧客・業務のマスタ（M1.1）のテスト: 顧客の登録・一覧（無効を含む / 含まない）・名前の変更（コードは変えられない）・
無効 / 有効（有効な業務が残る間は無効にできない。無効な顧客には業務・シートを作れず、業務を復元できない）、
業務の連番の自動採番（削除された業務の番号も使い回さない）、業務の削除の確認（依存関係・テンプレート・未完了の
オーダー）、業務の一覧の絞り込み。顧客・業務の書き込みは業務管理者のみ（ガードの目印で確認）。

※ テストごとに一意な顧客コードを使ってデータを分離しています。
"""
import uuid
from datetime import date, time

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from tests.client_codes import new_client_code
from src.core.exceptions import BadRequestError, DuplicatedRecordError, NotFoundError
from src.domain_biz import services as biz_services
from src.domain_biz.models import DependencyMatchRule
from src.domain_biz.schemas import BizDependencyCreate, BizMasterCreate
from src.domain_client import services as client_services
from src.domain_client.schemas import ClientCreate, ClientUpdate
from src.domain_ope import services as ope
from src.domain_ope.models import OpeOrder, OrderStatus
from src.domain_plan import services as plan_services
from src.domain_plan.models import TemplateType
from src.domain_plan.schemas import ReferenceSheetCreate, RRuleTemplateCreate
from src.domain_staff import services as staff_services
from src.domain_staff.models import RoleCode, StaffStatus
from src.domain_staff.schemas import StaffCreate
from src.infrastructure.database import SessionLocal


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def new_client(db, name="master client"):
    code = new_client_code("C")  # 他のテストの顧客とコードが衝突しないよう、別の先頭文字を使う
    return client_services.create_client(db, ClientCreate(code=code.lower(), name=name))


def new_biz(db, client_code, name="master biz", seq=None):
    return biz_services.create_biz(db, BizMasterCreate(client_code=client_code, name=name, business_seq=seq, manager_email=None, description=None))


def daily(db, biz_id):
    return plan_services.create_template(db, RRuleTemplateCreate.model_validate(dict(
        biz_master_id=biz_id, name="Daily", template_type=TemplateType.RRULE, timezone="Asia/Tokyo",
        start_time=time(9, 0), duration_minutes=60, anchor_date=date(2026, 1, 1), rrule="FREQ=DAILY",
    )))


def test_client_create_list_rename_and_fixed_code(db):
    client = new_client(db, name="  first name  ")
    assert client.code == client.code.upper() and client.is_active and client.name == "first name"

    with pytest.raises(DuplicatedRecordError):
        client_services.create_client(db, ClientCreate(code=client.code.lower(), name="same code"))
    for bad in ("AB", "ABCDE", "AB-1"):
        with pytest.raises(ValidationError):
            ClientCreate(code=bad, name="bad code")
    with pytest.raises(ValidationError):
        ClientUpdate.model_validate({"code": "ZZZZ"})  # コードは変えられない
    with pytest.raises(ValidationError):
        ClientUpdate(name="")

    renamed = client_services.update_client(db, client.id, ClientUpdate(name="second name"))
    assert renamed.name == "second name" and renamed.code == client.code
    with pytest.raises(NotFoundError):
        client_services.update_client(db, -1, ClientUpdate(name="nobody"))

    codes = [c.code for c in client_services.get_clients(db)]
    assert client.code in codes and codes == sorted(codes)


def test_deactivate_needs_no_active_business_and_blocks_new_data(db):
    client = new_client(db)
    biz = new_biz(db, client.code)

    with pytest.raises(BadRequestError, match="1 active business"):
        client_services.deactivate_client(db, client.id)

    biz_services.delete_biz(db, biz.id)
    inactive = client_services.deactivate_client(db, client.id)
    assert inactive.is_active is False
    with pytest.raises(BadRequestError, match="already inactive"):
        client_services.deactivate_client(db, client.id)

    # 一覧からは消え、include_inactive で戻る
    assert client.code not in [c.code for c in client_services.get_clients(db)]
    assert client.code in [c.code for c in client_services.get_clients(db, include_inactive=True)]

    # 無効な顧客には業務・シートを作れず、業務も復元できない
    with pytest.raises(BadRequestError, match="inactive"):
        new_biz(db, client.code)
    with pytest.raises(BadRequestError, match="inactive"):
        plan_services.create_sheet(db, ReferenceSheetCreate(
            client_code=client.code, name="sheet", period_start=date(2027, 1, 1), period_end=date(2027, 12, 31),
        ))
    with pytest.raises(BadRequestError, match="inactive"):
        biz_services.restore_biz(db, biz.id)

    active = client_services.activate_client(db, client.id)
    assert active.is_active is True
    with pytest.raises(BadRequestError, match="already active"):
        client_services.activate_client(db, client.id)
    assert biz_services.restore_biz(db, biz.id).is_deleted is False


def test_business_numbers_are_automatic_and_never_reused(db):
    client = new_client(db)
    first = new_biz(db, client.code, name="first")
    second = new_biz(db, client.code, name="second")
    assert (first.business_seq, first.business_code) == (1, f"{client.code}0001")
    assert second.business_seq == 2

    biz_services.delete_biz(db, second.id)
    third = new_biz(db, client.code, name="third")
    assert third.business_seq == 3  # 削除された 2 は使い回さない

    with pytest.raises(DuplicatedRecordError, match="deleted business"):
        new_biz(db, client.code, seq=2)
    with pytest.raises(DuplicatedRecordError, match="another business"):
        new_biz(db, client.code, seq=1)

    chosen = new_biz(db, client.code, name="chosen", seq=10)
    assert chosen.business_code == f"{client.code}0010"
    assert new_biz(db, client.code, name="after chosen").business_seq == 11

    with pytest.raises(ValidationError):
        BizMasterCreate(client_code=client.code, name="too big", business_seq=10000, manager_email=None, description=None)
    with pytest.raises(NotFoundError):
        new_biz(db, "ZZZ9")


def test_business_list_by_client_with_deleted(db):
    client = new_client(db)
    other = new_client(db)
    kept = new_biz(db, client.code, name="kept")
    gone = new_biz(db, client.code, name="gone")
    new_biz(db, other.code, name="other client")
    biz_services.delete_biz(db, gone.id)

    alive = biz_services.get_bizes(db, client_code=client.code.lower())
    assert [b.id for b in alive] == [kept.id]
    with_deleted = biz_services.get_bizes(db, client_code=client.code, include_deleted=True)
    assert [(b.id, b.is_deleted) for b in with_deleted] == [(kept.id, False), (gone.id, True)]


def test_business_delete_is_refused_while_in_use(db):
    client = new_client(db)
    upstream = new_biz(db, client.code, name="upstream")
    downstream = new_biz(db, client.code, name="downstream")

    # 依存関係の定義
    dep = biz_services.create_dependency(
        db, downstream_biz_id=downstream.id,
        dep_in=BizDependencyCreate(upstream_biz_id=upstream.id, match_rule=DependencyMatchRule.SAME_OCCURRENCE_DATE),
    )
    with pytest.raises(BadRequestError, match="1 dependency definition"):
        biz_services.delete_biz(db, upstream.id)
    biz_services.delete_dependency(db, dep.id)

    # テンプレート（未来の予定ごとにオーダーがある）
    template = daily(db, upstream.id)
    with pytest.raises(BadRequestError, match="1 plan template"):
        biz_services.delete_biz(db, upstream.id)

    # 1 件を保留にしてからテンプレートを削除: 未リリースのオーダーは取消、保留のものは矛盾の印つきで残る
    staff = staff_services.create_staff(
        db, StaffCreate(login_id="c-" + uuid.uuid4().hex[:10], name="master tester", roles=[RoleCode.OPERATOR]),
        actor=None, status=StaffStatus.ACTIVE,
    )
    held = db.scalars(select(OpeOrder).where(OpeOrder.template_id == template.id).order_by(OpeOrder.scheduled_start)).first()
    assert held is not None
    ope.hold_order(db, held.id, staff.id, "waiting for the client")
    plan_services.delete_template(db, template.id)
    with pytest.raises(BadRequestError, match="1 order"):
        biz_services.delete_biz(db, upstream.id)

    ope.cancel_order(db, held.id, staff.id, "business closed")
    db.expire_all()
    assert db.get(OpeOrder, held.id).status == OrderStatus.CANCELED
    assert biz_services.delete_biz(db, upstream.id).is_deleted is True
    with pytest.raises(NotFoundError):
        biz_services.delete_biz(db, upstream.id)


def test_client_writes_need_admin_and_the_list_stays_open():
    from fastapi.routing import APIRoute

    from main import app
    from src.domain_client.router import router as client_router

    writes = 0
    for route in client_router.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in (m.lower() for m in route.methods or ()):
            assert method != "delete", route.path  # 顧客は削除しない（無効にする）
            if method == "get":
                continue
            roles = getattr(route.endpoint, "__required_roles__", None)
            assert roles is not None and {r.value for r in roles} == {"admin"}, (route.path, method, roles)
            writes += 1
    assert writes == 4  # 登録・名前の変更・無効・有効

    get_op = app.openapi()["paths"]["/clients/"]["get"]
    names = {p["name"] for p in get_op.get("parameters", [])}
    assert "include_inactive" in names and "x-staff-id" not in names
