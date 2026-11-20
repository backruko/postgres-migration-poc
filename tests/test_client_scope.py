"""
顧客の範囲（担当顧客が限られた職員）: 範囲の設定、全顧客に共通の操作の拒否、ClientScope、すべてのルートの目印。
"""
import inspect
import uuid
from datetime import date, time, timedelta
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest
from fastapi.routing import APIRoute
from pydantic import ValidationError
from sqlalchemy import select

from src.core.clock import local_date_of, utc_now
from src.core.error_codes import ErrorCode
from src.core.exceptions import BadRequestError, ForbiddenError, NotFoundError
from src.domain_client.schemas import ClientCreate
from src.domain_staff import services
from src.domain_staff.auth import ScopeKind, check_roles, client_scope, get_client_scope, require_roles
from src.domain_staff.models import RoleCode, StaffClient, StaffStatus
from src.domain_staff.schemas import StaffClientsUpdate, StaffCreate, StaffResponse
from src.domain_staff.scope import ALL_CLIENTS, ClientScope
from src.infrastructure.database import SessionLocal
from src.infrastructure.repositories import client_repo
from tests.client_codes import new_client_code

# 担当顧客の範囲の確かめで作るシート・信号・締切を、同じ「いま」から決めるため
pytestmark = pytest.mark.usefixtures("frozen_clock")




@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def make_staff(db, *roles: RoleCode):
    login_id = "cs-" + uuid.uuid4().hex[:10]
    return services.create_staff(
        db, StaffCreate(login_id=login_id, name="範囲テスト", roles=list(roles)), actor=None, status=StaffStatus.ACTIVE
    )


def make_client(db, *, active: bool = True) -> str:
    code = new_client_code("Q")
    client = client_repo.create(db, obj_in=ClientCreate(code=code, name=f"範囲 {code}"))
    if not active:
        client.is_active = False
        db.commit()
    return code


# ==========================================
# 範囲の設定（PUT /staff/{id}/clients）
# ==========================================
def test_set_clients_replaces_the_set_and_empty_means_all(db):
    admin = make_staff(db, RoleCode.SYSTEM_ADMIN)
    operator = make_staff(db, RoleCode.OPERATOR)
    a, b, c = make_client(db), make_client(db), make_client(db)
    assert operator.client_codes == []
    assert not get_client_scope(operator).limited

    services.set_staff_clients(db, operator.id, sorted([a, b]), actor=admin)
    assert operator.client_codes == sorted([a, b])
    scope = get_client_scope(operator)
    assert scope.limited and scope.allows(a) and scope.allows(b.lower()) and not scope.allows(c)
    first = db.scalars(select(StaffClient).where(StaffClient.staff_user_id == operator.id, StaffClient.client_code == a)).one()
    granted_at = first.granted_at

    # 置き換え: 残る行（a）の設定日時はそのまま、b は外れ、c が加わる
    services.set_staff_clients(db, operator.id, sorted([a, c]), actor=admin)
    assert operator.client_codes == sorted([a, c])
    db.refresh(first)
    assert first.granted_at == granted_at and first.granted_by_id == admin.id
    assert StaffResponse.model_validate(operator).client_codes == sorted([a, c])

    # 空 = 全顧客に戻る
    services.set_staff_clients(db, operator.id, [], actor=admin)
    assert operator.client_codes == [] and not get_client_scope(operator).limited


def test_set_clients_refuses_unknown_and_newly_added_inactive_clients(db):
    admin = make_staff(db, RoleCode.SYSTEM_ADMIN)
    viewer = make_staff(db, RoleCode.VIEWER)
    active, inactive = make_client(db), make_client(db, active=False)
    with pytest.raises(BadRequestError, match="do not exist"):
        services.set_staff_clients(db, viewer.id, ["ZZZ!"], actor=admin)
    with pytest.raises(BadRequestError, match="inactive"):
        services.set_staff_clients(db, viewer.id, [inactive], actor=admin)
    assert viewer.client_codes == []

    # 範囲に入れた後で無効にした顧客は、残したまま置き換えられる（新しく加えるのでなければ拒否しない）
    services.set_staff_clients(db, viewer.id, [active], actor=admin)
    client = client_repo.get_by_code(db, code=active)
    assert client is not None
    client.is_active = False
    db.commit()
    other = make_client(db)
    services.set_staff_clients(db, viewer.id, sorted([active, other]), actor=admin)
    assert viewer.client_codes == sorted([active, other])


