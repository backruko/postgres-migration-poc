# src/infrastructure/repositories/Staff.py
"""domain_staff 用のリポジトリ群。commit は Service 層で行います。"""
from collections.abc import Iterable

from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from src.domain_staff.models import Role, StaffClient, StaffRole, StaffUser
from src.infrastructure.repositories.base import CRUDBase


# ==========================================
#  Role Repository
# ==========================================
class RoleRepository(CRUDBase[Role, BaseModel, BaseModel]):

    def get_all(self, db: Session) -> list[Role]:
        return list(db.scalars(select(self.model).order_by(self.model.id)).all())

    def get_by_codes(self, db: Session, *, codes: Iterable[str]) -> list[Role]:
        return list(db.scalars(select(self.model).where(self.model.code.in_(list(codes)))).all())


# ==========================================
#  StaffUser Repository
# ==========================================
class StaffUserRepository(CRUDBase[StaffUser, BaseModel, BaseModel]):

    def get_by_login_id(self, db: Session, *, login_id: str) -> StaffUser | None:
        return db.scalars(select(self.model).where(self.model.login_id == login_id)).first()

    def get_all(self, db: Session) -> list[StaffUser]:
        return list(db.scalars(select(self.model).order_by(self.model.login_id)).all())

    def get_names(self, db: Session, *, ids: Iterable[int | None]) -> dict[int, str]:
        """職員 ID → 名前（記録に操作した人の名前を添えるため。None は無視）"""
        wanted = {i for i in ids if i is not None}
        if not wanted:
            return {}
        rows = db.execute(select(self.model.id, self.model.name).where(self.model.id.in_(wanted))).all()
        return {row.id: row.name for row in rows}

    def replace_roles(self, db: Session, *, staff: StaffUser, roles: list[Role], granted_by_id: int | None) -> None:
        """ロールを指定した集合に置き換えます（残るロールの割り当て日時・割り当てた人はそのまま）"""
        current = {r.id for r in staff.roles}
        wanted = {r.id for r in roles}
        removed = current - wanted
        if removed:
            db.execute(delete(StaffRole).where(StaffRole.staff_user_id == staff.id, StaffRole.role_id.in_(removed)))
        db.add_all(
            StaffRole(staff_user_id=staff.id, role_id=role_id, granted_by_id=granted_by_id) for role_id in wanted - current
        )
        db.flush()
        db.expire(staff, ["roles"])

    def replace_clients(self, db: Session, *, staff: StaffUser, codes: list[str], granted_by_id: int | None) -> None:
        """担当する顧客を指定した集合に置き換えます（残る行の設定日時・設定した人はそのまま）"""
        current = set(staff.client_codes)
        wanted = set(codes)
        removed = current - wanted
        if removed:
            db.execute(delete(StaffClient).where(StaffClient.staff_user_id == staff.id, StaffClient.client_code.in_(removed)))
        db.add_all(
            StaffClient(staff_user_id=staff.id, client_code=code, granted_by_id=granted_by_id) for code in sorted(wanted - current)
        )
        db.flush()
        db.expire(staff, ["client_scope"])


# ==========================================
#  インスタンス化とエクスポート
# ==========================================
role_repo = RoleRepository(Role)
staff_user_repo = StaffUserRepository(StaffUser)
