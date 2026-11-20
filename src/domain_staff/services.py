
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.core.error_codes import ErrorCode
from src.core.exceptions import BadRequestError, DuplicatedRecordError, NotFoundError, UnauthorizedError
from src.domain_staff import credentials
from src.domain_staff.credentials import NewApiKey, NewSession
from src.domain_staff.models import MACHINE_ROLES, Role, RoleCode, StaffApiKey, StaffStatus, StaffUser
from src.domain_staff.schemas import StaffCreate, StaffUpdate
from src.infrastructure.repositories import client_repo, role_repo, staff_user_repo
from src.core.clock import utc_now as _now


def _commit(db: Session, message: str) -> None:
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise DuplicatedRecordError(message) from e


def _get_staff(db: Session, staff_id: int) -> StaffUser:
    staff = staff_user_repo.get(db, id=staff_id)
    if not staff:
        raise NotFoundError(f"Staff user (id={staff_id}) not found.")
    return staff


def _check_machine_only(codes: list[RoleCode], staff: StaffUser | None = None) -> None:
    """
    system_agent（読み取りの API キー用）は機械用のアカウントだけ: ほかに持てるのは signal_sender だけで、
    パスワードのある（画面にログインできる）アカウントには付けられない。キーが漏れても読み取りより先へ進めないようにする。
    """
    if RoleCode.SYSTEM_AGENT not in codes:
        return
    human = sorted(c.value for c in set(codes) - MACHINE_ROLES)
    if human:
        raise BadRequestError(f"system_agent is for machine accounts only; it cannot be combined with {human}.")
    if staff is not None and staff.has_password:
        raise BadRequestError(
            f"'{staff.login_id}' has a password (it can sign in); create a separate machine account for system_agent."
        )


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
    _check_machine_only(staff_in.roles)
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
    if RoleCode.SYSTEM_ADMIN in codes and staff.client_codes:
        raise BadRequestError(_SYSTEM_ADMIN_ALL_CLIENTS)
    _check_machine_only(codes, staff)
    staff_user_repo.replace_roles(db, staff=staff, roles=_get_roles(db, codes), granted_by_id=actor.id)
    db.commit()
    db.refresh(staff)
    return staff


_SYSTEM_ADMIN_ALL_CLIENTS = (
    "A system admin must handle all clients (staff accounts are shared by all clients): "
    "clear the client list or do not grant system_admin."
)


def set_staff_clients(db: Session, staff_id: int, codes: list[str], actor: StaffUser) -> StaffUser:
    """
    担当する顧客（顧客の範囲）を指定した集合に置き換えます。空なら全顧客。
    無い顧客コードは 400。新しく加える顧客は有効なものだけ（無効にした顧客が今の範囲に残っているのはそのまま）。
    システム管理者には範囲を付けられません（職員の画面は全顧客に共通のため、自分を締め出してしまう）。
    """
    staff = _get_staff(db, staff_id)
    if codes and RoleCode.SYSTEM_ADMIN in staff.role_codes:
        raise BadRequestError(_SYSTEM_ADMIN_ALL_CLIENTS)
    clients = {c.code: c for c in client_repo.get_by_codes(db, codes=codes)} if codes else {}
    unknown = sorted(set(codes) - set(clients))
    if unknown:
        raise BadRequestError(f"Clients {unknown} do not exist.")
    inactive = sorted(c for c in set(codes) - set(staff.client_codes) if not clients[c].is_active)
    if inactive:
        raise BadRequestError(f"Clients {inactive} are inactive and cannot be added.")
    staff_user_repo.replace_clients(db, staff=staff, codes=codes, granted_by_id=actor.id)
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
# ログイン・パスワード・API キー
# ==========================================


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