def test_a_system_admin_always_handles_all_clients(db):
    admin = make_staff(db, RoleCode.SYSTEM_ADMIN)
    other_admin = make_staff(db, RoleCode.SYSTEM_ADMIN, RoleCode.ADMIN)
    staff = make_staff(db, RoleCode.ADMIN)
    code = make_client(db)
    with pytest.raises(BadRequestError, match="system admin must handle all clients"):
        services.set_staff_clients(db, other_admin.id, [code], actor=admin)
    services.set_staff_clients(db, staff.id, [code], actor=admin)
    with pytest.raises(BadRequestError, match="system admin must handle all clients"):
        services.set_staff_roles(db, staff.id, [RoleCode.ADMIN, RoleCode.SYSTEM_ADMIN], actor=admin)
    # ほかのロールの変更は範囲があってもできる
    services.set_staff_roles(db, staff.id, [RoleCode.ADMIN, RoleCode.RELEASER], actor=admin)
    assert staff.role_codes == [RoleCode.RELEASER, RoleCode.ADMIN] and staff.client_codes == [code]


def test_clients_update_body_is_normalized_and_strict():
    assert StaffClientsUpdate(client_codes=[" ab12", "AA01"]).client_codes == ["AA01", "AB12"]
    with pytest.raises(ValidationError):
        StaffClientsUpdate(client_codes=["AB1"])
    with pytest.raises(ValidationError):
        StaffClientsUpdate(client_codes=["AB12", "ab12"])
    with pytest.raises(ValidationError):
        StaffClientsUpdate.model_validate({"client_codes": [], "roles": []})


# ==========================================
# 全顧客に共通の操作（require_roles(..., all_clients=True)）
# ==========================================
def test_all_clients_guard_refuses_limited_staff_with_403(db):
    admin = make_staff(db, RoleCode.SYSTEM_ADMIN)
    limited = make_staff(db, RoleCode.ADMIN)
    unlimited = make_staff(db, RoleCode.ADMIN)
    services.set_staff_clients(db, limited.id, [make_client(db)], actor=admin)

    check_roles(unlimited, frozenset({RoleCode.ADMIN}), all_clients=True)
    check_roles(limited, frozenset({RoleCode.ADMIN}))  # 目印の無い操作はロールだけで判定
    with pytest.raises(ForbiddenError) as e:
        check_roles(limited, frozenset({RoleCode.ADMIN}), all_clients=True)
    assert e.value.error_code == ErrorCode.ALL_CLIENTS_REQUIRED

    @require_roles([RoleCode.ADMIN], all_clients=True)
    def endpoint():
        return "ok"

    assert getattr(endpoint, "__client_scope__") == ScopeKind.ALL_CLIENTS
    call = cast(Any, endpoint)
    assert call(_require_roles_current_staff=unlimited) == "ok"
    with pytest.raises(ForbiddenError):
        call(_require_roles_current_staff=limited)


def test_client_scope_marker_is_declared_once():
    @client_scope(ScopeKind.CHECKED)
    def endpoint():
        return None

    assert getattr(endpoint, "__client_scope__") == ScopeKind.CHECKED
    with pytest.raises(TypeError):
        client_scope(ScopeKind.FILTERED)(endpoint)
    with pytest.raises(ValueError):
        client_scope(ScopeKind.ALL_CLIENTS)
    with pytest.raises(ValueError):
        client_scope("everything")

    # 目印はロールのガードの内側に付けても外から見える（functools.wraps が写す）
    @require_roles([RoleCode.OPERATOR])
    @client_scope(ScopeKind.CHECKED)
    def guarded():
        return None

    assert getattr(guarded, "__client_scope__") == ScopeKind.CHECKED


