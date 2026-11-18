"""
操作している職員の特定と、ロールによる権限の判定。

操作する職員はログインのセッション（Cookie ops_session。LG.2）で決まります。X-Staff-Id ヘッダーは廃止しました。
機械（信号を送るスクリプト・アラートを読むアダプター）は API キー（Authorization: Bearer ops_...）を使います。キーは、
その職員の機械用のロールに許された呼び出し（API_KEY_ROUTES_BY_ROLE）でだけ通ります。
すべての API は require_login（main.py でルーターごとに付ける）でログインを求めます。仮パスワードのままの職員は、
自分の情報・パスワードの変更・ログアウトしかできません。将来 SSO にするときも get_current_staff だけを置き換えます。

使い方（ルーターのデコレーターの内側に付ける）:

    @router.post("/orders/{order_id}/release", ...)
    @require_roles(["releaser", "admin"])
    def release_endpoint(order_id: int, db: Session = Depends(get_db)):
        ...

操作した職員が必要な場合は、エンドポイントの引数に `current_staff: StaffUser = Depends(get_current_staff)` を追加します
（同じリクエスト内では1回だけ取得されます）。
"""
import enum
import functools
import inspect
from typing import Any, TypeVar
from collections.abc import Callable, Iterable

from fastapi import Depends, Request
from fastapi.security import APIKeyCookie, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from src.core.error_codes import ErrorCode
from src.core.exceptions import ForbiddenError, UnauthorizedError
from src.domain_staff import credentials
from src.domain_staff.models import RoleCode, StaffStatus, StaffUser
from src.domain_staff.scope import ClientScope
from src.infrastructure.database import get_db
from src.core.clock import utc_now as _now

Endpoint = TypeVar("Endpoint", bound=Callable[..., Any])

# デコレーターがエンドポイントに追加する引数の名前（エンドポイント自身の引数と重ならない名前）
_GUARD_PARAM = "_require_roles_current_staff"


SESSION_COOKIE = "ops_session"
# API キー（Authorization: Bearer）で呼べるエンドポイント（メソッド, パス）を、機械用のロールごとに。
# キーは、その職員が持つロールのどれかに許された呼び出しでだけ通る（それ以外は 401。2026-11-12: 読み取り専用のキー）
API_KEY_ROUTES_BY_ROLE: dict[RoleCode, frozenset[tuple[str, str]]] = {
    RoleCode.SIGNAL_SENDER: frozenset({("POST", "/ope/signals")}),
    # mfa のアダプターなど: 日々のアラート（まもなく・遅れ）、これから始める作業の時刻表（並行稼働 P.1）、日次の状況を読む。
    # キーが通るかの確認に自分の情報
    RoleCode.SYSTEM_AGENT: frozenset({
        ("GET", "/ops/attention"), ("GET", "/ops/schedule-feed"), ("GET", "/ops/daily"), ("GET", "/staff/me"),
    }),
}
# どれかのロールでキーが使える呼び出し（ここに無い呼び出しでは、キーを調べもしない）
API_KEY_ROUTES = frozenset().union(*API_KEY_ROUTES_BY_ROLE.values())


def api_key_allows(staff: StaffUser, route: tuple[str, str]) -> bool:
    """この職員の API キーで、この呼び出しができるか（持っている機械用のロールのどれかが許していること）"""
    return any(route in API_KEY_ROUTES_BY_ROLE.get(role, frozenset()) for role in staff.role_codes)
# 仮パスワードのままでも使えるエンドポイント
PASSWORD_CHANGE_ROUTES = {("GET", "/staff/me"), ("POST", "/auth/password"), ("POST", "/auth/logout")}

# OpenAPI に認証の方法を載せる（値の取り出しは request から行う。auto_error=False で、無いときの扱いはここで決める）
_session_scheme = APIKeyCookie(name=SESSION_COOKIE, auto_error=False, description="login session (POST /auth/login sets it)")
_bearer_scheme = HTTPBearer(auto_error=False, description="API key of a signal sender (POST /ope/signals only)")


