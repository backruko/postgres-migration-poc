"""
読み取り専用の API キー（system_agent）: キーは職員の機械用のロールに許された呼び出しでだけ通る。
system_agent は機械用のアカウントだけ（signal_sender のほかのロール・パスワードは持てない）。
"""
import uuid
from types import SimpleNamespace

import pytest
from fastapi.security import HTTPAuthorizationCredentials
from starlette.requests import Request

from src.core.clock import utc_now
from src.core.exceptions import BadRequestError, UnauthorizedError
from src.domain_staff import credentials, services
from src.domain_staff.auth import API_KEY_ROUTES, API_KEY_ROUTES_BY_ROLE, api_key_allows, get_current_staff
from src.domain_staff.models import MACHINE_ROLES, RoleCode, StaffStatus
from src.domain_staff.schemas import StaffCreate
from src.infrastructure.database import SessionLocal

# API キーの最後の利用日時と、読む予定（今日・これから）を同じ「いま」で見るため
pytestmark = pytest.mark.usefixtures("frozen_clock")




@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def now():
    return utc_now()


def make_staff(db, *roles: RoleCode):
    return services.create_staff(
        db, StaffCreate(login_id="ak-" + uuid.uuid4().hex[:10], name="agent test", roles=list(roles)),
        actor=None, status=StaffStatus.ACTIVE,
    )


def key_for(db, staff) -> HTTPAuthorizationCredentials:
    key = credentials.issue_api_key(db, staff, "test", actor=None, now=now()).key
    db.commit()
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=key)


def request(method: str, path: str) -> Request:
    return Request({"type": "http", "method": method, "path": path, "headers": [], "route": SimpleNamespace(path=path)})


def call(db, method: str, path: str, bearer: HTTPAuthorizationCredentials):
    return get_current_staff(request(method, path), db=db, session_token=None, bearer=bearer)


# ==========================================
# どの呼び出しでキーが通るか
# ==========================================
def test_a_system_agent_key_reads_alerts_daily_and_me_only(db):
    agent = make_staff(db, RoleCode.SYSTEM_AGENT)
    bearer = key_for(db, agent)
    for method, path in (("GET", "/ops/attention"), ("GET", "/ops/daily"), ("GET", "/staff/me")):
        assert call(db, method, path, bearer).id == agent.id, path
    # ほかの参照・変更・信号の送信は 401（キーでは誰でもない）
    for method, path in (("GET", "/biz/"), ("GET", "/ops/sla"), ("POST", "/ope/signals"), ("GET", "/ope/orders/{order_id}")):
        with pytest.raises(UnauthorizedError):
            call(db, method, path, bearer)


def test_a_signal_sender_key_still_only_posts_signals(db):
    sender = make_staff(db, RoleCode.SIGNAL_SENDER)
    bearer = key_for(db, sender)
    assert call(db, "POST", "/ope/signals", bearer).id == sender.id
    for method, path in (("GET", "/ops/attention"), ("GET", "/ops/daily"), ("GET", "/staff/me")):
        with pytest.raises(UnauthorizedError):
            call(db, method, path, bearer)


def test_an_account_with_both_machine_roles_gets_both_sets(db):
    both = make_staff(db, RoleCode.SIGNAL_SENDER, RoleCode.SYSTEM_AGENT)
    bearer = key_for(db, both)
    assert call(db, "POST", "/ope/signals", bearer).id == both.id
    assert call(db, "GET", "/ops/attention", bearer).id == both.id


def test_revoked_and_forged_keys_are_refused(db):
    agent = make_staff(db, RoleCode.SYSTEM_AGENT)
    bearer = key_for(db, agent)
    api_key = credentials.resolve_api_key(db, bearer.credentials, now=now())
    assert api_key is not None
    credentials.revoke_api_key(db, api_key, actor=None, now=now())
    db.commit()
    with pytest.raises(UnauthorizedError):
        call(db, "GET", "/ops/attention", bearer)
    with pytest.raises(UnauthorizedError):
        call(db, "GET", "/ops/attention", HTTPAuthorizationCredentials(scheme="Bearer", credentials="ops_forged"))


