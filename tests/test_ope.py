# tests/test_ope.py
"""
domain_ope のテスト（4.2: テーブルの制約のみ。オーダーを作る処理は 4.3 以降）。
- 1つの発生枠に1件のオーダー（plan の _slot() と同じ規則: シート日付の行 / 臨時の最初のインスタンス / ルール上の日付）
- 状態・保留・矛盾・リリース日時の整合
- オーダーの記録は追記のみ（DB トリガーで UPDATE / DELETE を拒否）

※ テストごとに一意な顧客コードを使ってデータを分離しています（制約違反の確認は各テストの中でロールバックする）。
"""
import uuid
from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError

from src.domain_biz.schemas import BizMasterCreate
from src.domain_client.schemas import ClientCreate
from src.domain_ope.models import EventSource, OpeOrder, OpeOrderEvent, OrderEventType, OrderStatus
from src.domain_plan import services as plan_services
from src.domain_plan import slot_events
from src.domain_plan.models import TemplateType
from src.domain_plan.schemas import PlanOneOffCreate, RRuleTemplateCreate
from src.domain_staff import services as staff_services
from src.domain_staff.models import RoleCode, StaffStatus
from src.domain_staff.schemas import StaffCreate
from src.infrastructure.database import SessionLocal
from src.infrastructure.repositories import biz_master_repo, client_repo

JST = timezone(timedelta(hours=9))


# ==========================================
# Fixtures / Helpers
# ==========================================
@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def plan(db, monkeypatch):
    """
    顧客・業務・毎日のテンプレートと、その生成済みインスタンス。
    ここではテーブルの制約だけを確かめるため、plan → オーダーの自動追従（4.3）を止めてオーダーを直接作る。
    """
    monkeypatch.setattr(slot_events, "_listeners", [])
    # test_plan.py ("T") / test_ops.py ("D") / test_biz.py ("B") の顧客とコードが衝突しないよう、別の先頭文字を使う
    code = "O" + uuid.uuid4().hex[:3].upper()
    client_repo.create(db, obj_in=ClientCreate(code=code, name="ope test client"))
    biz = biz_master_repo.create(db, obj_in=BizMasterCreate(client_code=code, name="ope biz", business_seq=1, manager_email=None, description=None))
    template = plan_services.create_template(db, RRuleTemplateCreate(
        biz_master_id=biz.id, name="Daily", template_type=TemplateType.RRULE, timezone="Asia/Tokyo",
        start_time=time(9, 0), duration_minutes=60, anchor_date=date(2026, 1, 1), rrule="FREQ=DAILY",
    ))
    instances = sorted(
        (i for i in plan_services.get_instances(db, template.id, limit=1000) if not i.voided), key=lambda i: i.scheduled_start
    )
    return {"code": code, "biz_id": biz.id, "template_id": template.id, "instances": instances}


def order_for(plan, instance, **overrides) -> OpeOrder:
    """plan インスタンスからオーダーを作る（4.3 の処理の代わりに、テストでは直接作る）"""
    values = dict(
        template_id=plan["template_id"], occurrence_date=instance.occurrence_date,
        source_sheet_event_id=None, one_off_instance_id=None,
        current_instance_id=instance.id, biz_master_id=plan["biz_id"], client_code=plan["code"],
        scheduled_start=instance.scheduled_start, scheduled_end=instance.scheduled_end,
    )
    values.update(overrides)
    return OpeOrder(**values)


def assert_rejected(db, obj, match: str):
    """追加すると制約違反になることを確認する（確認後はロールバックしてセッションを使える状態に戻す）"""
    db.add(obj)
    with pytest.raises(IntegrityError, match=match):
        db.flush()
    db.rollback()


def make_staff(db):
    return staff_services.create_staff(
        db, StaffCreate(login_id="o-" + uuid.uuid4().hex[:10], name="ope tester", roles=[RoleCode.OPERATOR]),
        actor=None, status=StaffStatus.ACTIVE,
    )


# ==========================================
# 発生枠ごとに1件
# ==========================================
def test_one_order_per_rule_date_slot(db, plan):
    first, second = plan["instances"][2], plan["instances"][3]
    db.add(order_for(plan, first))
    db.add(order_for(plan, second))
    db.commit()

    # 同じテンプレート・同じルール上の日付のオーダーは2件作れない（インスタンスが置き換わっても枠は同じ）
    assert_rejected(db, order_for(plan, first, current_instance_id=second.id), "uq_ope_order_date_slot")

    # 取消されたオーダーは一意の対象外: 同じ枠が戻ったら新しいオーダーを作れる（取消済みは履歴として残る）
    third = plan["instances"][15]
    db.add(order_for(plan, third, status=OrderStatus.CANCELED))
    db.add(order_for(plan, third))
    db.commit()


def test_sheet_slots_are_keyed_by_the_sheet_date_row(db, plan):
    inst = plan["instances"][4]
    # シート由来の枠は、ルール上の日付が同じでもシート日付の行が違えば別の枠（ルール上の日付の枠とも重ならない）
    db.add(order_for(plan, inst))
    db.add(order_for(plan, inst, source_sheet_event_id=901))
    db.add(order_for(plan, inst, source_sheet_event_id=902))
    db.commit()

    # 同じシート日付の行は、日付が動いて occurrence_date が変わっても同じ枠
    assert_rejected(
        db, order_for(plan, inst, source_sheet_event_id=901, occurrence_date=inst.occurrence_date + timedelta(days=1)),
        "uq_ope_order_sheet_slot",
    )


