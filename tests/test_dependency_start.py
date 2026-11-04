# tests/test_dependency_start.py
"""
依存関係による開始の判定（6.4）のテスト:
- hard のつながりは、上流のオーダーが完了するまで開始を止める（未完了・取消・待ち先なし）。informational は止めない
- リリース担当・業務管理者の承認（理由必須）で、その時点の満たされていないつながりを越えて開始できる（開始は作業者）
- 承認の後に新しく満たされなくなったつながりには、改めて承認が必要
- オーダーの詳細に、待つ上流のオーダー（状態・満たしているか・承認済みか）が出る

業務は「上流」「下流」の2つ（毎日の予定・同じルール上の日付で突き合わせ）。
※ テストごとに一意な顧客コードを使ってデータを分離しています。
"""
import uuid
from datetime import date, time

import pytest
from sqlalchemy import select

from src.core.exceptions import BadRequestError, DuplicatedRecordError
from src.domain_biz import services as biz_services
from src.domain_biz.models import DependencyKind, DependencyMatchRule
from src.domain_biz.schemas import BizDependencyCreate, BizMasterCreate
from src.domain_client.schemas import ClientCreate
from src.domain_ope import services as ope
from src.domain_ope.models import OpeOrder, OrderEventType, OrderStatus
from src.domain_plan import services as plan_services
from src.domain_plan.models import TemplateType
from src.domain_plan.schemas import RRuleTemplateCreate
from src.domain_staff import services as staff_services
from src.domain_staff.models import RoleCode, StaffStatus
from src.domain_staff.schemas import StaffCreate
from src.infrastructure.database import SessionLocal
from src.infrastructure.repositories import biz_master_repo, client_repo


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def staff(db):
    def make(*roles: RoleCode) -> int:
        return staff_services.create_staff(
            db, StaffCreate(login_id="q-" + uuid.uuid4().hex[:10], name="dep actor", roles=list(roles)),
            actor=None, status=StaffStatus.ACTIVE,
        ).id
    return {"releaser": make(RoleCode.RELEASER), "operator": make(RoleCode.OPERATOR), "admin": make(RoleCode.ADMIN)}


def daily(db, biz_id: int):
    return plan_services.create_template(db, RRuleTemplateCreate(
        biz_master_id=biz_id, name="Daily", template_type=TemplateType.RRULE, timezone="Asia/Tokyo",
        start_time=time(9, 0), duration_minutes=60, anchor_date=date(2026, 1, 1), rrule="FREQ=DAILY",
    ))


def setup_pair(db, kind=DependencyKind.HARD, with_upstream=True):
    code = "Q" + uuid.uuid4().hex[:3].upper()  # 他のテストの顧客とコードが衝突しないよう、別の先頭文字を使う
    client_repo.create(db, obj_in=ClientCreate(code=code, name="dep start client"))
    up = biz_master_repo.create(db, obj_in=BizMasterCreate(client_code=code, name="受領", business_seq=1, manager_email=None, description=None))
    down = biz_master_repo.create(db, obj_in=BizMasterCreate(client_code=code, name="本処理", business_seq=2, manager_email=None, description=None))
    if with_upstream:
        daily(db, up.id)
    daily(db, down.id)
    biz_services.create_dependency(db, down.id, BizDependencyCreate(
        upstream_biz_id=up.id, match_rule=DependencyMatchRule.SAME_OCCURRENCE_DATE, kind=kind,
    ))
    return up.id, down.id


def pick(db, biz_id: int, day: date) -> OpeOrder:
    db.expire_all()
    return db.scalars(select(OpeOrder).where(
        OpeOrder.biz_master_id == biz_id, OpeOrder.occurrence_date == day, OpeOrder.status != OrderStatus.CANCELED,
    )).one()


def ready_down(db, down_id: int, staff, monkeypatch) -> OpeOrder:
    """下流の3件目のオーダーをリリースし、その予定日を「今日」にする"""
    db.expire_all()
    order = db.scalars(select(OpeOrder).where(OpeOrder.biz_master_id == down_id).order_by(OpeOrder.scheduled_start).offset(2)).first()
    assert order is not None
    ope.release_order(db, order.id, staff["releaser"])
    monkeypatch.setattr(ope, "_now", lambda: order.scheduled_start)
    return order


