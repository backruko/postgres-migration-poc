# tests/test_staff.py
"""
domain_staff のテスト（職員・ロール・状態、require_roles デコレーター）。

※ Service層は内部で commit するため、テストごとに一意なログインIDを使ってデータを分離しています。
"""
import asyncio
import inspect
import uuid
from typing import Any, cast

import pytest
from fastapi.params import Depends as DependsParam
from pydantic import ValidationError

from src.core.error_codes import ErrorCode
from src.core.exceptions import BadRequestError, DuplicatedRecordError, ForbiddenError, UnauthorizedError
from src.domain_staff import services
from src.domain_staff.auth import check_roles, get_current_staff, require_active, require_roles
from src.domain_staff.models import RoleCode, StaffStatus
from src.domain_staff.schemas import StaffCreate, StaffResponse, StaffUpdate
from src.infrastructure.database import SessionLocal


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


def make_staff(db, *roles: RoleCode, status=StaffStatus.ACTIVE, actor=None):
    login_id = "u-" + uuid.uuid4().hex[:10]
    return services.create_staff(db, StaffCreate(login_id=login_id, name="テスト職員", roles=list(roles)), actor=actor, status=status)


# ==========================================
# 職員・ロール・状態
# ==========================================
def test_roles_are_seeded(db):
    assert [r.code for r in services.get_roles(db)] == [
        "operator", "checker", "releaser", "admin", "viewer", "auditor", "system_admin",
    ]
    assert {r.code: r.name for r in services.get_roles(db)}["system_admin"] == "システム管理者"


def test_create_staff_starts_pending_with_roles(db):
    admin = make_staff(db, RoleCode.SYSTEM_ADMIN)
    login_id = "u-" + uuid.uuid4().hex[:10]
    staff = services.create_staff(
        db, StaffCreate(login_id=f"  {login_id} ", name="山田 太郎", email="yamada@example.com",
                        roles=[RoleCode.CHECKER, RoleCode.OPERATOR]),
        actor=admin,
    )
    assert staff.status == StaffStatus.PENDING and staff.login_id == login_id  # 前後の空白は除去
    assert staff.role_codes == [RoleCode.OPERATOR, RoleCode.CHECKER]  # ロールの定義順
    response = StaffResponse.model_validate(staff)
    assert response.roles == [RoleCode.OPERATOR, RoleCode.CHECKER] and response.email == "yamada@example.com"

    with pytest.raises(DuplicatedRecordError):
        services.create_staff(db, StaffCreate(login_id=login_id, name="別人"), actor=admin)
    with pytest.raises(ValidationError, match="duplicated roles"):
        StaffCreate(login_id="x", name="x", roles=[RoleCode.VIEWER, RoleCode.VIEWER])
    with pytest.raises(ValidationError):
        StaffCreate(login_id="x", name="x", roles=["superuser"])  # type: ignore[list-item]

    renamed = services.update_staff(db, staff.id, StaffUpdate(name="山田 花子"))
    assert renamed.name == "山田 花子" and renamed.email == "yamada@example.com"


def test_status_changes_and_lockout_guards(db):
    admin = make_staff(db, RoleCode.SYSTEM_ADMIN)
    staff = make_staff(db, RoleCode.OPERATOR, status=StaffStatus.PENDING, actor=admin)

    assert services.set_staff_status(db, staff.id, StaffStatus.ACTIVE, admin).status == StaffStatus.ACTIVE
    suspended = services.set_staff_status(db, staff.id, StaffStatus.SUSPENDED, admin)
    assert suspended.status == StaffStatus.SUSPENDED and suspended.role_codes == [RoleCode.OPERATOR]  # ロールは残る
    with pytest.raises(BadRequestError, match="pending"):
        services.set_staff_status(db, staff.id, StaffStatus.PENDING, admin)
    with pytest.raises(BadRequestError, match="your own status"):
        services.set_staff_status(db, admin.id, StaffStatus.SUSPENDED, admin)


