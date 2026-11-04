# tests/test_order_links.py
"""
オーダー同士のつながり（6.3）のテスト: 業務間の定義（biz_dependency）から ope_dependency を作る・直す。
- 定義の作成で、下流の業務の開始前のオーダーにつながりができる。削除で「外れた」が付く（行は残る）
- 上流のオーダーが無ければ待ち先なしの行。上流のオーダーができたら、その行が埋まる
- 上流が取消されたら取消済みのオーダーを指したまま。枠が戻って新しいオーダーができたら付け替える
- 下流のオーダーにキーが無い（シート由来でない）ときは、シート日付の定義は当てはまらない
- 開始後の下流のオーダーのつながりは直さない

業務は「上流」「下流」の2つ。plan → オーダーの追従（受け手）は conftest.py で登録済み。
※ テストごとに一意な顧客コードを使ってデータを分離しています。
"""
import json
import uuid
from datetime import date, time
from pathlib import Path

import pytest
from sqlalchemy import select

from src.domain_biz import services as biz_services
from src.domain_biz.models import DependencyKind, DependencyMatchRule
from src.domain_biz.schemas import BizDependencyCreate, BizMasterCreate
from src.domain_client.schemas import ClientCreate
from src.domain_ope import links
from src.domain_ope import services as ope
from src.domain_ope.models import OpeDependency, OpeOrder, OrderStatus
from src.domain_plan import services as plan_services
from src.domain_plan.models import TemplateType
from src.domain_plan.schemas import ReferenceSheetImport, ReferenceSheetTemplateCreate, RRuleTemplateCreate
from src.domain_staff import services as staff_services
from src.domain_staff.models import RoleCode, StaffStatus
from src.domain_staff.schemas import StaffCreate
from src.infrastructure.database import SessionLocal
from src.infrastructure.repositories import biz_master_repo, client_repo

SHEET, DATE = DependencyMatchRule.SAME_SHEET_EVENT, DependencyMatchRule.SAME_OCCURRENCE_DATE
MOCK_SHEET_PATH = Path(__file__).resolve().parent.parent / "mock_data" / "desensitized_mock_data.json"


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def bizes(db):
    code = "N" + uuid.uuid4().hex[:3].upper()  # 他のテストの顧客とコードが衝突しないよう、別の先頭文字を使う
    client_repo.create(db, obj_in=ClientCreate(code=code, name="link client"))
    up = biz_master_repo.create(db, obj_in=BizMasterCreate(client_code=code, name="受領", business_seq=1, manager_email=None, description=None))
    down = biz_master_repo.create(db, obj_in=BizMasterCreate(client_code=code, name="本処理", business_seq=2, manager_email=None, description=None))
    return {"code": code, "up": up.id, "down": down.id}


@pytest.fixture
def staff(db):
    def make(*roles: RoleCode) -> int:
        return staff_services.create_staff(
            db, StaffCreate(login_id="n-" + uuid.uuid4().hex[:10], name="link actor", roles=list(roles)),
            actor=None, status=StaffStatus.ACTIVE,
        ).id
    return {"releaser": make(RoleCode.RELEASER), "operator": make(RoleCode.OPERATOR)}


def daily(db, biz_id: int):
    return plan_services.create_template(db, RRuleTemplateCreate(
        biz_master_id=biz_id, name="Daily", template_type=TemplateType.RRULE, timezone="Asia/Tokyo",
        start_time=time(9, 0), duration_minutes=60, anchor_date=date(2026, 1, 1), rrule="FREQ=DAILY",
    ))


def define(db, bizes, rule=DATE, kind=DependencyKind.HARD):
    return biz_services.create_dependency(db, bizes["down"], BizDependencyCreate(upstream_biz_id=bizes["up"], match_rule=rule, kind=kind))


def orders_of(db, biz_id: int) -> list[OpeOrder]:
    db.expire_all()
    return list(db.scalars(select(OpeOrder).where(OpeOrder.biz_master_id == biz_id).order_by(OpeOrder.scheduled_start, OpeOrder.id)))


def active_links(db, order_id: int) -> list[OpeDependency]:
    return list(db.scalars(select(OpeDependency).where(
        OpeDependency.downstream_order_id == order_id, OpeDependency.removed_at.is_(None),
    ).order_by(OpeDependency.id)))


def all_links(db, order_id: int) -> list[OpeDependency]:
    return list(db.scalars(select(OpeDependency).where(OpeDependency.downstream_order_id == order_id).order_by(OpeDependency.id)))


def by_date(orders: list[OpeOrder]) -> dict[date, OpeOrder]:
    return {o.occurrence_date: o for o in orders if o.status != OrderStatus.CANCELED}


# ==========================================
# 定義の作成・削除
# ==========================================
def test_definition_links_existing_orders_and_deleting_it_marks_them_removed(db, bizes):
    daily(db, bizes["up"])
    daily(db, bizes["down"])
    dep = define(db, bizes)

    ups = by_date(orders_of(db, bizes["up"]))
    downs = orders_of(db, bizes["down"])
    assert downs
    for d in downs:
        (row,) = active_links(db, d.id)
        assert (row.upstream_order_id, row.kind, row.match_rule, row.match_date, row.biz_dependency_id) == (
            ups[d.occurrence_date].id, DependencyKind.HARD, DATE, d.occurrence_date, dep.id,
        )

    biz_services.delete_dependency(db, dep.id)
    db.expire_all()
    for d in downs:
        assert active_links(db, d.id) == []
        (row,) = all_links(db, d.id)
        assert row.removed_reason == links.REASON_DEFINITION_DELETED and row.biz_dependency_id is None