# ==========================================
# ClientScope
# ==========================================
def test_client_scope_filters_in_sql_and_requires_with_the_same_404(db):
    a, b = make_client(db), make_client(db)
    from src.domain_client.models import ClientMaster

    def codes(scope: ClientScope) -> set[str]:
        return set(db.scalars(select(ClientMaster.code).where(ClientMaster.code.in_([a, b]), scope.clause(ClientMaster.code))))

    assert codes(ALL_CLIENTS) == {a, b}
    assert codes(ClientScope.of([a])) == {a}
    assert ClientScope.of([]) == ALL_CLIENTS
    assert ClientScope.of([b]).visible([a, b, a]) == [b]

    ALL_CLIENTS.require(a, "Order (id=1) not found.")
    with pytest.raises(NotFoundError, match=r"Order \(id=1\) not found\.") as e:
        ClientScope.of([a]).require(b, "Order (id=1) not found.")
    assert e.value.error_code == ErrorCode.NOT_FOUND
    with pytest.raises(NotFoundError):
        ClientScope.of([a]).require(None, "x")


# ==========================================
# biz / clients / ops の一覧と1件の確認
# ==========================================
JST = ZoneInfo("Asia/Tokyo")


def make_biz(db, code: str, name: str = "範囲の業務"):
    from src.domain_biz import services as biz_services
    from src.domain_biz.schemas import BizMasterCreate

    return biz_services.create_biz(db, BizMasterCreate(client_code=code, name=name, business_seq=None, manager_email=None, description=None))


def daily_template(db, biz_id: int, **kwargs):
    from src.domain_plan import services as plan_services
    from src.domain_plan.models import TemplateType
    from src.domain_plan.schemas import RRuleTemplateCreate

    data = dict(
        biz_master_id=biz_id, name="範囲の予定", template_type=TemplateType.RRULE, timezone="Asia/Tokyo",
        start_time=time(9, 0), duration_minutes=60, anchor_date=date(2026, 1, 1), rrule="FREQ=DAILY",
    )
    data.update(kwargs)
    return plan_services.create_template(db, RRuleTemplateCreate.model_validate(data))


@pytest.fixture
def two_clients(db):
    """顧客 A・B に業務を1つずつ（毎日の予定・オーダー付き）"""
    a, b = make_client(db), make_client(db)
    biz_a, biz_b = make_biz(db, a), make_biz(db, b)
    t_a = daily_template(db, biz_a.id, warn_before_minutes=30)
    t_b = daily_template(db, biz_b.id, warn_before_minutes=30)
    return {"a": a, "b": b, "biz_a": biz_a, "biz_b": biz_b, "t_a": t_a, "t_b": t_b, "only_a": ClientScope.of([a])}


def test_biz_and_client_lists_show_only_the_scope(db, two_clients):
    from src.domain_biz import services as biz_services
    from src.domain_client import services as client_services

    a, b, only_a = two_clients["a"], two_clients["b"], two_clients["only_a"]
    everyone = {c.code for c in client_services.get_clients(db, include_inactive=True)}
    assert {a, b} <= everyone
    assert {c.code for c in client_services.get_clients(db, include_inactive=True, scope=only_a)} == {a}

    assert {x.client_code for x in biz_services.get_bizes(db, limit=10000, scope=only_a)} == {a}
    # 顧客を指定しても範囲の外なら空（ほかの顧客の業務は見えない）
    assert biz_services.get_bizes(db, client_code=b, scope=only_a) == []
    assert [x.id for x in biz_services.get_bizes(db, client_code=b)] == [two_clients["biz_b"].id]


