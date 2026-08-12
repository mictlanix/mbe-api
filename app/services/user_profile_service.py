"""User profiles — named permission templates copied onto users (spec 014).

Two things about this module are easy to get backwards:

1. **A profile is sparse; a user is dense.** A profile stores an entry only for an object it grants
   something on, while a user carries one `access_privilege` row per `SystemObject`. `apply_to_user`
   is the translation, and it lives in `user_service` because it writes user rows.

2. **Uniqueness is compared on `LOWER(name)`, not on the column.** The deployed collation
   (`utf8mb3_unicode_ci`) is case-insensitive, so a plain `==` would satisfy FR-004 on MariaDB — but
   `tests/integration/` runs these services against SQLite, where `=` on `TEXT` is case-sensitive.
   Comparing on `func.lower` makes the stated rule the enforced rule in both (research R4).
"""

from collections.abc import Sequence

from fastapi import HTTPException
from fastapi import status as http_status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import EntityStatus
from app.models.user import UserProfile, UserProfilePrivilege
from app.schemas.user import ProfilePrivilegeUpdate, UserProfileCreate, UserProfileUpdate
from app.services.references import assert_not_referenced, assert_unique


async def get_profile(db: AsyncSession, profile_id: int) -> UserProfile | None:
    return await db.get(UserProfile, profile_id)


async def list_profiles(
    db: AsyncSession,
    *,
    search: str | None = None,
    status: EntityStatus | None = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[Sequence[UserProfile], int]:
    base = select(UserProfile)
    count_q = select(func.count()).select_from(UserProfile)

    if search:
        term = f'%{search}%'
        base = base.where(UserProfile.name.ilike(term))
        count_q = count_q.where(UserProfile.name.ilike(term))

    if status is not None:
        base = base.where(UserProfile.status == status)
        count_q = count_q.where(UserProfile.status == status)

    total: int = (await db.execute(count_q)).scalar_one()
    items = (await db.execute(base.offset(skip).limit(limit))).scalars().all()
    return items, total


async def create_profile(db: AsyncSession, data: UserProfileCreate) -> UserProfile:
    await _assert_name_available(db, data.name)

    profile = UserProfile(
        name=data.name,
        description=data.description,
        status=data.status,
    )
    profile.privileges = _entries_from(data.privileges)
    db.add(profile)
    await db.commit()
    await db.refresh(profile)
    return profile


async def update_profile(
    db: AsyncSession, profile: UserProfile, data: UserProfileUpdate
) -> UserProfile:
    if data.name is not None:
        await _assert_name_available(db, data.name, exclude_pk=profile.user_profile_id)
        profile.name = data.name
    if data.description is not None:
        profile.description = data.description
    if data.status is not None:
        profile.status = data.status

    # Present replaces the entry set entirely; omitted leaves it alone, so a rename need not
    # resend the masks. Unlike a user's privileges, which are a partial upsert (FR-026), a
    # profile's entry set is authoritative on every write that includes it.
    if data.privileges is not None:
        profile.privileges = _entries_from(data.privileges)

    await db.commit()
    await db.refresh(profile)
    return profile


async def delete_profile(db: AsyncSession, profile: UserProfile) -> None:
    # Refuses while any user was provisioned from it, naming the blocking table and row count.
    # `referencing_columns` derives that from FK metadata, so `user`.`profile` being a mapped FK
    # is the whole implementation of FR-008 (research R5). `user_profile_privilege` is exempt
    # because the ORM cascade deletes those rows with the profile.
    await assert_not_referenced(db, profile, exempt=frozenset({'user_profile_privilege'}))
    await db.delete(profile)
    await db.commit()


def masks_of(profile: UserProfile) -> dict[int, int]:
    """`{system_object: mask}` for what this profile grants. Objects absent are denied."""
    return {entry.system_object: entry.privileges for entry in profile.privileges}


def assert_applyable(profile: UserProfile) -> None:
    """An inactive profile stays readable but cannot be applied (FR-017).

    409 rather than 422: the request is well formed and the profile exists — it is the resource's
    state that refuses, which is what 409 means elsewhere in this API.
    """
    if profile.status != EntityStatus.ACTIVE:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT, detail='Profile is not active'
        )


async def _assert_name_available(
    db: AsyncSession, name: str, *, exclude_pk: int | None = None
) -> None:
    await assert_unique(
        db,
        UserProfile,
        func.lower(UserProfile.name),
        name.lower(),
        exclude_pk=exclude_pk,
        label='Profile name',
    )


def _entries_from(entries: list[ProfilePrivilegeUpdate] | None) -> list[UserProfilePrivilege]:
    """Zero-mask entries are dropped, so "no entry" is the single representation of "denied".

    A round-trip is then stable: what a read returns is what a subsequent write would produce
    (FR-003). Accepting a zero and storing it would make two payloads mean the same thing.
    """
    if not entries:
        return []
    return [
        UserProfilePrivilege(system_object=entry.system_object, privileges=entry.privileges)
        for entry in entries
        if entry.privileges != 0
    ]
