"""
ログインの部品（LG.1）: パスワード・セッション・API キー。エンドポイントへの組み込みは LG.2。

- パスワード: 標準ライブラリの scrypt（塩付き）。保存形式 "scrypt$n$r$p$<塩>$<ハッシュ>"（どちらも base64）
- セッション・API キー: 推測できない乱数（secrets）を渡し、DB には SHA-256 だけを持つ（値が十分に長いので塩は不要）
- 5 回続けて失敗すると 15 分ログインできない。成功で回数は 0 に戻る
- セッションは最後の利用から 12 時間で切れ、最長でも 7 日。パスワードを設定・変更すると、その職員のセッションは終わる

この関数群は commit しない（呼び出し側のサービスが commit する）。
"""
import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from src.core.error_codes import ErrorCode
from src.core.exceptions import BadRequestError, ForbiddenError, UnauthorizedError
from src.domain_staff.models import RoleCode, StaffApiKey, StaffSession, StaffStatus, StaffUser

# --- 決まり（設計書の決定。2026-11-06） ---
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128
MAX_FAILED_LOGINS = 5
LOCK_DURATION = timedelta(minutes=15)
SESSION_IDLE = timedelta(hours=12)
SESSION_MAX_AGE = timedelta(days=7)
SESSION_TOUCH_INTERVAL = timedelta(minutes=1)  # 最後の利用日時の更新は 1 分に 1 回まで（毎リクエストの書き込みを避ける）
API_KEY_PREFIX = "ops_"

# scrypt の強さ（n=2^14, r=8, p=1: 約 16 MB・数十ミリ秒）
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1

INVALID_LOGIN_MESSAGE = "Login ID or password is wrong."  # どちらが違うかは言わない（画面はエラーコードで日本語にする）
LOCAL_TIMEZONE = ZoneInfo("Asia/Tokyo")


