# tests/test_login.py
"""
ログインのサービス（LG.2: src/domain_staff/services.py）のテスト: ログイン（失敗の回数はロールバックされずに残る）・
ログアウト・本人のパスワードの変更（今のセッションは残る）・システム管理者の仮パスワード（自分には使えない）・
利用停止でセッションが終わる・API キーの発行 / 一覧 / 取り消し。
"""
import uuid
from datetime import datetime, timezone

import pytest

from src.core.error_codes import ErrorCode
from src.core.exceptions import BadRequestError, ForbiddenError, UnauthorizedError
from src.domain_staff import credentials, services
from src.domain_staff.models import RoleCode, StaffStatus, StaffUser
from src.domain_staff.schemas import StaffCreate, StaffResponse
from src.infrastructure.database import SessionLocal

PASSWORD = "first password 1"


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def staff_with_password(db, *roles: RoleCode, password: str | None = PASSWORD) -> StaffUser:
    staff = services.create_staff(
        db, StaffCreate(login_id="lgs-" + uuid.uuid4().hex[:10], name="login service", roles=list(roles or (RoleCode.OPERATOR,))),
        actor=None, status=StaffStatus.ACTIVE,
    )
    if password is not None:
        credentials.set_password(db, staff, password, temporary=False, now=datetime.now(timezone.utc))
        db.commit()
    return staff


def now():
    return datetime.now(timezone.utc)


def test_login_logout_and_failures_are_kept(db):
    staff = staff_with_password(db)
    logged_in, new = services.login(db, staff.login_id, PASSWORD)
    assert logged_in.id == staff.id and credentials.resolve_session(db, new.token, now=now()) is not None
    assert StaffResponse.model_validate(logged_in).has_password

    with pytest.raises(UnauthorizedError):
        services.login(db, staff.login_id, "wrong password")
    db.rollback()  # ルーターの外のロールバックでも、失敗の回数は残る（login が commit 済み）
    db.expire_all()
    assert db.get(StaffUser, staff.id).failed_logins == 1

    services.logout(db, new.token)
    assert credentials.resolve_session(db, new.token, now=now()) is None
    services.logout(db, None)  # ログインしていなくてもよい


def test_change_own_password_keeps_this_session_only(db):
    staff = staff_with_password(db)
    _, here = services.login(db, staff.login_id, PASSWORD)
    _, elsewhere = services.login(db, staff.login_id, PASSWORD)
    with pytest.raises(UnauthorizedError) as e:
        services.change_own_password(db, staff, "not my password", "second password 2", here.session.id)
    assert e.value.error_code == ErrorCode.INVALID_CREDENTIALS
    with pytest.raises(BadRequestError, match="different"):
        services.change_own_password(db, staff, PASSWORD, PASSWORD, here.session.id)
    with pytest.raises(BadRequestError):
        services.change_own_password(db, staff, PASSWORD, "short", here.session.id)

    changed = services.change_own_password(db, staff, PASSWORD, "second password 2", here.session.id)
    assert not changed.must_change_password
    assert credentials.resolve_session(db, here.token, now=now()) is not None
    assert credentials.resolve_session(db, elsewhere.token, now=now()) is None
    services.login(db, staff.login_id, "second password 2")


def test_admin_sets_a_temporary_password_and_suspending_ends_sessions(db):
    admin = staff_with_password(db, RoleCode.SYSTEM_ADMIN)
    staff = staff_with_password(db, password=None)
    assert not StaffResponse.model_validate(staff).has_password
    with pytest.raises(UnauthorizedError):
        services.login(db, staff.login_id, PASSWORD)  # パスワードが無い職員はログインできない

    with pytest.raises(BadRequestError, match="own password"):
        services.set_temporary_password(db, admin.id, "temporary pw 9", admin)
    updated = services.set_temporary_password(db, staff.id, "temporary pw 9", admin)
    assert updated.must_change_password and updated.has_password
    logged_in, new = services.login(db, staff.login_id, "temporary pw 9")
    assert logged_in.must_change_password
    services.change_own_password(db, logged_in, "temporary pw 9", "my own password 3", new.session.id)
    assert not db.get(StaffUser, staff.id).must_change_password

    services.set_staff_status(db, staff.id, StaffStatus.SUSPENDED, admin)
    assert credentials.resolve_session(db, new.token, now=now()) is None
    with pytest.raises(ForbiddenError) as e:
        services.login(db, staff.login_id, "my own password 3")
    assert e.value.error_code == ErrorCode.STAFF_NOT_ACTIVE


def test_api_keys_issue_list_revoke(db):
    admin = staff_with_password(db, RoleCode.SYSTEM_ADMIN)
    sender = staff_with_password(db, RoleCode.SIGNAL_SENDER, password=None)
    first = services.issue_api_key(db, sender.id, "SFTP 監視", admin)
    second = services.issue_api_key(db, sender.id, "予備", admin)
    listed = services.list_api_keys(db, sender.id)
    assert [k.id for k in listed] == [second.api_key.id, first.api_key.id]
    assert all(not hasattr(k, "key") for k in listed)

    revoked = services.revoke_api_key(db, first.api_key.id, admin)
    assert revoked.revoked_at is not None and revoked.revoked_by_id == admin.id
    assert credentials.resolve_api_key(db, first.key, now=now()) is None
    assert credentials.resolve_api_key(db, second.key, now=now()) is not None
    with pytest.raises(BadRequestError, match="signal_sender"):
        services.issue_api_key(db, admin.id, "x", admin)
