from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_recovery_token, sha1_hash, verify_password
from app.enums import EntityStatus, SystemObject
from app.models.user import AccessPrivilege, User, UserProfile, UserSettings
from app.schemas.user import (
    UserCreate,
    UserListItem,
    UserResponse,
    UserSettingsUpdate,
    UserUpdate,
)
from app.services import user_profile_service
from app.services.references import assert_not_referenced


async def get_user(db: AsyncSession, user_id: str) -> User | None:
    return await db.get(User, user_id)


async def list_users(
    db: AsyncSession,
    search: str | None = None,
    status: EntityStatus | None = None,
    profile_id: int | None = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[Sequence[User], int]:
    base = select(User)
    count_q = select(func.count()).select_from(User)

    if search:
        # Employee name search requires employee module join — scope: username + email only
        condition = or_(User.user_id.ilike(f'%{search}%'), User.email.ilike(f'%{search}%'))
        base = base.where(condition)
        count_q = count_q.where(condition)

    if status is not None:
        base = base.where(User.status == status)
        count_q = count_q.where(User.status == status)

    # FR-021: find every account provisioned from a profile, so a corrected profile can be
    # re-applied to the right people without a list kept outside the system
    if profile_id is not None:
        base = base.where(User.profile_id == profile_id)
        count_q = count_q.where(User.profile_id == profile_id)

    total: int = (await db.execute(count_q)).scalar_one()
    users = (await db.execute(base.offset(skip).limit(limit))).scalars().all()
    return users, total


async def profile_names_for(db: AsyncSession, users: Sequence[User]) -> dict[int, str]:
    """`{user_profile_id: name}` for the origins on this page — **one query, never one per row**.

    Two columns, not entities, and deliberately not `fk_expansion.batch_fetch`. That helper loads
    mapped objects, and `UserProfile.privileges` is `lazy='selectin'` — so fetching profiles as
    entities fires a second query that loads every mask of every profile on the page, to render a
    name. Measured: 2 queries via `batch_fetch`, 1 this way.

    `test_profile_names_cost_one_query_for_the_whole_page` asserts the count, which is how the
    difference was found — both versions return identical JSON.
    """
    ids = {user.profile_id for user in users if user.profile_id is not None}
    if not ids:
        return {}
    rows = await db.execute(
        select(UserProfile.user_profile_id, UserProfile.name).where(
            UserProfile.user_profile_id.in_(ids)
        )
    )
    return {profile_id: name for profile_id, name in rows.all()}


async def to_response(db: AsyncSession, user: User) -> UserResponse:
    """A single user, with the name of the profile it was provisioned from (FR-020)."""
    names = await profile_names_for(db, [user])
    response = UserResponse.model_validate(user)
    response.profile_name = names.get(user.profile_id) if user.profile_id is not None else None
    return response


async def to_list_items(db: AsyncSession, users: Sequence[User]) -> list[UserListItem]:
    """A page of users, each row naming its origin profile (FR-020), at one query for the page."""
    names = await profile_names_for(db, users)
    items = []
    for user in users:
        item = UserListItem.model_validate(user)
        item.profile_name = names.get(user.profile_id) if user.profile_id is not None else None
        items.append(item)
    return items


def _write_privileges_from(user: User, profile: UserProfile | None) -> None:
    """Replace the user's whole permission set from `profile` (spec 014, FR-013).

    Every one of the 107 `SystemObject` values ends at the profile's mask or at 0, and any row on an
    object outside the enum is removed — 70, 104 and 105 are features commented out in the legacy
    catalog whose grants outlived them (research R9). `profile=None` denies everything, which is
    what `create_user` did before this feature existed.

    **Why this updates in place rather than clearing and re-inserting.** The obvious implementation
    is `user.privileges.clear()` plus 107 appends, and that is what shipped first. Migration 015
    then added `UNIQUE (user, object)`, and SQLAlchemy's unit of work emits INSERTs before DELETEs
    within one flush — so re-inserting the same pairs collides with the rows being deleted and every
    apply raises `IntegrityError`. Caught by `tests/integration/`, whose SQLite schema is built from
    this metadata and therefore carries the constraint.

    Updating the row that already exists sidesteps the ordering question, and is cheaper: most masks
    are already 0 and stay 0, so SQLAlchemy issues no UPDATE for them, where the previous version
    wrote 107 rows unconditionally.

    The observable result is unchanged — this is a different mechanism for the same decision, not a
    revision of it (research R3).

    Stages only — the caller commits, so a create can validate, write and commit in one transaction
    (research R8).
    """
    masks = user_profile_service.masks_of(profile) if profile is not None else {}
    known = {int(obj) for obj in SystemObject}
    existing = {entry.system_object: entry for entry in user.privileges}

    for obj in known:
        entry = existing.get(obj)
        if entry is None:
            user.privileges.append(
                AccessPrivilege(system_object=obj, privileges=masks.get(obj, 0))
            )
        else:
            entry.privileges = masks.get(obj, 0)

    # Objects this API does not define. Removing them from the collection is a real DELETE —
    # `User.privileges` carries `cascade='all, delete-orphan'`.
    for obj, entry in existing.items():
        if obj not in known:
            user.privileges.remove(entry)


async def create_user(
    db: AsyncSession, data: UserCreate, profile: UserProfile | None = None
) -> User:
    """Create an account, optionally provisioned from a profile in the same transaction.

    The profile is resolved and validated by the caller *before* anything is staged, and there is
    exactly one commit with nothing after it. That ordering is the lesson of #154, where
    `_attach_links` ran after `await db.commit()` and a 500 came back on a request that had already
    written the row (FR-011).
    """
    user = User(
        user_id=data.user_id,
        password=sha1_hash(data.password),
        email=data.email.lower(),
        employee_id=data.employee_id,
        administrator=data.administrator,
        status=data.status,
        session_version=0,
        profile_id=profile.user_profile_id if profile is not None else None,
    )
    db.add(user)

    # UserSettings.facility is NOT NULL — row created once a facility is assigned via update_user

    # One access_privilege row per SystemObject. Denied throughout when no profile is named, which
    # is byte-for-byte the prior behaviour (FR-027).
    _write_privileges_from(user, profile)

    await db.commit()
    await db.refresh(user)
    return user


async def apply_profile(db: AsyncSession, user: User, profile: UserProfile) -> User:
    """Copy a profile's permissions onto an existing account (spec 014, FR-010).

    One transaction: the rewrite, the recorded origin and the session invalidation commit together,
    so a failure part-way leaves the account exactly as it was (FR-018).

    The caller checks that the profile is applyable — `user_profile_service.assert_applyable` — so
    that an inactive profile is refused before any row is touched (FR-017).
    """
    _write_privileges_from(user, profile)
    user.profile_id = profile.user_profile_id
    # A privilege mutation, so existing JWTs stop being accepted (FR-015). Relative, which is
    # correct from any starting value — the schema defaults this to 1 and the model to 0.
    user.session_version += 1

    await db.commit()
    await db.refresh(user)
    return user


async def update_user(db: AsyncSession, user: User, data: UserUpdate) -> User:
    if data.email is not None:
        user.email = data.email.lower()
    if data.employee_id is not None:
        user.employee_id = data.employee_id
    if data.administrator is not None:
        user.administrator = data.administrator
    if data.status is not None:
        user.status = data.status

    if data.privileges is not None:
        existing = {p.system_object: p for p in user.privileges}
        for entry in data.privileges:
            if entry.system_object in existing:
                existing[entry.system_object].privileges = entry.privileges
            else:
                db.add(
                    AccessPrivilege(
                        user_id=user.user_id,
                        system_object=entry.system_object,
                        privileges=entry.privileges,
                    )
                )

    if data.settings is not None:
        await _apply_settings(db, user, data.settings)

    # Increment session_version to immediately invalidate all existing JWTs
    user.session_version += 1

    await db.commit()
    await db.refresh(user)
    # TODO: write incidence record with source_type = SourceType.UserSettings
    return user


async def delete_user(db: AsyncSession, user: User) -> None:
    await assert_not_referenced(db, user, exempt=frozenset({'user_settings', 'access_privilege'}))
    # ORM cascade (all, delete-orphan) handles: user_settings → access_privilege → user
    await db.delete(user)
    await db.commit()
    # TODO: write incidence record with source_type = SourceType.UserSettings


async def authenticate_user(db: AsyncSession, username: str, password: str) -> User | None:
    user = await db.get(User, username)
    if user is None or user.status != EntityStatus.ACTIVE:
        return None

    if not verify_password(password, user.password):
        return None

    return user


async def change_password(db: AsyncSession, user: User, new_password: str) -> None:
    user.password = sha1_hash(new_password)
    await db.commit()


async def initiate_recovery(db: AsyncSession, user: User) -> tuple[str, datetime]:
    """Admin-triggered recovery. Returns a signed time-limited recovery token."""
    token, expires_at = create_recovery_token(user.user_id)
    # Token is self-contained — no temp password stored; user sets new password via /auth/recover
    return token, expires_at


async def complete_recovery(db: AsyncSession, user: User, new_password: str) -> None:
    user.password = sha1_hash(new_password)
    await db.commit()


async def _apply_settings(db: AsyncSession, user: User, data: UserSettingsUpdate) -> None:
    settings = user.settings
    if settings is None:
        if data.facility_id is None:
            return  # facility is NOT NULL — cannot create UserSettings without a facility
        settings = UserSettings(user_id=user.user_id, facility_id=data.facility_id)
        db.add(settings)
        user.settings = settings
    else:
        if data.facility_id is not None:
            settings.facility_id = data.facility_id

    if data.point_sale_id is not None:
        settings.point_sale_id = data.point_sale_id
    if data.cash_drawer_id is not None:
        settings.cash_drawer_id = data.cash_drawer_id
