import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.enums import EntityStatus, SystemObject

_USERNAME_RE = re.compile(r'^[0-9a-zA-Z]+$')
_KNOWN_OBJECTS = {int(obj) for obj in SystemObject}


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


class ProfilePrivilegeResponse(BaseModel):
    """One entry of a profile. Same field set as `PrivilegeResponse` by design — a client renders
    a user's permissions and a profile's with the same component."""

    model_config = ConfigDict(from_attributes=True)

    system_object: int
    privileges: int
    allow_create: bool
    allow_read: bool
    allow_update: bool
    allow_delete: bool


class ProfilePrivilegeUpdate(BaseModel):
    system_object: int
    privileges: int = Field(ge=0, le=15)

    @field_validator('system_object')
    @classmethod
    def validate_system_object(cls, v: int) -> int:
        # A profile naming an object outside the catalog could never be applied, so it is refused
        # at write time rather than stored and failed later (FR-010)
        if v not in _KNOWN_OBJECTS:
            raise ValueError(f'Unknown system object: {v}')
        return v


def _reject_duplicate_objects(
    entries: list[ProfilePrivilegeUpdate] | None,
) -> list[ProfilePrivilegeUpdate] | None:
    """Two entries for one object make the profile's meaning depend on iteration order (FR-002)."""
    if entries is None:
        return None
    seen = {entry.system_object for entry in entries}
    if len(seen) != len(entries):
        raise ValueError('Duplicate system_object in privileges')
    return entries


class UserProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=250)
    status: EntityStatus = EntityStatus.ACTIVE
    # Sparse: only the objects the profile grants. Absent means denied (FR-003)
    privileges: list[ProfilePrivilegeUpdate] | None = None

    @field_validator('privileges')
    @classmethod
    def no_duplicate_objects(
        cls, v: list[ProfilePrivilegeUpdate] | None
    ) -> list[ProfilePrivilegeUpdate] | None:
        return _reject_duplicate_objects(v)


class UserProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=250)
    status: EntityStatus | None = None
    # Present replaces the whole entry set; omitted leaves entries untouched, so renaming a
    # profile does not require resending its masks
    privileges: list[ProfilePrivilegeUpdate] | None = None

    @field_validator('privileges')
    @classmethod
    def no_duplicate_objects(
        cls, v: list[ProfilePrivilegeUpdate] | None
    ) -> list[ProfilePrivilegeUpdate] | None:
        return _reject_duplicate_objects(v)


class UserProfileListItem(BaseModel):
    """No `privileges`: a catalog page of twenty profiles would fetch masks it will not render."""

    model_config = ConfigDict(from_attributes=True)

    user_profile_id: int
    name: str
    description: str | None
    status: EntityStatus


class UserProfileListResponse(BaseModel):
    items: list[UserProfileListItem]
    total: int


class UserProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_profile_id: int
    name: str
    description: str | None
    status: EntityStatus
    privileges: list[ProfilePrivilegeResponse]


class UserSettingsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    facility_id: int | None
    facility_code: str | None = None
    facility_name: str | None = None
    point_sale_id: int | None
    point_sale_code: str | None = None
    point_sale_name: str | None = None
    cash_drawer_id: int | None
    cash_drawer_code: str | None = None
    cash_drawer_name: str | None = None


class UserSettingsUpdate(BaseModel):
    facility_id: int | None = None
    point_sale_id: int | None = None
    cash_drawer_id: int | None = None


class UserCreate(BaseModel):
    user_id: str = Field(min_length=4, max_length=20)
    password: str = Field(min_length=1)
    email: str
    # Required, and NOT NULL in the database since migration 012 (#127). A user with no employee
    # can log in and then be refused by every service that authors a document
    employee_id: int
    administrator: bool = False
    status: EntityStatus = EntityStatus.ACTIVE
    # Optional: names a profile to provision the account from, applied in the same transaction so
    # a bad profile leaves no user behind (FR-011). Absent means every permission denied, exactly
    # as before spec 014.
    profile_id: int | None = None

    @field_validator('user_id')
    @classmethod
    def validate_username(cls, v: str) -> str:
        if not _USERNAME_RE.match(v):
            raise ValueError('Username must contain only alphanumeric characters')
        return v


class UserUpdate(BaseModel):
    email: str | None = None
    employee_id: int | None = None
    administrator: bool | None = None
    status: EntityStatus | None = None
    # Full privilege list — server upserts all provided entries
    privileges: list[PrivilegeUpdate] | None = None
    settings: UserSettingsUpdate | None = None


class UserListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: str
    email: str
    employee_id: int
    administrator: bool
    status: EntityStatus
    # Provenance, carried on every row so the list is legible as well as filterable (FR-020).
    # `profile_name` is resolved for the whole page in one query — see user_service.to_list_items.
    profile_id: int | None = None
    profile_name: str | None = None


class UserListResponse(BaseModel):
    items: list[UserListItem]
    total: int


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: str
    email: str
    employee_id: int
    administrator: bool
    status: EntityStatus
    session_version: int
    profile_id: int | None = None
    profile_name: str | None = None
    settings: UserSettingsResponse | None
    privileges: list[PrivilegeResponse]


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str = Field(min_length=6)