def test_submitted_drafts_list_and_count_follow_the_scope(db, two_clients):
    from src.domain_biz import services as biz_services
    from src.domain_biz.schemas import SOPStepTemplateCreate, SOPVersionCreate

    drafter = make_staff(db, RoleCode.RELEASER)
    for key in ("biz_a", "biz_b"):
        draft = biz_services.create_new_sop_version(db, two_clients[key].id, SOPVersionCreate(version_tag="v1.0.0"), actor_id=drafter.id)
        biz_services.create_step_template(db, draft.id, SOPStepTemplateCreate.model_validate({"title": "確認"}), actor_id=drafter.id)
        biz_services.submit_draft(db, draft.id, actor_id=drafter.id)
    a, only_a = two_clients["a"], two_clients["only_a"]
    total = biz_services.count_submitted_drafts(db)
    mine = biz_services.list_submitted_drafts(db, scope=only_a)
    assert {d.client_code for d in mine} == {a} and len(mine) == 1
    assert biz_services.count_submitted_drafts(db, scope=only_a) == 1
    assert total >= 2 and len(biz_services.list_submitted_drafts(db)) == total
    assert biz_services.list_submitted_drafts(db, client_code=two_clients["b"], scope=only_a) == []


def test_daily_attention_and_sla_show_only_the_scope(db, two_clients, monkeypatch):
    from src.coordination import services as ops_services
    from src.domain_ope.models import OpeOrder

    a, b, only_a = two_clients["a"], two_clients["b"], two_clients["only_a"]
    order = db.scalars(
        select(OpeOrder).where(OpeOrder.template_id == two_clients["t_a"].id).order_by(OpeOrder.scheduled_start).offset(1)
    ).first()
    assert order is not None and order.deadline_at is not None
    day = order.scheduled_start.astimezone(JST).date()

    both = {c.client_code for c in ops_services.get_daily_dashboard(db, day, "Asia/Tokyo").clients}
    assert {a, b} <= both
    dash = ops_services.get_daily_dashboard(db, day, "Asia/Tokyo", scope=only_a)
    assert [c.client_code for c in dash.clients] == [a]
    assert dash.total == sum(len(biz.occurrences) for c in dash.clients for biz in c.businesses)
    assert dash.calendars == ops_services.get_daily_dashboard(db, day, "Asia/Tokyo").calendars  # カレンダーは共通

    # 遅れ・まもなく: 範囲の顧客だけ。件数の上限は絞った後に効く
    deadline = order.deadline_at
    monkeypatch.setattr(ops_services, "_now", lambda: deadline + timedelta(days=2))
    everything = {i.client_code for i in ops_services.get_attention(db, limit=1000).items}
    assert {a, b} <= everything
    limited = ops_services.get_attention(db, limit=1, scope=only_a)
    assert len(limited.items) == 1 and limited.items[0].client_code == a and limited.truncated

    month = day.strftime("%Y-%m")
    rows = ops_services.get_sla_report(db, from_month=month, to_month=month, client_code=None, scope=only_a).rows
    assert rows and {r.client_code for r in rows} == {a}
    assert ops_services.get_sla_report(db, from_month=month, to_month=month, client_code=b, scope=only_a).rows == []