def test_one_off_slots_are_keyed_by_their_first_instance(db, plan):
    rule_inst = plan["instances"][5]
    start = rule_inst.scheduled_start + timedelta(hours=3)
    one_offs = [
        plan_services.add_one_off_instance(db, plan["template_id"], PlanOneOffCreate(scheduled_start=start + timedelta(hours=h)))
        for h in (0, 1)
    ]
    # 臨時の予定は、ルールの予定と同じ日・同じ日に2件でも、それぞれ別の枠
    db.add(order_for(plan, rule_inst))
    for oo in one_offs:
        db.add(order_for(plan, oo, one_off_instance_id=oo.id))
    db.commit()

    assert_rejected(db, order_for(plan, one_offs[0], one_off_instance_id=one_offs[0].id), "uq_ope_order_one_off_slot")
    # シート日付と臨時の両方を持つ枠は無い
    assert_rejected(
        db, order_for(plan, one_offs[1], one_off_instance_id=one_offs[1].id, source_sheet_event_id=903),
        "ck_ope_order_one_slot_kind",
    )


# ==========================================
# 状態の整合
# ==========================================
def test_order_status_consistency_checks(db, plan):
    inst = plan["instances"][6]
    order = order_for(plan, inst)
    db.add(order)
    db.commit()
    assert order.status == OrderStatus.PENDING and not order.conflict and order.released_at is None

    bad = {
        "ck_ope_order_hold": dict(status=OrderStatus.ON_HOLD),  # 保留なのに保留前の状態が無い
        "ck_ope_order_hold_from": dict(status=OrderStatus.ON_HOLD, status_before_hold=OrderStatus.FINISHED),
        "ck_ope_order_released_at": dict(status=OrderStatus.READY),  # リリース日時の無い ready
        "ck_ope_order_conflict_reason": dict(conflict=True),
        "ck_ope_order_period": dict(scheduled_end=inst.scheduled_start),
    }
    for i, (constraint, values) in enumerate(bad.items(), start=7):
        assert_rejected(db, order_for(plan, plan["instances"][i], **values), constraint)

    # 正しい組み合わせは通る（保留前が ready の保留・リリース済み・理由付きの矛盾）
    ok = order_for(
        plan, plan["instances"][12], status=OrderStatus.ON_HOLD, status_before_hold=OrderStatus.READY,
        released_at=datetime.now(JST), conflict=True, conflict_reason="作業中に日時が変わりました",
    )
    db.add(ok)
    db.commit()


# ==========================================
# オーダーの記録（追記のみ）
# ==========================================
def test_order_events_are_append_only(db, plan):
    staff = make_staff(db)
    order = order_for(plan, plan["instances"][13])
    db.add(order)
    db.flush()
    created = OpeOrderEvent(order_id=order.id, event_type=OrderEventType.CREATED, to_status=OrderStatus.PENDING, source=EventSource.SYSTEM)
    held = OpeOrderEvent(
        order_id=order.id, event_type=OrderEventType.STATUS_CHANGED, from_status=OrderStatus.PENDING,
        to_status=OrderStatus.ON_HOLD, source=EventSource.HUMAN, actor_staff_id=staff.id, reason="顧客確認待ち",
        detail={"note": "free form"},
    )
    db.add_all([created, held])
    db.commit()
    assert created.occurred_at is not None and held.detail == {"note": "free form"}

    # 記録は変更も削除もできない（DB トリガー）
    held.reason = "書き換え"
    with pytest.raises(DBAPIError, match="ope_order_event is immutable"):
        db.commit()
    db.rollback()
    db.delete(created)
    with pytest.raises(DBAPIError, match="ope_order_event is immutable"):
        db.commit()
    db.rollback()

    # 記録のあるオーダーは物理削除できない
    db.delete(db.get(OpeOrder, order.id))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_order_event_checks(db, plan):
    staff = make_staff(db)
    order = order_for(plan, plan["instances"][14])
    db.add(order)
    db.commit()

    def event(**values):
        return OpeOrderEvent(order_id=order.id, **values)

    # 人の操作は職員が必須・システムの処理は職員なし
    assert_rejected(db, event(event_type=OrderEventType.PINNED, source=EventSource.HUMAN), "ck_ope_order_event_actor")
    assert_rejected(
        db, event(event_type=OrderEventType.RESCHEDULED, source=EventSource.SYSTEM, actor_staff_id=staff.id),
        "ck_ope_order_event_actor",
    )
    # 状態の変更は前後の状態が必須・作成は作成時の状態が必須
    assert_rejected(
        db, event(event_type=OrderEventType.STATUS_CHANGED, to_status=OrderStatus.READY, source=EventSource.SYSTEM),
        "ck_ope_order_event_status_change",
    )
    assert_rejected(db, event(event_type=OrderEventType.CREATED, source=EventSource.SYSTEM), "ck_ope_order_event_created")