def test_the_route_table_is_read_only_for_system_agent():
    assert API_KEY_ROUTES_BY_ROLE[RoleCode.SYSTEM_AGENT] == {
        ("GET", "/ops/attention"), ("GET", "/ops/schedule-feed"), ("GET", "/ops/daily"), ("GET", "/staff/me"),  # schedule-feed: 並行稼働の時刻表
    }
    assert all(method == "GET" for method, _ in API_KEY_ROUTES_BY_ROLE[RoleCode.SYSTEM_AGENT])
    assert set(API_KEY_ROUTES_BY_ROLE) == MACHINE_ROLES
    assert frozenset().union(*API_KEY_ROUTES_BY_ROLE.values()) == API_KEY_ROUTES
    # 実在するルート（パスの書き間違いで、キーが黙って使えなくなるのを防ぐ）
    from tests.test_client_scope import _all_routes

    real = {(m, r.path) for r in _all_routes() for m in r.methods or ()}
    assert real >= API_KEY_ROUTES, API_KEY_ROUTES - real


def test_api_key_allows_checks_the_owners_roles(db):
    agent = make_staff(db, RoleCode.SYSTEM_AGENT)
    viewer = make_staff(db, RoleCode.VIEWER)
    assert api_key_allows(agent, ("GET", "/ops/daily"))
    assert not api_key_allows(agent, ("POST", "/ope/signals"))
    assert not api_key_allows(viewer, ("GET", "/ops/daily"))


# ==========================================
# 機械用のアカウントだけ
# ==========================================
def test_keys_are_issued_to_machine_accounts_only(db):
    with pytest.raises(BadRequestError, match="machine accounts"):
        credentials.issue_api_key(db, make_staff(db, RoleCode.VIEWER), "x", actor=None, now=now())
    credentials.issue_api_key(db, make_staff(db, RoleCode.SYSTEM_AGENT), "x", actor=None, now=now())
    credentials.issue_api_key(db, make_staff(db, RoleCode.SIGNAL_SENDER), "x", actor=None, now=now())


def test_system_agent_cannot_be_combined_with_human_roles(db):
    with pytest.raises(BadRequestError, match="machine accounts only"):
        make_staff(db, RoleCode.SYSTEM_AGENT, RoleCode.VIEWER)
    db.rollback()
    admin = make_staff(db, RoleCode.SYSTEM_ADMIN)
    agent = make_staff(db, RoleCode.SYSTEM_AGENT)
    with pytest.raises(BadRequestError, match="machine accounts only"):
        services.set_staff_roles(db, agent.id, [RoleCode.SYSTEM_AGENT, RoleCode.ADMIN], actor=admin)
    db.rollback()
    services.set_staff_roles(db, agent.id, [RoleCode.SYSTEM_AGENT, RoleCode.SIGNAL_SENDER], actor=admin)
    assert set(agent.role_codes) == MACHINE_ROLES


def test_system_agent_never_has_a_password(db):
    admin = make_staff(db, RoleCode.SYSTEM_ADMIN)
    agent = make_staff(db, RoleCode.SYSTEM_AGENT)
    with pytest.raises(BadRequestError, match="never has a password"):
        services.set_temporary_password(db, agent.id, "temporary pw 1234", actor=admin)
    db.rollback()
    with pytest.raises(BadRequestError, match="never has a password"):
        credentials.set_password(db, agent, "own pw 12345678", temporary=False, now=now())  # scripts/set_password.py も同じ
    db.rollback()
    # パスワードのある職員（画面にログインできる）には付けられない: 別の機械用アカウントを作る
    person = make_staff(db, RoleCode.SIGNAL_SENDER)
    credentials.set_password(db, person, "person pw 12345", temporary=False, now=now())
    db.commit()
    with pytest.raises(BadRequestError, match="separate machine account"):
        services.set_staff_roles(db, person.id, [RoleCode.SIGNAL_SENDER, RoleCode.SYSTEM_AGENT], actor=admin)
