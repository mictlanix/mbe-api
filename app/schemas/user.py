import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

_USERNAME_RE = re.compile(r"^[0-9a-zA-Z]+$")


class PrivilegeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    system_object: int
    privileges: int
    allow_create: bool
    allow_read: bool
    allow_update: bool
    allow_delete: bool


class PrivilegeUpdate(BaseModel):
    system_object: int
    privileges: int = Field(ge=0, le=15)


class UserSettingsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    store_id: int | None
    store_code: str | None = None
    store_name: str | None = None
    point_sale_id: int | None
    point_sale_code: str | None = None
    point_sale_name: str | None = None
    cash_drawer_id: int | None
    cash_drawer_code: str | None = None
    cash_drawer_name: str | None = None


class UserSettingsUpdate(BaseModel):
    store_id: int | None = None
    point_sale_id: int | None = None
    cash_drawer_id: int | None = None


class UserCreate(BaseModel):
    user_id: str = Field(min_length=4, max_length=20)
    password: str = Field(min_length=1)
    email: str
    employee_id: int | None = None
    administrator: bool = False
    disabled: bool = False

    @field_validator("user_id")
    @classmethod
    def validate_username(cls, v: str) -> str:
        if not _USERNAME_RE.match(v):
            raise ValueError("Username must contain only alphanumeric characters")
        return v


class UserUpdate(BaseModel):
    email: str | None = None
    employee_id: int | None = None
    administrator: bool | None = None
    disabled: bool | None = None
    # Full privilege list — server upserts all provided entries
    privileges: list[PrivilegeUpdate] | None = None
    settings: UserSettingsUpdate | None = None


class UserListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: str
    email: str
    employee_id: int | None
    administrator: bool
    disabled: bool


class UserListResponse(BaseModel):
    items: list[UserListItem]
    total: int


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: str
    email: str
    employee_id: int | None
    administrator: bool
    disabled: bool
    session_version: int
    settings: UserSettingsResponse | None
    privileges: list[PrivilegeResponse]


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str = Field(min_length=6)