def _route_key(request: Request) -> tuple[str, str]:
    route = request.scope.get("route")
    return request.method, getattr(route, "path", request.url.path)


def get_current_staff(
    request: Request,
    db: Session = Depends(get_db),
    session_token: str | None = Depends(_session_scheme),
    bearer: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> StaffUser:
    """
    操作している職員（状態は問わない。利用できるかどうかの判定は require_login / require_roles / require_active で行う）。
    ログインのセッション、または API キー（その職員の機械用のロールに許された呼び出しでだけ）で決まる。無ければ 401。
    """
    cached = getattr(request.state, "staff", None)
    if cached is not None:
        return cached
    now = _now()
    staff: StaffUser | None = None
    session = credentials.resolve_session(db, session_token, now=now)
    if session is not None:
        staff = db.get(StaffUser, session.staff_user_id)
        request.state.session_id = session.id
    elif bearer is not None and _route_key(request) in API_KEY_ROUTES:
        api_key = credentials.resolve_api_key(db, bearer.credentials, now=now)
        if api_key is not None:
            key_owner = db.get(StaffUser, api_key.staff_user_id)
            # 信号の送信のキーでアラートは読めない・読み取りのキーで信号は送れない（ロールごとの呼び出しだけ）
            if key_owner is not None and api_key_allows(key_owner, _route_key(request)):
                staff = key_owner
                request.state.api_key_id = api_key.id
    if staff is None:
        db.rollback()
        raise UnauthorizedError("Login required.")
    db.commit()  # 最後の利用日時（セッション・キー）を残す（参照だけのエンドポイントは commit しないため）
    request.state.staff = staff
    return staff


def current_session_id(request: Request) -> int | None:
    """このリクエストのセッションの ID（API キーで来たときは None）"""
    return getattr(request.state, "session_id", None)


def require_login(request: Request, staff: StaffUser = Depends(get_current_staff)) -> StaffUser:
    """
    すべての API に付ける（main.py）。ログインしていて有効な職員であること（自分の情報だけは状態を問わない）。
    仮パスワードのままなら、PASSWORD_CHANGE_ROUTES 以外は 403 PASSWORD_CHANGE_REQUIRED。
    """
    route = _route_key(request)
    if staff.status != StaffStatus.ACTIVE and route != ("GET", "/staff/me"):
        raise ForbiddenError(f"Staff user '{staff.login_id}' is {staff.status.value}.", error_code=ErrorCode.STAFF_NOT_ACTIVE)
    if staff.must_change_password and route not in PASSWORD_CHANGE_ROUTES:
        raise ForbiddenError("Change your temporary password first (POST /auth/password).", error_code=ErrorCode.PASSWORD_CHANGE_REQUIRED)
    return staff


def check_roles(staff: StaffUser, required: frozenset[RoleCode] | None, *, all_clients: bool = False) -> None:
    """
    職員が操作できるか判定します。active でなければ拒否し、required があればそのどれか1つを持っている必要があります。
    all_clients なら、顧客の範囲が無い（全顧客を扱える）職員だけ（全顧客に共通の設定。403 ALL_CLIENTS_REQUIRED）。
    """
    if staff.status != StaffStatus.ACTIVE:
        raise ForbiddenError(
            f"Staff user '{staff.login_id}' is {staff.status.value}.", error_code=ErrorCode.STAFF_NOT_ACTIVE
        )
    if required is not None and not required.intersection(staff.role_codes):
        raise ForbiddenError(f"This operation requires one of the roles: {sorted(r.value for r in required)}.")
    if all_clients and staff.client_codes:
        raise ForbiddenError(
            "This operation is shared by all clients: staff limited to some clients cannot use it.",
            error_code=ErrorCode.ALL_CLIENTS_REQUIRED,
        )


def _guard(required: frozenset[RoleCode] | None, all_clients: bool = False) -> Callable[[Endpoint], Endpoint]:
    def decorator(endpoint: Endpoint) -> Endpoint:
        signature = inspect.signature(endpoint)
        if _GUARD_PARAM in signature.parameters:
            raise TypeError(f"{endpoint.__name__}: require_roles / require_active can only be applied once.")
        # FastAPI が職員を注入できるよう、シグネチャにキーワード専用の引数を足す（エンドポイント本体には渡さない）
        guard_param = inspect.Parameter(
            _GUARD_PARAM, inspect.Parameter.KEYWORD_ONLY, default=Depends(get_current_staff), annotation=StaffUser
        )

        if inspect.iscoroutinefunction(endpoint):
            @functools.wraps(endpoint)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                check_roles(kwargs.pop(_GUARD_PARAM), required, all_clients=all_clients)
                return await endpoint(*args, **kwargs)
            wrapper: Any = async_wrapper
        else:
            @functools.wraps(endpoint)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                check_roles(kwargs.pop(_GUARD_PARAM), required, all_clients=all_clients)
                return endpoint(*args, **kwargs)
            wrapper = sync_wrapper

        wrapper.__signature__ = signature.replace(parameters=[*signature.parameters.values(), guard_param])
        # どのロールが必要か（require_active は None）。テストでガードの付け忘れ・付け違いを確かめるための目印
        wrapper.__required_roles__ = required
        if all_clients:
            wrapper.__client_scope__ = ScopeKind.ALL_CLIENTS
        return wrapper
    return decorator


def require_roles(roles: Iterable[str | RoleCode], *, all_clients: bool = False) -> Callable[[Endpoint], Endpoint]:
    """
    指定したロールのどれか1つを持つ、active な職員だけが呼べるエンドポイントにします。
    all_clients=True: 全顧客に共通の設定。顧客の範囲がある職員は 403（ScopeKind.ALL_CLIENTS の目印も付く）。
    ロール名の誤りは起動時（インポート時）に ValueError になります。
    """
    required = frozenset(RoleCode(r) for r in roles)
    if not required:
        raise ValueError("require_roles needs at least one role (use require_active for any active staff user).")
    return _guard(required, all_clients)


def require_active() -> Callable[[Endpoint], Endpoint]:
    """ロールを問わず、active な職員だけが呼べるエンドポイントにします。"""
    return _guard(None)


# ==========================================
# 顧客の範囲（src/domain_staff/scope.py）
# ==========================================
def get_client_scope(staff: StaffUser = Depends(get_current_staff)) -> ClientScope:
    """操作している職員の顧客の範囲（担当顧客が無ければ全顧客）。エンドポイントの引数に Depends で付ける"""
    return ClientScope.of(staff.client_codes)


class ScopeKind(str, enum.Enum):
    """エンドポイントが顧客の範囲をどう扱うか（すべてのエンドポイントに付ける。tests/test_client_scope.py が確かめる）"""
    FILTERED = "filtered"        # 一覧: 範囲の中の顧客のものだけを返す
    CHECKED = "checked"          # 1件の参照・操作: 対象の顧客が範囲の外なら 404
    SHARED = "shared"            # 顧客に関係しない（自分の情報・ログイン・ロール・営業日カレンダーの参照）
    ALL_CLIENTS = "all_clients"  # 全顧客に共通の設定: 範囲のある職員は 403（require_roles(..., all_clients=True) が付ける）


def client_scope(kind: ScopeKind | str) -> Callable[[Endpoint], Endpoint]:
    """
    エンドポイントに、顧客の範囲の扱い（ScopeKind）の目印を付けます（目印だけ。絞り込み・確認はサービスが ClientScope で行う）。
    ALL_CLIENTS は require_roles(..., all_clients=True) で付けてください（判定も行うため）。
    """
    scope_kind = ScopeKind(kind)
    if scope_kind == ScopeKind.ALL_CLIENTS:
        raise ValueError("use require_roles(..., all_clients=True) for endpoints shared by all clients.")

    def decorator(endpoint: Endpoint) -> Endpoint:
        if hasattr(endpoint, "__client_scope__"):
            raise TypeError(f"{endpoint.__name__}: the client scope is already declared.")
        setattr(endpoint, "__client_scope__", scope_kind)
        return endpoint
    return decorator