def test_single_item_guards_answer_404_outside_the_scope(db, two_clients):
    from src.coordination import scope_guards as g
    from src.domain_biz import services as biz_services
    from src.domain_biz.models import DependencyMatchRule
    from src.domain_biz.schemas import BizDependencyCreate, SOPStepTemplateCreate, SOPVersionCreate

    b, only_a = two_clients["b"], two_clients["only_a"]
    biz_b, t_b = two_clients["biz_b"], two_clients["t_b"]
    other_b = make_biz(db, b, name="範囲の業務 2")
    draft = biz_services.create_new_sop_version(db, biz_b.id, SOPVersionCreate(version_tag="v1.0.0"))
    step = biz_services.create_step_template(db, draft.id, SOPStepTemplateCreate.model_validate({"title": "確認"}))
    dep = biz_services.create_dependency(
        db, biz_b.id, BizDependencyCreate(upstream_biz_id=other_b.id, match_rule=DependencyMatchRule.SAME_OCCURRENCE_DATE),
    )

    cases = [
        (lambda s: g.check_biz(db, s, biz_b.id), rf"BizMaster\(id={biz_b.id}\) not found\."),
        (lambda s: g.check_sop_version(db, s, draft.id), rf"SOPVersion\(id={draft.id}\) not found\."),
        (lambda s: g.check_sop_step(db, s, step.id), rf"SOPStepTemplate\(id={step.id}\) not found\."),
        (lambda s: g.check_biz_dependency(db, s, dep.id), rf"BizDependency\(id={dep.id}\) not found\."),
        (lambda s: g.check_template(db, s, t_b.id), rf"PlanTemplate\(id={t_b.id}\) not found\."),
        (lambda s: g.check_client(s, b.lower()), rf"Client with code '{b}' does not exist\."),
    ]
    for call, message in cases:
        call(ALL_CLIENTS)          # 全顧客なら通る
        call(ClientScope.of([b]))  # 範囲の中なら通る
        with pytest.raises(NotFoundError, match=message):
            call(only_a)           # 範囲の外は、無いときと同じ 404

    # 論理削除された業務も範囲で守る（復元の前の確認）
    biz_services.delete_dependency(db, dep.id)
    biz_services.delete_biz(db, other_b.id)
    with pytest.raises(NotFoundError):
        g.check_biz(db, only_a, other_b.id)
    # 無い ID は何もしない（サービスがいつもどおり 404 にする）
    g.check_biz(db, only_a, 10**9)
    g.check_template_change(db, only_a, 10**9)


# ==========================================
# plans / ope
# ==========================================
def test_template_changes_list_and_count_follow_the_scope_even_for_deleted_businesses(db, two_clients):
    from sqlalchemy import update

    from src.domain_biz.models import BizMaster
    from src.domain_plan import template_changes
    from src.domain_plan.models import TemplateChangeKind, TemplateChangeStatus
    from src.domain_plan.schemas import PlanTemplateUpdate, TemplateUpdateDraft

    drafter = make_staff(db, RoleCode.RELEASER)
    for key in ("t_a", "t_b"):
        template_changes.create_draft(db, TemplateUpdateDraft(
            kind=TemplateChangeKind.UPDATE, template_id=two_clients[key].id, changes=PlanTemplateUpdate(name="範囲の予定 改"),
        ), actor_id=drafter.id)
    only_a = two_clients["only_a"]
    mine = template_changes.list_changes(db, drafted_by_id=drafter.id, scope=only_a)
    assert [c.template_id for c in mine] == [two_clients["t_a"].id]
    assert template_changes.count_changes(db, status=TemplateChangeStatus.DRAFT, drafted_by_id=drafter.id, scope=only_a) == 1
    assert template_changes.count_changes(db, status=TemplateChangeStatus.DRAFT, drafted_by_id=drafter.id) == 2
    assert template_changes.list_changes(db, biz_master_id=two_clients["biz_b"].id, scope=only_a) == []

    # 業務を論理削除しても、範囲の中の記録は今までどおり見える（表の列で絞るので全局フィルタが掛からない）
    db.execute(update(BizMaster).where(BizMaster.id == two_clients["biz_a"].id).values(is_deleted=True))
    db.commit()
    assert len(template_changes.list_changes(db, drafted_by_id=drafter.id, scope=only_a)) == 1
    assert len(template_changes.list_changes(db, drafted_by_id=drafter.id)) == len(
        template_changes.list_changes(db, drafted_by_id=drafter.id, scope=ClientScope.of([two_clients["a"], two_clients["b"]]))
    )


