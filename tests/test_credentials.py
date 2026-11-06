# tests/test_credentials.py
"""
ログインの部品（LG.1: src/domain_staff/credentials.py）のテスト: パスワードのハッシュと決まり、仮パスワード、
ログインの失敗の回数とロック（5 回で 15 分）、セッション（最後の利用から 12 時間・最長 7 日・終える）、
信号の送信用の API キー（発行・確認・取り消し）。日時は now を渡して決める。
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from src.core.error_codes import ErrorCode
from src.core.exceptions import BadRequestError, ForbiddenError, UnauthorizedError
from src.domain_staff import credentials as cred
from src.domain_staff import services as staff_services
from src.domain_staff.models import RoleCode, StaffSession, StaffStatus
from src.domain_staff.schemas import StaffCreate
from src.infrastructure.database import SessionLocal

T0 = datetime(2026, 11, 6, 9, 0, tzinfo=timezone.utc)
PASSWORD = "correct horse 42"


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def new_staff(db, roles=(RoleCode.OPERATOR,), status=StaffStatus.ACTIVE, password: str | None = PASSWORD):
    staff = staff_services.create_staff(
        db, StaffCreate(login_id="lg-" + uuid.uuid4().hex[:10], name="login tester", roles=list(roles)), actor=None, status=status,
    )
    if password is not None:
        cred.set_password(db, staff, password, temporary=False, now=T0)
        db.commit()
    return staff


def test_password_hash_and_rules(db):
    stored = cred.hash_password(PASSWORD)
    assert stored.startswith("scrypt$") and PASSWORD not in stored
    assert cred.hash_password(PASSWORD) != stored  # 塩が毎回違う
    assert cred.verify_password_hash(PASSWORD, stored)
    assert not cred.verify_password_hash(PASSWORD + "x", stored)
    assert not cred.verify_password_hash(PASSWORD, None) and not cred.verify_password_hash(PASSWORD, "garbage")

    staff = new_staff(db, password=None)
    for bad in ("short", " leading-space", "x" * 129, staff.login_id.upper()):
        with pytest.raises(BadRequestError):
            cred.set_password(db, staff, bad, temporary=False, now=T0)

    cred.set_password(db, staff, PASSWORD, temporary=True, now=T0)
    assert staff.must_change_password and staff.password_changed_at == T0


def test_login_counts_failures_and_locks_for_15_minutes(db):
    staff = new_staff(db)
    with pytest.raises(UnauthorizedError) as e:
        cred.authenticate(db, "nobody-" + uuid.uuid4().hex, PASSWORD, now=T0)
    assert e.value.error_code == ErrorCode.INVALID_CREDENTIALS

    for n in range(1, 5):
        with pytest.raises(UnauthorizedError):
            cred.authenticate(db, staff.login_id, "wrong password", now=T0)
        assert staff.failed_logins == n
    assert cred.authenticate(db, f"  {staff.login_id} ", PASSWORD, now=T0).id == staff.id  # 成功で 0 に戻る
    assert staff.failed_logins == 0

    for _ in range(5):
        with pytest.raises(UnauthorizedError):
            cred.authenticate(db, staff.login_id, "wrong password", now=T0)
    assert staff.locked_until == T0 + cred.LOCK_DURATION
    with pytest.raises(ForbiddenError) as locked:
        cred.authenticate(db, staff.login_id, PASSWORD, now=T0 + timedelta(minutes=14))  # 正しくてもロック中
    assert locked.value.error_code == ErrorCode.ACCOUNT_LOCKED and "JST" in locked.value.message
    assert cred.authenticate(db, staff.login_id, PASSWORD, now=T0 + timedelta(minutes=15)).locked_until is None

    # 仮パスワードを設定すると、ロックも解ける
    for _ in range(5):
        with pytest.raises(UnauthorizedError):
            cred.authenticate(db, staff.login_id, "wrong password", now=T0)
    cred.set_password(db, staff, "new temporary 1", temporary=True, now=T0)
    assert cred.authenticate(db, staff.login_id, "new temporary 1", now=T0).must_change_password


def test_inactive_staff_and_staff_without_password_cannot_log_in(db):
    suspended = new_staff(db, status=StaffStatus.SUSPENDED)
    with pytest.raises(ForbiddenError) as e:
        cred.authenticate(db, suspended.login_id, PASSWORD, now=T0)
    assert e.value.error_code == ErrorCode.STAFF_NOT_ACTIVE
    no_password = new_staff(db, password=None)
    with pytest.raises(UnauthorizedError):
        cred.authenticate(db, no_password.login_id, PASSWORD, now=T0)


def test_sessions_expire_after_12_idle_hours_or_7_days(db):
    staff = new_staff(db)
    s = cred.create_session(db, staff, now=T0)
    assert len(s.token) >= 40 and s.session.token_hash != s.token
    assert cred.resolve_session(db, s.token, now=T0 + timedelta(seconds=30)) is not None
    assert s.session.last_seen_at == T0  # 1 分に 1 回しか更新しない
    assert cred.resolve_session(db, s.token, now=T0 + timedelta(hours=11)) is not None
    assert s.session.last_seen_at == T0 + timedelta(hours=11)
    assert cred.resolve_session(db, s.token, now=T0 + timedelta(hours=23)) is None  # 最後の利用から 12 時間
    assert cred.resolve_session(db, "not-a-token", now=T0) is None and cred.resolve_session(db, None, now=T0) is None

    # 使い続けても最長 7 日
    long = cred.create_session(db, staff, now=T0)
    t = T0
    while t < T0 + timedelta(days=7) - timedelta(hours=10):
        t += timedelta(hours=10)
        assert cred.resolve_session(db, long.token, now=t) is not None
    assert cred.resolve_session(db, long.token, now=T0 + timedelta(days=7)) is None


def test_logout_and_password_changes_end_sessions(db):
    staff = new_staff(db)
    a = cred.create_session(db, staff, now=T0)
    b = cred.create_session(db, staff, now=T0)
    c = cred.create_session(db, staff, now=T0)
    assert cred.revoke_session(db, a.token, now=T0) and not cred.revoke_session(db, a.token, now=T0)
    assert cred.resolve_session(db, a.token, now=T0) is None

    # 本人が変えたとき: 今のセッション（b）だけ残る
    cred.set_password(db, staff, "changed by me 1", temporary=False, now=T0, keep_session_id=b.session.id)
    db.flush()
    db.expire_all()
    assert cred.resolve_session(db, b.token, now=T0) is not None and cred.resolve_session(db, c.token, now=T0) is None

    # 停止など: すべて終える
    assert cred.revoke_all_sessions(db, staff.id, now=T0) == 1
    assert db.get(StaffSession, b.session.id).revoked_at == T0


def test_api_keys_only_for_signal_senders(db):
    admin = new_staff(db, roles=(RoleCode.SYSTEM_ADMIN,))
    operator = new_staff(db)
    with pytest.raises(BadRequestError, match="signal_sender"):
        cred.issue_api_key(db, operator, "watcher", actor=admin, now=T0)
    sender = new_staff(db, roles=(RoleCode.SIGNAL_SENDER,), password=None)
    with pytest.raises(BadRequestError, match="label"):
        cred.issue_api_key(db, sender, "  ", actor=admin, now=T0)

    issued = cred.issue_api_key(db, sender, " SFTP 監視 ", actor=admin, now=T0)
    assert issued.key.startswith("ops_") and issued.api_key.key_prefix == issued.key[:10]
    assert issued.api_key.label == "SFTP 監視" and issued.api_key.created_by_id == admin.id
    found = cred.resolve_api_key(db, issued.key, now=T0 + timedelta(hours=1))
    assert found is not None and found.staff_user_id == sender.id and found.last_used_at == T0 + timedelta(hours=1)
    assert cred.resolve_api_key(db, "ops_wrong", now=T0) is None and cred.resolve_api_key(db, issued.key[4:], now=T0) is None

    cred.revoke_api_key(db, issued.api_key, actor=admin, now=T0)
    assert cred.resolve_api_key(db, issued.key, now=T0) is None
    with pytest.raises(BadRequestError, match="already revoked"):
        cred.revoke_api_key(db, issued.api_key, actor=admin, now=T0)
