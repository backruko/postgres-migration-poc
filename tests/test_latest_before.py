# tests/test_latest_before.py
"""
直前の上流（latest_before、L.2）のテスト: 周期の違う業務どうしのつながり。
- 下流のオーダーは、予定開始以前で lookback_days 日前までに開始する上流のオーダーのうち最新の1件を待つ（無ければ待ち先なし）
- 取消された上流でも最新ならそれを待つ（前の回に戻らない）
- 上流・下流が動いたら（人が日時を変えた）、つながりを直す

上流 = 毎週月曜 9:00、下流 = 毎日 10:00。日付は今日から計算するので、実行する日によらない。
plan → オーダーの追従（受け手）は conftest.py で登録済み。※ テストごとに一意な顧客コードを使ってデータを分離しています。
"""
import uuid
from datetime import date, datetime, time, timedelta

import pytest
from sqlalchemy import select

from src.domain_biz import services as biz_services
from src.domain_biz.models import DependencyMatchRule
from src.domain_biz.schemas import BizDependencyCreate, BizMasterCreate
from src.domain_client.schemas import ClientCreate
from src.domain_ope import links
from src.domain_ope import services as ope
from src.domain_ope.models import OpeDependency, OpeOrder, OrderStatus
from src.domain_plan import services as plan_services
from src.domain_plan.models import TemplateType
from src.domain_plan.schemas import PlanInstanceOverride, RRuleTemplateCreate
from src.domain_staff import services as staff_services
from src.domain_staff.models import RoleCode, StaffStatus
from src.domain_staff.schemas import StaffCreate
from src.infrastructure.database import SessionLocal
from src.infrastructure.repositories import biz_master_repo, client_repo

LATEST = DependencyMatchRule.LATEST_BEFORE


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
    code = "W" + uuid.uuid4().hex[:3].upper()  # 他のテストの顧客とコードが衝突しないよう、別の先頭文字を使う
    client_repo.create(db, obj_in=ClientCreate(code=code, name="latest client"))
    up = biz_master_repo.create(db, obj_in=BizMasterCreate(client_code=code, name="週次の受領", business_seq=1, manager_email=None, description=None))
    down = biz_master_repo.create(db, obj_in=BizMasterCreate(client_code=code, name="毎日の処理", business_seq=2, manager_email=None, description=None))
    return {"up": up.id, "down": down.id}


@pytest.fixture
def staff(db):
    def make(*roles: RoleCode) -> int:
        return staff_services.create_staff(
            db, StaffCreate(login_id="w-" + uuid.uuid4().hex[:10], name="latest actor", roles=list(roles)),
            actor=None, status=StaffStatus.ACTIVE,
        ).id
    return {"releaser": make(RoleCode.RELEASER)}


def template(db, biz_id: int, rrule: str, start: time):
    return plan_services.create_template(db, RRuleTemplateCreate(
        biz_master_id=biz_id, name=rrule, template_type=TemplateType.RRULE, timezone="Asia/Tokyo",
        start_time=start, duration_minutes=30, anchor_date=date(2026, 1, 1), rrule=rrule,
    ))


def setup(db, bizes, lookback: int):
    template(db, bizes["up"], "FREQ=WEEKLY;BYDAY=MO", time(9, 0))
    template(db, bizes["down"], "FREQ=DAILY", time(10, 0))
    return biz_services.create_dependency(db, bizes["down"], BizDependencyCreate(
        upstream_biz_id=bizes["up"], match_rule=LATEST, lookback_days=lookback,
    ))


def orders_of(db, biz_id: int) -> list[OpeOrder]:
    db.expire_all()
    return list(db.scalars(select(OpeOrder).where(OpeOrder.biz_master_id == biz_id).order_by(OpeOrder.scheduled_start, OpeOrder.id)))


def active_links(db, order_id: int) -> list[OpeDependency]:
    return list(db.scalars(select(OpeDependency).where(
        OpeDependency.downstream_order_id == order_id, OpeDependency.removed_at.is_(None),
    ).order_by(OpeDependency.id)))


def expected(ups: list[OpeOrder], at: datetime, lookback: int) -> OpeOrder | None:
    """直前の上流: at 以前・lookback 日前まで、最新（同じ時刻なら新しいオーダー）。取消済みも含む"""
    window = [u for u in ups if at - timedelta(days=lookback) <= u.scheduled_start <= at]
    return max(window, key=lambda u: (u.scheduled_start, u.id), default=None)