def finish(db, order_id: int, staff) -> None:
    ope.release_order(db, order_id, staff["releaser"])
    ope.start_order(db, order_id, staff["operator"])
    ope.finish_order(db, order_id, staff["operator"])  # ステップの無いオーダー


# ==========================================
# 上流の完了を待つ
# ==========================================
def test_hard_link_blocks_until_the_upstream_finishes(db, staff, monkeypatch):
    up_id, down_id = setup_pair(db)
    down = ready_down(db, down_id, staff, monkeypatch)
    up = pick(db, up_id, down.occurrence_date)

    d = ope.get_order(db, down.id)
    (dep,) = d.dependencies
    assert (dep.upstream_order_id, dep.upstream_status, dep.met, dep.overridden, dep.kind) == (
        up.id, OrderStatus.PENDING, False, False, DependencyKind.HARD,
    )
    with pytest.raises(BadRequestError, match=f"waits for: order {up.id} of .* is pending"):
        ope.start_order(db, down.id, staff["operator"])

    finish(db, up.id, staff)
    r = ope.start_order(db, down.id, staff["operator"])
    assert r.status == OrderStatus.IN_PROGRESS and r.dependencies[0].met
    assert r.events[-1].detail is None  # 承認なしで開始（越えたつながりなし）


def test_informational_links_never_block(db, staff, monkeypatch):
    _, down_id = setup_pair(db, kind=DependencyKind.INFORMATIONAL)
    down = ready_down(db, down_id, staff, monkeypatch)
    r = ope.start_order(db, down.id, staff["operator"])
    assert r.status == OrderStatus.IN_PROGRESS and not r.dependencies[0].met


# ==========================================
# 承認して開始
# ==========================================
def test_override_lets_the_operator_start(db, staff, monkeypatch):
    _, down_id = setup_pair(db, with_upstream=False)  # 上流の予定なし → 待ち先なし
    down = ready_down(db, down_id, staff, monkeypatch)
    with pytest.raises(BadRequestError, match="no order of .* for"):
        ope.start_order(db, down.id, staff["operator"])

    r = ope.approve_dependency_override(db, down.id, staff["admin"], "上流は今月なし（顧客確認済み）")
    approval = r.events[-1]
    assert approval.event_type == OrderEventType.DEPENDENCY_OVERRIDE_APPROVED and approval.actor_staff_id == staff["admin"]
    assert approval.detail is not None and approval.detail["links"] == [r.dependencies[0].id]
    assert r.dependencies[0].overridden and not r.dependencies[0].met
    with pytest.raises(DuplicatedRecordError):
        ope.approve_dependency_override(db, down.id, staff["releaser"], "二重")

    r = ope.start_order(db, down.id, staff["operator"])
    assert r.status == OrderStatus.IN_PROGRESS
    assert r.events[-1].detail == {"dependency_override": [r.dependencies[0].id]}


def test_override_covers_only_the_links_unmet_at_approval(db, staff, monkeypatch):
    up_id, down_id = setup_pair(db)
    down = ready_down(db, down_id, staff, monkeypatch)
    up = pick(db, up_id, down.occurrence_date)
    ope.approve_dependency_override(db, down.id, staff["releaser"], "先行して開始")

    # 上流を取消して枠を戻す → 新しい上流のオーダーへ付け替え（新しいつながり）→ 改めて承認が必要
    instance_id = up.current_instance_id
    ope.cancel_order(db, up.id, staff["releaser"], "誤登録")
    plan_services.restore_cancelled_instance(db, instance_id, actor_staff_id=staff["operator"])
    new_up = pick(db, up_id, down.occurrence_date)
    d = ope.get_order(db, down.id)
    assert d.dependencies[0].upstream_order_id == new_up.id and not d.dependencies[0].overridden
    with pytest.raises(BadRequestError, match=f"order {new_up.id} of .* is pending"):
        ope.start_order(db, down.id, staff["operator"])


def test_override_needs_something_to_override(db, staff, monkeypatch):
    up_id, down_id = setup_pair(db)
    down = ready_down(db, down_id, staff, monkeypatch)
    finish(db, pick(db, up_id, down.occurrence_date).id, staff)
    with pytest.raises(BadRequestError, match="no unmet hard dependency"):
        ope.approve_dependency_override(db, down.id, staff["releaser"], "不要")
