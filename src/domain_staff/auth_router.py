"""
ログイン・ログアウト・本人のパスワードの変更（LG.2）。

ログインはセッションの Cookie（ops_session: HttpOnly・SameSite=Lax・Path=/。COOKIE_SECURE=1 なら Secure）を設定します。
画面は同じオリジンから API を呼ぶため、SameSite=Lax で別サイトからの書き込み（CSRF）を防げます。
"""
from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from src.core.schemas.error import AUTH_ERRORS, COMMON_ERRORS
from src.domain_staff import credentials
from src.domain_staff.auth import SESSION_COOKIE, current_session_id, require_login
from src.domain_staff.models import StaffUser
from src.domain_staff.schemas import LoginIn, PasswordChangeIn, StaffResponse
from src.domain_staff.services import change_own_password, login, logout
from src.infrastructure.config import settings
from src.infrastructure.database import get_db
from sqlalchemy.orm import Session

router = APIRouter(prefix="/auth", tags=["auth"])

RESPONSES: dict[int | str, dict[str, Any]] = {**COMMON_ERRORS, **AUTH_ERRORS}


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE, token, max_age=int(credentials.SESSION_MAX_AGE.total_seconds()), path="/",
        httponly=True, samesite="lax", secure=settings.cookie_secure,
    )


@router.post("/login", response_model=StaffResponse, responses=RESPONSES)
def login_endpoint(body: LoginIn, response: Response, db: Session = Depends(get_db)):
    """
    ログイン ID とパスワードでログインし、セッションの Cookie を設定します。
    違う: 401 INVALID_CREDENTIALS（5 回続くと 15 分ロック: 403 ACCOUNT_LOCKED）、停止中など: 403 STAFF_NOT_ACTIVE。
    must_change_password が true なら、POST /auth/password で変えるまでほかの操作はできません。
    """
    staff, new = login(db, body.login_id, body.password)
    _set_cookie(response, new.token)
    return staff


@router.post("/logout", status_code=204, responses=RESPONSES)
def logout_endpoint(request: Request, response: Response, db: Session = Depends(get_db)):
    """セッションを終え、Cookie を消します（ログインしていなくても 204）。"""
    logout(db, request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.status_code = 204
    return response


@router.post("/password", response_model=StaffResponse, responses=RESPONSES)
def change_password_endpoint(
    body: PasswordChangeIn, request: Request, db: Session = Depends(get_db), staff: StaffUser = Depends(require_login),
):
    """本人のパスワードを変えます（仮パスワードのままでも使えます）。このセッションは続き、ほかのセッションは終わります。"""
    return change_own_password(db, staff, body.current_password, body.new_password, current_session_id(request))