def test_set_roles_replaces_the_set_and_keeps_own_system_admin(db):
    admin = make_staff(db, RoleCode.SYSTEM_ADMIN)
    staff = make_staff(db, RoleCode.OPERATOR, RoleCode.CHECKER, actor=admin)

    updated = services.set_staff_roles(db, staff.id, [RoleCode.CHECKER, RoleCode.RELEASER], admin)
    assert updated.role_codes == [RoleCode.CHECKER, RoleCode.RELEASER]
    assert services.set_staff_roles(db, staff.id, [], admin).role_codes == []

    # 自分のシステム管理者ロールは外せない（他のロールの追加はできる）
    with pytest.raises(BadRequestError, match="system_admin"):
        services.set_staff_roles(db, admin.id, [RoleCode.ADMIN], admin)
    db.rollback()
    assert services.set_staff_roles(db, admin.id, [RoleCode.ADMIN, RoleCode.SYSTEM_ADMIN], admin).role_codes == [
        RoleCode.ADMIN, RoleCode.SYSTEM_ADMIN,
    ]


# ==========================================
# 権限の判定（check_roles / require_roles）
# ==========================================
def test_check_roles_requires_active_and_one_of_the_roles(db):
    operator = make_staff(db, RoleCode.OPERATOR)
    check_roles(operator, frozenset({RoleCode.OPERATOR, RoleCode.ADMIN}))
    check_roles(operator, None)  # require_active
    with pytest.raises(ForbiddenError) as e:
        check_roles(operator, frozenset({RoleCode.CHECKER}))
    assert e.value.status_code == 403 and e.value.error_code == ErrorCode.FORBIDDEN

    for status in (StaffStatus.PENDING, StaffStatus.SUSPENDED):
        inactive = make_staff(db, RoleCode.OPERATOR, status=status)
        with pytest.raises(ForbiddenError) as e:
            check_roles(inactive, None)
        assert e.value.error_code == ErrorCode.STAFF_NOT_ACTIVE and status.value in e.value.message


def test_require_roles_decorator_injects_and_checks_the_staff(db):
    operator = make_staff(db, RoleCode.OPERATOR)
    viewer = make_staff(db, RoleCode.VIEWER)

    @require_roles(["operator", "checker"])
    def endpoint(order_id: int, note: str = ""):
        return f"{order_id}{note}"

    # FastAPI が職員を注入する引数がシグネチャに追加される（get_current_staff に依存）
    param = inspect.signature(endpoint).parameters["_require_roles_current_staff"]
    assert param.kind == inspect.Parameter.KEYWORD_ONLY
    assert isinstance(param.default, DependsParam) and param.default.dependency is get_current_staff
    assert list(inspect.signature(endpoint).parameters)[:2] == ["order_id", "note"]
    assert endpoint.__name__ == "endpoint"

    # ロールがあれば本体が呼ばれ、注入された職員は本体に渡らない（FastAPI が渡す引数は宣言上の型に無いため Any で呼ぶ）
    call = cast(Any, endpoint)
    assert call(7, note="!", _require_roles_current_staff=operator) == "7!"
    with pytest.raises(ForbiddenError):
        call(7, _require_roles_current_staff=viewer)

    # async のエンドポイントも同じ
    @require_active()
    async def async_endpoint(x: int):
        return x * 2

    assert inspect.iscoroutinefunction(async_endpoint)
    assert asyncio.run(cast(Any, async_endpoint)(3, _require_roles_current_staff=viewer)) == 6

    # ロール名の誤り・空・二重適用は定義時にエラー
    with pytest.raises(ValueError):
        require_roles(["releasr"])
    with pytest.raises(ValueError):
        require_roles([])
    with pytest.raises(TypeError):
        require_active()(endpoint)


def test_get_current_staff_reads_the_header(db):
    staff = make_staff(db, RoleCode.VIEWER, status=StaffStatus.SUSPENDED)
    assert get_current_staff(x_staff_id=staff.id, db=db).id == staff.id  # 状態は問わない（判定は require_*）
    with pytest.raises(UnauthorizedError, match="required"):
        get_current_staff(x_staff_id=None, db=db)
    with pytest.raises(UnauthorizedError, match="does not exist"):
        get_current_staff(x_staff_id=999_999_999, db=db)


def test_staff_endpoints_declare_the_staff_header():
    from main import app

    paths = app.openapi()["paths"]
    for path, method in (("/staff/", "get"), ("/staff/", "post"), ("/staff/{staff_id}/roles", "put"), ("/staff/roles", "get"), ("/staff/me", "get")):
        params = {p["name"]: p for p in paths[path][method].get("parameters", [])}
        assert "x-staff-id" in params, (path, method)
        assert "_require_roles_current_staff" not in params
        assert {"401", "403"} <= set(paths[path][method]["responses"])
    # 既存の plan のエンドポイントは対象外（変更なし）
    assert "x-staff-id" not in {p["name"] for p in paths["/plans/templates"]["get"].get("parameters", [])}
