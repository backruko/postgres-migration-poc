"""
操作している職員の特定と、ロールによる権限の判定。

ログインはまだ無いため、操作する職員は信頼できるヘッダー X-Staff-Id で指定します（開発用）。
本番の認証（セッション・SSO 等）を入れるときは get_current_staff だけを置き換えます（戻り値は同じ StaffUser）。

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
from typing import Any, Callable, Iterable, TypeVar

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from src.core.error_codes import ErrorCode
from src.core.exceptions import ForbiddenError, UnauthorizedError
from src.domain_staff.models import RoleCode, StaffStatus, StaffUser
from src.infrastructure.database import get_db
from src.infrastructure.repositories import staff_user_repo

Endpoint = TypeVar("Endpoint", bound=Callable[..., Any])

# デコレーターがエンドポイントに追加する引数の名前（エンドポイント自身の引数と重ならない名前）
_GUARD_PARAM = "_require_roles_current_staff"


def get_current_staff(
    x_staff_id: int | None = Header(default=None, description="acting staff user id (development only, until login exists)"),
    db: Session = Depends(get_db),
) -> StaffUser:
    """操作している職員（状態は問わない。利用できるかどうかの判定は require_roles / require_active で行う）"""
    if x_staff_id is None:
        raise UnauthorizedError("X-Staff-Id header is required.")
    staff = staff_user_repo.get(db, id=x_staff_id)
    if staff is None:
        raise UnauthorizedError(f"Staff user (id={x_staff_id}) does not exist.")
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