def test_signals_list_shows_only_the_scope(db, two_clients):
    from sqlalchemy import func, select

    from src.domain_ope import signals
    from src.domain_ope.schemas import SignalIn

    sender = make_staff(db, RoleCode.SIGNAL_SENDER)
    now = utc_now().astimezone(JST)
    # since は recorded_at と比べる。recorded_at は DB が自分の時計で付けるので、since も DB の時計から取る
    # （アプリの時計は止めてあり、DB の時計と同じではない）
    since = db.scalar(select(func.now())) - timedelta(minutes=1)
    a, b = two_clients["a"], two_clients["b"]
    for code in (a, b.lower()):  # 小文字で送っても大文字で残る
        signals.receive_signal(db, SignalIn(type="C3_NONE", client_code=code, key="2026-11-12", received_at=now), sender.id)
    listed = {x.client_code for x in signals.list_signals(db, scope="all", since=since, limit=1000)}
    assert {a, b} <= listed
    mine = signals.list_signals(db, scope="all", since=since, limit=1000, clients=two_clients["only_a"])
    assert mine and {x.client_code for x in mine} == {a}
    assert signals.list_signals(db, scope="all", client_code=b, clients=two_clients["only_a"]) == []


def test_plan_and_ope_guards_answer_404_outside_the_scope(db, two_clients):
    from src.coordination import scope_guards as g
    from src.domain_ope import signals
    from src.domain_ope.models import OpeOrder, OpeSignal, OpeStep
    from src.domain_ope.schemas import SignalIn
    from src.domain_plan import services as plan_services
    from src.domain_plan.models import PlanInstance
    from src.domain_plan.schemas import ReferenceSheetCreate, ReferenceSheetEventsCreate, ReferenceSheetMarkerCreate

    b, only_a = two_clients["b"], two_clients["only_a"]
    today = local_date_of(utc_now())
    sheet = plan_services.create_sheet(db, ReferenceSheetCreate(
        client_code=b, name=f"範囲 {uuid.uuid4().hex[:6]}", period_start=today, period_end=today + timedelta(days=60),
    ))
    plan_services.add_marker(db, sheet.id, ReferenceSheetMarkerCreate(marker="M", description=None, rule=None))
    marker = plan_services.add_sheet_events(db, sheet.id, "M", ReferenceSheetEventsCreate(dates=[today + timedelta(days=3)]))
    event_id = marker.events[0].id
    instance_id = db.scalars(select(PlanInstance.id).where(PlanInstance.template_id == two_clients["t_b"].id)).first()
    order = db.scalars(select(OpeOrder).where(OpeOrder.template_id == two_clients["t_b"].id)).first()
    assert instance_id is not None and order is not None
    step_id = db.scalars(select(OpeStep.id).where(OpeStep.order_id == order.id)).first()
    sender = make_staff(db, RoleCode.SIGNAL_SENDER)
    signals.receive_signal(db, SignalIn(type="C3_GUARD", client_code=b, key="2026-11-12", received_at=utc_now()), sender.id)
    signal_id = db.scalars(select(OpeSignal.id).where(OpeSignal.signal_type == "C3_GUARD", OpeSignal.client_code == b)).first()
    assert signal_id is not None

    cases = [
        (lambda s: g.check_sheet(db, s, sheet.id), rf"ReferenceSheet\(id={sheet.id}\) not found\."),
        (lambda s: g.check_sheet_event(db, s, event_id), rf"ReferenceSheetEvent\(id={event_id}\) not found\."),
        (lambda s: g.check_instance(db, s, instance_id), rf"PlanInstance\(id={instance_id}\) not found\."),
        (lambda s: g.check_order(db, s, order.id), rf"Order\(id={order.id}\) not found\."),
        (lambda s: g.check_signal(db, s, signal_id), rf"Signal\(id={signal_id}\) not found\."),
    ]
    if step_id is not None:
        cases.append((lambda s: g.check_order_step(db, s, step_id), rf"Step\(id={step_id}\) not found\."))
    for call, message in cases:
        call(ALL_CLIENTS)
        call(ClientScope.of([b]))
        with pytest.raises(NotFoundError, match=message):
            call(only_a)


