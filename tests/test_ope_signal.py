# tests/test_ope_signal.py
"""
信号（7.2: テーブルの制約とトリガーのみ。受け取り・照合は 7.3）。
- 照合の結果と対応したオーダーの整合（matched / duplicate はオーダーあり、unmatched / ambiguous はなし）
- 人の対応（handled_*）は3つそろって、照合できなかった信号にだけ、一度だけ書ける
- それ以外の変更・削除は DB トリガーが拒否する
- ロール「信号の送信」（signal_sender）がある

※ テストごとに一意な顧客コードを使ってデータを分離しています（制約違反の確認は各テストの中でロールバックする）。
"""
import uuid
from datetime import date, datetime, time, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, IntegrityError

from src.domain_biz.schemas import BizMasterCreate
from src.domain_client.schemas import ClientCreate
from src.domain_ope.models import OpeOrder, OpeSignal, SignalResult
from src.domain_plan import services as plan_services
from src.domain_plan.models import TemplateType
from src.domain_plan.schemas import RRuleTemplateCreate
from src.domain_staff import services as staff_services
from src.domain_staff.models import Role, RoleCode, StaffStatus
from src.domain_staff.schemas import StaffCreate
from src.infrastructure.database import SessionLocal
from src.infrastructure.repositories import biz_master_repo, client_repo

NOW = datetime(2026, 11, 4, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def setup(db):
    code = "H" + uuid.uuid4().hex[:3].upper()  # 他のテストの顧客とコードが衝突しないよう、別の先頭文字を使う
    client_repo.create(db, obj_in=ClientCreate(code=code, name="signal client"))
    biz = biz_master_repo.create(db, obj_in=BizMasterCreate(client_code=code, name="受領", business_seq=1, manager_email=None, description=None))
    template = plan_services.create_template(db, RRuleTemplateCreate(
        biz_master_id=biz.id, name="Daily", template_type=TemplateType.RRULE, timezone="Asia/Tokyo",
        start_time=time(9, 0), duration_minutes=60, anchor_date=date(2026, 1, 1), rrule="FREQ=DAILY",
    ))
    db.expire_all()
    order = db.scalars(select(OpeOrder).where(OpeOrder.template_id == template.id)).first()
    assert order is not None
    sender = staff_services.create_staff(
        db, StaffCreate(login_id="h-" + uuid.uuid4().hex[:10], name="sender", roles=[RoleCode.SIGNAL_SENDER]),
        actor=None, status=StaffStatus.ACTIVE,
    ).id
    return {"code": code, "order_id": order.id, "sender": sender}


def signal(setup, **overrides) -> OpeSignal:
    values = dict(
        signal_type="data_received", client_code=setup["code"], key="2026-11-10", received_at=NOW,
        sender_staff_id=setup["sender"], result=SignalResult.UNMATCHED, result_note="no order",
    )
    values.update(overrides)
    return OpeSignal(**values)


def assert_rejected(db, obj, match: str):
    db.add(obj)
    with pytest.raises(IntegrityError, match=match):
        db.flush()
    db.rollback()


def test_result_and_handled_consistency(db, setup):
    oid = setup["order_id"]
    db.add_all([
        signal(setup, result=SignalResult.MATCHED, matched_order_id=oid, result_note=None, payload={"file": "a.csv", "rows": 120}),
        signal(setup, result=SignalResult.DUPLICATE, matched_order_id=oid),
        signal(setup, client_code="ZZZZZZ"),  # 未登録の顧客コードも残す
        signal(setup, result=SignalResult.AMBIGUOUS),
    ])
    db.commit()

    assert_rejected(db, signal(setup, result=SignalResult.MATCHED), "ck_ope_signal_matched_order")
    assert_rejected(db, signal(setup, matched_order_id=oid), "ck_ope_signal_matched_order")
    assert_rejected(db, signal(setup, handled_at=NOW), "ck_ope_signal_handled")
    assert_rejected(
        db, signal(setup, result=SignalResult.MATCHED, matched_order_id=oid, handled_at=NOW,
                   handled_by_staff_id=setup["sender"], handled_note="x"),
        "ck_ope_signal_handled_unmatched",
    )


def test_only_handled_can_be_set_once(db, setup):
    row = signal(setup)
    db.add(row)
    db.commit()

    # 照合の結果などは変えられない
    row.result_note = "書き換え"
    with pytest.raises(DBAPIError, match="ope_signal is immutable"):
        db.commit()
    db.rollback()

    # 対応済みは一度だけ書ける
    row = db.get(OpeSignal, row.id)
    assert row is not None
    row.handled_at, row.handled_by_staff_id, row.handled_note = NOW, setup["sender"], "手動で完了にした"
    db.commit()
    row.handled_note = "書き直し"
    with pytest.raises(DBAPIError, match="ope_signal is immutable"):
        db.commit()
    db.rollback()

    db.delete(db.get(OpeSignal, row.id))
    with pytest.raises(DBAPIError, match="ope_signal is immutable"):
        db.commit()
    db.rollback()


def test_signal_sender_role_exists(db):
    role = db.scalars(select(Role).where(Role.code == RoleCode.SIGNAL_SENDER.value)).one()
    assert role.name == "信号の送信"