def test_waiting_rows_are_filled_when_the_upstream_orders_appear(db, bizes):
    define(db, bizes)
    daily(db, bizes["down"])  # 上流の予定がまだ無い → 待ち先なし
    downs = orders_of(db, bizes["down"])
    waiting = {d.id: active_links(db, d.id) for d in downs}
    assert all(len(rows) == 1 and rows[0].upstream_order_id is None for rows in waiting.values())

    daily(db, bizes["up"])  # 上流のオーダーができる → 同じ行が埋まる
    ups = by_date(orders_of(db, bizes["up"]))
    for d in downs:
        (row,) = active_links(db, d.id)
        assert row.id == waiting[d.id][0].id and row.upstream_order_id == ups[d.occurrence_date].id


# ==========================================
# 上流の取消・枠が戻る
# ==========================================
def test_canceled_upstream_stays_linked_until_the_slot_returns(db, bizes, staff):
    daily(db, bizes["up"])
    daily(db, bizes["down"])
    define(db, bizes)
    up = orders_of(db, bizes["up"])[2]
    down = by_date(orders_of(db, bizes["down"]))[up.occurrence_date]
    instance_id = up.current_instance_id

    # 上流を取消（plan の予定も取消）→ 下流は取消済みのオーダーを待ったまま
    ope.cancel_order(db, up.id, staff["releaser"], "今月はデータなし")
    db.expire_all()
    (row,) = active_links(db, down.id)
    assert row.upstream_order_id == up.id

    # 予定を元に戻す → 新しい上流のオーダー → つながりを付け替える（古い行は理由付きで残る）
    plan_services.restore_cancelled_instance(db, instance_id, actor_staff_id=staff["operator"])
    new_up = by_date(orders_of(db, bizes["up"]))[up.occurrence_date]
    assert new_up.id != up.id
    (row,) = active_links(db, down.id)
    assert row.upstream_order_id == new_up.id
    old = [r for r in all_links(db, down.id) if r.removed_at is not None]
    assert len(old) == 1 and old[0].upstream_order_id == up.id and old[0].removed_reason == links.REASON_UPSTREAM_CHANGED


# ==========================================
# シート日付の突き合わせ・当てはまらない場合
# ==========================================
def test_sheet_rule_matches_the_same_sheet_event_and_skips_orders_without_one(db, bizes):
    data = json.loads(MOCK_SHEET_PATH.read_text(encoding="utf-8"))
    data["client_code"] = bizes["code"]
    sheet = plan_services.import_sheet(db, ReferenceSheetImport.model_validate(data)).sheet
    for biz_id, offset in ((bizes["up"], 0), (bizes["down"], 1)):
        plan_services.create_template(db, ReferenceSheetTemplateCreate(
            biz_master_id=biz_id, name="本締", template_type=TemplateType.REFERENCE_SHEET_BASED,
            start_time=time(9, 0), duration_minutes=60, reference_sheet_id=sheet.id, reference_event_marker="本締",
            offset_days=offset, offset_minutes=0,
        ))
    daily(db, bizes["down"])  # シート由来でない下流のオーダー
    define(db, bizes, rule=SHEET)

    ups = {o.source_sheet_event_id: o for o in orders_of(db, bizes["up"])}
    downs = orders_of(db, bizes["down"])
    sheet_downs = [d for d in downs if d.source_sheet_event_id is not None]
    assert sheet_downs
    for d in sheet_downs:
        (row,) = active_links(db, d.id)
        assert row.upstream_order_id == ups[d.source_sheet_event_id].id and row.match_sheet_event_id == d.source_sheet_event_id
    # シート日付の無い下流のオーダーには、この定義は当てはまらない
    assert all(active_links(db, d.id) == [] for d in downs if d.source_sheet_event_id is None)


# ==========================================
# 開始後は直さない
# ==========================================
def test_started_downstream_orders_keep_their_links(db, bizes, staff, monkeypatch):
    define(db, bizes)
    daily(db, bizes["down"])
    started, pending = orders_of(db, bizes["down"])[1:3]
    ope.release_order(db, started.id, staff["releaser"])
    monkeypatch.setattr(ope, "_now", lambda: started.scheduled_start)
    # 待ち先なし（hard）のままでは開始できない（6.4）→ リリース担当の承認で開始
    ope.approve_dependency_override(db, started.id, staff["releaser"], "上流の予定がまだ無い")
    ope.start_order(db, started.id, staff["operator"])

    daily(db, bizes["up"])
    (row,) = active_links(db, started.id)
    assert row.upstream_order_id is None  # 開始後は待ち先なしのまま
    (row,) = active_links(db, pending.id)
    assert row.upstream_order_id is not None
