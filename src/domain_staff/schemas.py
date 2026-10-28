from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.domain_staff.models import RoleCode, StaffStatus


def _unique_roles(roles: list[RoleCode]) -> list[RoleCode]:
    duplicated = sorted({r.value for r in roles if roles.count(r) > 1})
    if duplicated:
        raise ValueError(f"duplicated roles: {duplicated}")
    return roles


# ==========================================
# Role
# ==========================================
class RoleResponse(BaseModel):
    code: RoleCode
    name: str
    description: str | None

    model_config = ConfigDict(from_attributes=True)


# ==========================================
# StaffUser
# ==========================================
class StaffCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    login_id: str = Field(..., min_length=1, max_length=100, description="login id (employee number, email ...)")
    name: str = Field(..., min_length=1, max_length=100)
    email: str | None = Field(default=None, max_length=255)
    roles: list[RoleCode] = Field(default_factory=list, description="roles to grant (the account starts as pending)")

    _check_roles = field_validator("roles")(_unique_roles)


class StaffUpdate(BaseModel):
    """氏名・メールアドレスの変更（ログインID・状態・ロールは別のエンドポイント）"""
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=100)
    email: str | None = Field(default=None, max_length=255)


class StaffRolesUpdate(BaseModel):
    roles: list[RoleCode] = Field(..., description="the full set of roles (replaces the current set)")

    _check_roles = field_validator("roles")(_unique_roles)


class StaffStatusUpdate(BaseModel):
    status: StaffStatus


class StaffResponse(BaseModel):
    id: int
    login_id: str
    name: str
    email: str | None
    status: StaffStatus
    roles: list[RoleCode] = Field(validation_alias="role_codes")
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