# ==========================================
# パスワード
# ==========================================
def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password_hash(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(
            password.encode("utf-8"), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=len(expected)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def password_problem(password: str, staff: StaffUser) -> str | None:
    """パスワードとして使えない理由（なければ None）"""
    if len(password) < PASSWORD_MIN_LENGTH:
        return f"Password must be at least {PASSWORD_MIN_LENGTH} characters."
    if len(password) > PASSWORD_MAX_LENGTH:
        return f"Password must be at most {PASSWORD_MAX_LENGTH} characters."
    if password.strip() != password:
        return "Password must not start or end with a space."
    if password.lower() == staff.login_id.lower():
        return "Password must not be the same as the login ID."
    return None


def set_password(
    db: Session, staff: StaffUser, password: str, *, temporary: bool, now: datetime, keep_session_id: int | None = None,
) -> None:
    """
    パスワードを設定します。temporary=True（システム管理者が設定した仮パスワード）なら、次のログインで本人が変える必要がある。
    ロックと失敗回数は解除し、その職員のセッションは終える（keep_session_id のセッションだけ残せる: 本人が自分で変えたとき）。
    """
    problem = password_problem(password, staff)
    if problem:
        raise BadRequestError(problem)
    staff.password_hash = hash_password(password)
    staff.must_change_password = temporary
    staff.failed_logins = 0
    staff.locked_until = None
    staff.password_changed_at = now
    revoke_all_sessions(db, staff.id, now=now, except_session_id=keep_session_id)


def authenticate(db: Session, login_id: str, password: str, *, now: datetime) -> StaffUser:
    """
    ログイン ID とパスワードを確かめます。失敗は 401 INVALID_CREDENTIALS（どちらが違うかは言わない）、
    ロック中は 403 ACCOUNT_LOCKED、利用開始前・停止中は 403 STAFF_NOT_ACTIVE。失敗を数え、5 回目で 15 分ロックする。
    """
    staff = db.scalars(select(StaffUser).where(StaffUser.login_id == login_id.strip()).with_for_update()).first()
    if staff is None:
        hash_password(password)  # 職員がいない場合も同じくらい時間をかける（ログイン ID の有無を時間で見分けにくくする）
        raise UnauthorizedError(INVALID_LOGIN_MESSAGE, error_code=ErrorCode.INVALID_CREDENTIALS)
    if staff.locked_until is not None and staff.locked_until > now:
        raise ForbiddenError(
            f"Too many failed logins; try again after {staff.locked_until.astimezone(LOCAL_TIMEZONE):%H:%M} JST.",
            error_code=ErrorCode.ACCOUNT_LOCKED,
        )
    if not verify_password_hash(password, staff.password_hash):
        staff.failed_logins += 1
        if staff.failed_logins >= MAX_FAILED_LOGINS:
            staff.locked_until = now + LOCK_DURATION
            staff.failed_logins = 0
        db.flush()
        raise UnauthorizedError(INVALID_LOGIN_MESSAGE, error_code=ErrorCode.INVALID_CREDENTIALS)
    if staff.status != StaffStatus.ACTIVE:
        raise ForbiddenError(f"Staff user '{staff.login_id}' is {staff.status.value}.", error_code=ErrorCode.STAFF_NOT_ACTIVE)
    staff.failed_logins = 0
    staff.locked_until = None
    db.flush()
    return staff


# ==========================================
# セッション
# ==========================================
def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass
class NewSession:
    token: str  # Cookie に入れる値（DB には残らない）
    session: StaffSession


def create_session(db: Session, staff: StaffUser, *, now: datetime) -> NewSession:
    token = secrets.token_urlsafe(32)
    session = StaffSession(
        staff_user_id=staff.id, token_hash=_sha256(token), created_at=now, last_seen_at=now, expires_at=now + SESSION_MAX_AGE,
    )
    db.add(session)
    db.flush()
    return NewSession(token=token, session=session)


def resolve_session(db: Session, token: str | None, *, now: datetime) -> StaffSession | None:
    """有効なセッション（終えていない・最後の利用から 12 時間以内・最長期限の前）。使ったことを記録する（1 分に 1 回まで）"""
    if not token:
        return None
    session = db.scalars(select(StaffSession).where(StaffSession.token_hash == _sha256(token))).first()
    if session is None or session.revoked_at is not None:
        return None
    if now >= session.expires_at or now >= session.last_seen_at + SESSION_IDLE:
        return None
    if now - session.last_seen_at >= SESSION_TOUCH_INTERVAL:
        session.last_seen_at = now
        db.flush()
    return session


def revoke_session(db: Session, token: str | None, *, now: datetime) -> bool:
    """ログアウト。終えたら True"""
    if not token:
        return False
    session = db.scalars(select(StaffSession).where(StaffSession.token_hash == _sha256(token))).first()
    if session is None or session.revoked_at is not None:
        return False
    session.revoked_at = now
    db.flush()
    return True


def revoke_all_sessions(db: Session, staff_id: int, *, now: datetime, except_session_id: int | None = None) -> int:
    """その職員のセッションをすべて終えます（利用停止・パスワードの設定）。終えた数"""
    stmt = update(StaffSession).where(StaffSession.staff_user_id == staff_id, StaffSession.revoked_at.is_(None))
    if except_session_id is not None:
        stmt = stmt.where(StaffSession.id != except_session_id)
    result = db.execute(stmt.values(revoked_at=now).execution_options(synchronize_session="fetch"))
    return int(getattr(result, "rowcount", 0) or 0)


# ==========================================
# API キー（機械用。信号の送信の職員だけ）
# ==========================================
@dataclass
class NewApiKey:
    key: str  # 発行のときに一度だけ見せる値（DB には残らない）
    api_key: StaffApiKey


def issue_api_key(db: Session, staff: StaffUser, label: str, *, actor: StaffUser | None, now: datetime) -> NewApiKey:
    if RoleCode.SIGNAL_SENDER not in staff.role_codes:
        raise BadRequestError(f"API keys are only for staff with the signal_sender role ('{staff.login_id}' does not have it).")
    label = label.strip()
    if not label:
        raise BadRequestError("A label (what the key is for) is required.")
    key = API_KEY_PREFIX + secrets.token_urlsafe(32)
    api_key = StaffApiKey(
        staff_user_id=staff.id, label=label[:100], key_prefix=key[:10], key_hash=_sha256(key),
        created_by_id=actor.id if actor else None, created_at=now,
    )
    db.add(api_key)
    db.flush()
    return NewApiKey(key=key, api_key=api_key)


def resolve_api_key(db: Session, key: str | None, *, now: datetime) -> StaffApiKey | None:
    """取り消していない API キー。使った日時を記録する"""
    if not key or not key.startswith(API_KEY_PREFIX):
        return None
    api_key = db.scalars(select(StaffApiKey).where(StaffApiKey.key_hash == _sha256(key))).first()
    if api_key is None or api_key.revoked_at is not None:
        return None
    api_key.last_used_at = now
    db.flush()
    return api_key


def revoke_api_key(db: Session, api_key: StaffApiKey, *, actor: StaffUser | None, now: datetime) -> None:
    if api_key.revoked_at is not None:
        raise BadRequestError(f"API key {api_key.key_prefix}… is already revoked.")
    api_key.revoked_at = now
    api_key.revoked_by_id = actor.id if actor else None
    db.flush()