def check_all(db, bizes, lookback: int) -> tuple[int, int]:
    """開始前の下流のオーダーすべてが、期待どおりの1件を待っているか。（つながった数, 待ち先なしの数）"""
    ups = orders_of(db, bizes["up"])
    linked = waiting = 0
    for d in orders_of(db, bizes["down"]):
        if not links.is_not_started(d):
            continue
        (row,) = active_links(db, d.id)
        want = expected(ups, d.scheduled_start, lookback)
        assert row.upstream_order_id == (want.id if want else None), (d.scheduled_start, row.upstream_order_id, want and want.id)
        assert row.match_rule == LATEST and row.match_date is None and row.match_sheet_event_id is None
        linked += want is not None
        waiting += want is None
    return linked, waiting


def downstream_on(db, bizes, day: date) -> OpeOrder:
    return next(o for o in orders_of(db, bizes["down"]) if o.occurrence_date == day and o.status != OrderStatus.CANCELED)


# ==========================================
# 週次 → 毎日
# ==========================================
def test_each_daily_order_waits_for_the_latest_weekly_order(db, bizes):
    setup(db, bizes, lookback=10)
    linked, _ = check_all(db, bizes, 10)
    assert linked > 50  # 90 日先までの毎日のオーダーのほとんどが、その週の月曜を待つ

    # 月曜 10:00 の下流は、同じ日の 9:00 の上流を待つ（同じ日でも開始が前なら「直前」）
    ups = [u for u in orders_of(db, bizes["up"])]
    monday = ups[1].occurrence_date
    (row,) = active_links(db, downstream_on(db, bizes, monday).id)
    assert row.upstream_order_id == ups[1].id


def test_lookback_limit_leaves_waiting_rows(db, bizes):
    setup(db, bizes, lookback=3)
    linked, waiting = check_all(db, bizes, 3)
    # 月曜 9:00 から 3 日以内 = 月・火・水と木曜の朝まで。木曜 10:00 以降は待ち先なし
    assert linked > 0 and waiting > 0
    thursday = orders_of(db, bizes["up"])[1].occurrence_date + timedelta(days=3)
    (row,) = active_links(db, downstream_on(db, bizes, thursday).id)
    assert row.upstream_order_id is None
    assert "in the 3 days before it" in links.describe(db, row)


# ==========================================
# 取消・移動
# ==========================================
def test_a_canceled_latest_upstream_is_still_awaited(db, bizes, staff):
    setup(db, bizes, lookback=10)
    canceled = orders_of(db, bizes["up"])[2]
    ope.cancel_order(db, canceled.id, staff["releaser"], "今週はデータなし")

    # その週の下流は取消済みの月曜を待ったまま（前の週の月曜には戻らない）
    tuesday = canceled.occurrence_date + timedelta(days=1)
    (row,) = active_links(db, downstream_on(db, bizes, tuesday).id)
    assert row.upstream_order_id == canceled.id
    check_all(db, bizes, 10)


def test_moving_the_upstream_re_resolves_the_downstream_orders(db, bizes, staff):
    setup(db, bizes, lookback=10)
    ups = orders_of(db, bizes["up"])
    target = ups[2]
    previous = ups[1]
    # 人が月曜の上流を水曜 9:00 に動かす → 火曜の下流は前の週の月曜を、水曜以降はこの上流を待つ
    plan_services.override_instance(db, target.current_instance_id, PlanInstanceOverride(
        scheduled_start=target.scheduled_start + timedelta(days=2),
    ), actor_staff_id=staff["releaser"])
    check_all(db, bizes, 10)
    tuesday = target.occurrence_date + timedelta(days=1)
    (row,) = active_links(db, downstream_on(db, bizes, tuesday).id)
    assert row.upstream_order_id == previous.id
    old = [r for r in db.scalars(select(OpeDependency).where(
        OpeDependency.downstream_order_id == downstream_on(db, bizes, tuesday).id, OpeDependency.removed_at.is_not(None),
    ))]
    assert [r.removed_reason for r in old] == [links.REASON_UPSTREAM_CHANGED]


def test_moving_the_downstream_re_resolves_it(db, bizes, staff):
    setup(db, bizes, lookback=10)
    ups = orders_of(db, bizes["up"])
    tuesday = ups[1].occurrence_date + timedelta(days=1)
    down = downstream_on(db, bizes, tuesday)
    (row,) = active_links(db, down.id)
    assert row.upstream_order_id == ups[1].id

    # 下流を翌週の火曜に動かす（ルール上の日付は同じ・時刻だけ変わる）→ 翌週の月曜を待つ
    plan_services.override_instance(db, down.current_instance_id, PlanInstanceOverride(
        scheduled_start=down.scheduled_start + timedelta(days=7),
    ), actor_staff_id=staff["releaser"])
    db.expire_all()
    (row,) = active_links(db, down.id)
    assert row.upstream_order_id == ups[2].id
    check_all(db, bizes, 10)
