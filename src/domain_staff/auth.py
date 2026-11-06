"""
操作している職員の特定と、ロールによる権限の判定。

操作する職員はログインのセッション（Cookie ops_session。LG.2）で決まります。X-Staff-Id ヘッダーは廃止しました。
機械（信号を送るスクリプト）は API キー（Authorization: Bearer ops_...）を使い、キーは API_KEY_ROUTES のエンドポイントでだけ通ります。
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
import functools
import inspect
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, TypeVar

from fastapi import Depends, Request
from fastapi.security import APIKeyCookie, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from src.core.error_codes import ErrorCode
from src.core.exceptions import ForbiddenError, UnauthorizedError
from src.domain_staff import credentials
from src.domain_staff.models import RoleCode, StaffStatus, StaffUser
from src.infrastructure.database import get_db

Endpoint = TypeVar("Endpoint", bound=Callable[..., Any])

# デコレーターがエンドポイントに追加する引数の名前（エンドポイント自身の引数と重ならない名前）
_GUARD_PARAM = "_require_roles_current_staff"


SESSION_COOKIE = "ops_session"
# API キー（Authorization: Bearer）を受け付けるエンドポイント（メソッド, パス）。それ以外ではキーは使えない
API_KEY_ROUTES = {("POST", "/ope/signals")}
# 仮パスワードのままでも使えるエンドポイント
PASSWORD_CHANGE_ROUTES = {("GET", "/staff/me"), ("POST", "/auth/password"), ("POST", "/auth/logout")}

# OpenAPI に認証の方法を載せる（値の取り出しは request から行う。auto_error=False で、無いときの扱いはここで決める）
_session_scheme = APIKeyCookie(name=SESSION_COOKIE, auto_error=False, description="login session (POST /auth/login sets it)")
_bearer_scheme = HTTPBearer(auto_error=False, description="API key of a signal sender (POST /ope/signals only)")


def _now() -> datetime:
    return datetime.now(timezone.utc)


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
    ログインのセッション、または（API_KEY_ROUTES でだけ）信号の送信の API キーで決まる。無ければ 401。
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
            staff = db.get(StaffUser, api_key.staff_user_id)
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


def check_roles(staff: StaffUser, required: frozenset[RoleCode] | None) -> None:
    """
    職員が操作できるか判定します。active でなければ拒否し、required があればそのどれか1つを持っている必要があります。
    """
    if staff.status != StaffStatus.ACTIVE:
        raise ForbiddenError(
            f"Staff user '{staff.login_id}' is {staff.status.value}.", error_code=ErrorCode.STAFF_NOT_ACTIVE
        )
    if required is not None and not required.intersection(staff.role_codes):
        raise ForbiddenError(f"This operation requires one of the roles: {sorted(r.value for r in required)}.")


def _guard(required: frozenset[RoleCode] | None) -> Callable[[Endpoint], Endpoint]:
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
                check_roles(kwargs.pop(_GUARD_PARAM), required)
                return await endpoint(*args, **kwargs)
            wrapper: Any = async_wrapper
        else:
            @functools.wraps(endpoint)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                check_roles(kwargs.pop(_GUARD_PARAM), required)
                return endpoint(*args, **kwargs)
            wrapper = sync_wrapper

        wrapper.__signature__ = signature.replace(parameters=[*signature.parameters.values(), guard_param])
        # どのロールが必要か（require_active は None）。テストでガードの付け忘れ・付け違いを確かめるための目印
        wrapper.__required_roles__ = required
        return wrapper
    return decorator


def require_roles(roles: Iterable[str | RoleCode]) -> Callable[[Endpoint], Endpoint]:
    """
    指定したロールのどれか1つを持つ、active な職員だけが呼べるエンドポイントにします。
    ロール名の誤りは起動時（インポート時）に ValueError になります。
    """
    required = frozenset(RoleCode(r) for r in roles)
    if not required:
        raise ValueError("require_roles needs at least one role (use require_active for any active staff user).")
    return _guard(required)


def require_active() -> Callable[[Endpoint], Endpoint]:
    """ロールを問わず、active な職員だけが呼べるエンドポイントにします。"""
    return _guard(None)