# ==========================================
# すべてのルートに目印（ScopeKind）があること
# ==========================================
# まだ確かめないルーターの prefix（今は空。新しいルーターも初めから目印を付ける）
NOT_YET_DECLARED: tuple[str, ...] = ()


def _all_routes() -> list[APIRoute]:
    """アプリのすべてのルート（include した router のルートは app.routes に並ばないため、各 router から集める）"""
    from main import app
    from src.coordination.router import router as ops_router
    from src.domain_biz.router import router as biz_router
    from src.domain_client.router import router as client_router
    from src.domain_ope.router import router as ope_router
    from src.domain_plan.router import router as plan_router
    from src.domain_staff.auth_router import router as auth_router
    from src.domain_staff.router import router as staff_router

    routers = [auth_router, client_router, biz_router, plan_router, ops_router, staff_router, ope_router]
    routes = [r for r in app.routes if isinstance(r, APIRoute)]
    routes += [r for router in routers for r in router.routes if isinstance(r, APIRoute)]
    return routes


def test_every_route_declares_how_it_handles_the_client_scope():
    missing = []
    checked = 0
    for route in _all_routes():
        if route.path.startswith(NOT_YET_DECLARED):
            continue
        kind = getattr(route.endpoint, "__client_scope__", None)
        if not isinstance(kind, ScopeKind):
            missing.append((sorted(route.methods or ()), route.path))
        checked += 1
    assert not missing, f"routes without a client scope mark: {missing}"
    assert checked >= 120


def _takes_scope(route: APIRoute) -> bool:
    """エンドポイントが ClientScope を受け取る（一覧の絞り込み・本文の値の確認）"""
    return any(p.annotation is ClientScope for p in inspect.signature(route.endpoint).parameters.values())


def test_checked_and_filtered_routes_really_use_the_scope():
    """目印だけでなく、CHECKED はパスの ID の確認（SCOPE_GUARDS）か ClientScope の引数、FILTERED は ClientScope の引数を持つ"""
    from src.coordination.scope_guards import SCOPE_GUARDS

    wrong = []
    for route in _all_routes():
        if route.path.startswith(NOT_YET_DECLARED):
            continue
        kind = getattr(route.endpoint, "__client_scope__", None)
        guarded = any(d.dependency in SCOPE_GUARDS for d in route.dependencies)
        if kind == ScopeKind.CHECKED and not (guarded or _takes_scope(route)):
            wrong.append(("checked without a guard", route.path))
        if kind == ScopeKind.FILTERED and not _takes_scope(route):
            wrong.append(("filtered without a scope", route.path))
        if kind in (ScopeKind.SHARED, ScopeKind.ALL_CLIENTS) and (guarded or _takes_scope(route)):
            wrong.append((f"{kind} but uses the scope", route.path))
    assert not wrong, wrong


def test_client_master_writes_are_shared_by_all_clients():
    marks = {
        (method, route.path): getattr(route.endpoint, "__client_scope__", None)
        for route in _all_routes() if route.path.startswith("/clients")
        for method in route.methods or ()
    }
    assert marks.pop(("GET", "/clients/")) == ScopeKind.FILTERED
    assert len(marks) == 4 and all(kind == ScopeKind.ALL_CLIENTS for kind in marks.values()), marks


def test_staff_accounts_are_shared_by_all_clients():
    marks = {
        (method, route.path): getattr(route.endpoint, "__client_scope__", None)
        for route in _all_routes() if route.path.startswith("/staff")
        for method in route.methods or ()
    }
    assert marks.pop(("GET", "/staff/me")) == ScopeKind.SHARED
    assert marks.pop(("GET", "/staff/roles")) == ScopeKind.SHARED
    assert marks and all(kind == ScopeKind.ALL_CLIENTS for kind in marks.values()), marks
    assert ("PUT", "/staff/{staff_id}/clients") in marks
