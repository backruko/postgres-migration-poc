from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.core.error_codes import ErrorCode
from src.core.exceptions import BadRequestError, DuplicatedRecordError, NotFoundError, UnauthorizedError
from src.domain_staff import credentials
from src.domain_staff.credentials import NewApiKey, NewSession
from src.domain_staff.models import Role, RoleCode, StaffApiKey, StaffStatus, StaffUser
from src.domain_staff.schemas import StaffCreate, StaffUpdate
from src.infrastructure.repositories import role_repo, staff_user_repo


def _commit(db: Session, message: str) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise DuplicatedRecordError(message)


def _get_staff(db: Session, staff_id: int) -> StaffUser:
    staff = staff_user_repo.get(db, id=staff_id)
    if not staff:
        raise NotFoundError(f"Staff user (id={staff_id}) not found.")
    return staff


def _get_roles(db: Session, codes: list[RoleCode]) -> list[Role]:
    roles = role_repo.get_by_codes(db, codes=[c.value for c in codes])
    missing = sorted({c.value for c in codes} - {r.code for r in roles})
    if missing:
        # ロールはマイグレーションで登録する。ここに来るのは DB の初期データが足りない場合だけ
        raise BadRequestError(f"Roles {missing} are not registered.")
    return roles


# ==========================================
# Role
# ==========================================
def get_roles(db: Session) -> list[Role]:
    return role_repo.get_all(db)


# ==========================================
# StaffUser
# ==========================================
def get_staff_list(db: Session) -> list[StaffUser]:
    return staff_user_repo.get_all(db)


def get_staff(db: Session, staff_id: int) -> StaffUser:
    return _get_staff(db, staff_id)


def create_staff(
    db: Session, staff_in: StaffCreate, actor: StaffUser | None, status: StaffStatus = StaffStatus.PENDING
) -> StaffUser:
    """
    職員を登録します。通常は利用開始前（pending）で作り、システム管理者が有効化します。
    actor が None なのは初期登録スクリプト（scripts/create_staff.py）からの登録だけです。
    """
    if staff_user_repo.get_by_login_id(db, login_id=staff_in.login_id):
        raise DuplicatedRecordError(f"Staff user with login id '{staff_in.login_id}' already exists.")
    roles = _get_roles(db, staff_in.roles)
    staff = StaffUser(login_id=staff_in.login_id, name=staff_in.name, email=staff_in.email, status=status)
    db.add(staff)
    db.flush()
    staff_user_repo.replace_roles(db, staff=staff, roles=roles, granted_by_id=actor.id if actor else None)
    _commit(db, f"Staff user with login id '{staff_in.login_id}' already exists.")
    db.refresh(staff)
    return staff


def update_staff(db: Session, staff_id: int, staff_in: StaffUpdate) -> StaffUser:
    staff = _get_staff(db, staff_id)
    update_data = staff_in.model_dump(exclude_unset=True)
    if update_data.get("name", "") is None:
        raise BadRequestError("name must not be null.")
    for field, value in update_data.items():
        setattr(staff, field, value)
    db.commit()
    db.refresh(staff)
    return staff


def set_staff_roles(db: Session, staff_id: int, codes: list[RoleCode], actor: StaffUser) -> StaffUser:
    """ロールを指定した集合に置き換えます。自分のシステム管理者ロールは外せません（管理者がいなくなるのを防ぐ）。"""
    staff = _get_staff(db, staff_id)
    if staff.id == actor.id and RoleCode.SYSTEM_ADMIN in staff.role_codes and RoleCode.SYSTEM_ADMIN not in codes:
        raise BadRequestError("You cannot remove your own system_admin role.")
    staff_user_repo.replace_roles(db, staff=staff, roles=_get_roles(db, codes), granted_by_id=actor.id)
    db.commit()
    db.refresh(staff)
    return staff


def set_staff_status(db: Session, staff_id: int, status: StaffStatus, actor: StaffUser) -> StaffUser:
    """
    アカウントの状態を変えます（有効化・利用停止・再開）。ロールはそのまま残ります。
    自分の状態は変えられません（自分を停止して操作できなくなるのを防ぐ）。
    """
    staff = _get_staff(db, staff_id)
    if staff.id == actor.id:
        raise BadRequestError("You cannot change your own status.")
    if status == StaffStatus.PENDING and staff.status != StaffStatus.PENDING:
        raise BadRequestError("An account that has been activated cannot go back to pending (suspend it instead).")
    staff.status = status
    if status != StaffStatus.ACTIVE:
        credentials.revoke_all_sessions(db, staff.id, now=_now())  # 停止した職員は、開いているセッションもすぐ使えなくなる
    db.commit()
    db.refresh(staff)
    return staff


# ==========================================
# ログイン・パスワード・API キー（LG.2）
# ==========================================
def _now() -> datetime:
    return datetime.now(timezone.utc)


def login(db: Session, login_id: str, password: str) -> tuple[StaffUser, NewSession]:
    """ログイン ID とパスワードを確かめてセッションを作ります（失敗の回数・ロックも記録する）"""
    now = _now()
    try:
        staff = credentials.authenticate(db, login_id, password, now=now)
    except Exception:
        db.commit()  # 失敗の回数・ロックは、ログインが失敗しても残す
        raise
    new = credentials.create_session(db, staff, now=now)
    db.commit()
    db.refresh(staff)
    return staff, new


def logout(db: Session, token: str | None) -> None:
    credentials.revoke_session(db, token, now=_now())
    db.commit()


def change_own_password(db: Session, staff: StaffUser, current_password: str, new_password: str, session_id: int | None) -> StaffUser:
    """本人のパスワードの変更（今のセッションは残し、ほかのセッションは終える）。仮パスワードの印も外れる"""
    if not credentials.verify_password_hash(current_password, staff.password_hash):
        raise UnauthorizedError("The current password is wrong.", error_code=ErrorCode.INVALID_CREDENTIALS)
    if current_password == new_password:
        raise BadRequestError("The new password must be different from the current one.")
    credentials.set_password(db, staff, new_password, temporary=False, now=_now(), keep_session_id=session_id)
    db.commit()
    db.refresh(staff)
    return staff


def set_temporary_password(db: Session, staff_id: int, password: str, actor: StaffUser) -> StaffUser:
    """システム管理者が仮パスワードを設定します（本人は次のログインで変える。ロックも解ける。自分には使えない）"""
    staff = _get_staff(db, staff_id)
    if staff.id == actor.id:
        raise BadRequestError("Change your own password with POST /auth/password.")
    credentials.set_password(db, staff, password, temporary=True, now=_now())
    db.commit()
    db.refresh(staff)
    return staff


def list_api_keys(db: Session, staff_id: int) -> list[StaffApiKey]:
    _get_staff(db, staff_id)
    return list(db.scalars(
        select(StaffApiKey).where(StaffApiKey.staff_user_id == staff_id).order_by(StaffApiKey.created_at.desc(), StaffApiKey.id.desc())
    ).all())


def issue_api_key(db: Session, staff_id: int, label: str, actor: StaffUser) -> NewApiKey:
    staff = _get_staff(db, staff_id)
    issued = credentials.issue_api_key(db, staff, label, actor=actor, now=_now())
    db.commit()
    db.refresh(issued.api_key)
    return issued


def revoke_api_key(db: Session, key_id: int, actor: StaffUser) -> StaffApiKey:
    api_key = db.get(StaffApiKey, key_id)
    if api_key is None:
        raise NotFoundError(f"API key (id={key_id}) not found.")
    credentials.revoke_api_key(db, api_key, actor=actor, now=_now())
    db.commit()
    db.refresh(api_key)
    return api_key
