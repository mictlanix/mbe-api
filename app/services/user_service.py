from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import bcrypt_hash, create_recovery_token, random_password, verify_password
from app.enums import SystemObject
from app.models.user import AccessPrivilege, User, UserSettings
from app.schemas.user import UserCreate, UserSettingsUpdate, UserUpdate


async def get_user(db: AsyncSession, user_id: str) -> User | None:
    return await db.get(User, user_id)


async def list_users(
    db: AsyncSession,
    search: str | None = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[Sequence[User], int]:
    base = select(User)
    count_q = select(func.count()).select_from(User)

    if search:
        # Employee name search requires employee module join — scope: username + email only
        condition = or_(User.user_id.ilike(f"%{search}%"), User.email.ilike(f"%{search}%"))
        base = base.where(condition)
        count_q = count_q.where(condition)

    total: int = (await db.execute(count_q)).scalar_one()
    users = (await db.execute(base.offset(skip).limit(limit))).scalars().all()
    return users, total


async def create_user(db: AsyncSession, data: UserCreate) -> User:
    user = User(
        user_id=data.user_id,
        password=bcrypt_hash(data.password),
        password_scheme="bcrypt",
        email=data.email.lower(),
        employee_id=data.employee_id,
        administrator=data.administrator,
        disabled=data.disabled,
        session_version=0,
    )
    db.add(user)

    # UserSettings.store is NOT NULL — row is created via update_user once a store is assigned

    # Pre-create one access_privilege row per SystemObject, all denied
    for obj in SystemObject:
        db.add(AccessPrivilege(user_id=data.user_id, system_object=int(obj), privileges=0))

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
    if data.disabled is not None:
        user.disabled = data.disabled

    if data.privileges is not None:
        existing = {p.system_object: p for p in user.privileges}
        for entry in data.privileges:
            if entry.system_object in existing:
                existing[entry.system_object].privileges = entry.privileges
            else:
                db.add(AccessPrivilege(
                    user_id=user.user_id,
                    system_object=entry.system_object,
                    privileges=entry.privileges,
                ))

    if data.settings is not None:
        await _apply_settings(db, user, data.settings)

    # Increment session_version to immediately invalidate all existing JWTs
    user.session_version += 1

    await db.commit()
    await db.refresh(user)
    # TODO: write incidence record with source_type = SourceType.UserSettings
    return user


async def delete_user(db: AsyncSession, user: User) -> None:
    # ORM cascade (all, delete-orphan) handles: user_settings → access_privilege → user
    await db.delete(user)
    await db.commit()
    # TODO: write incidence record with source_type = SourceType.UserSettings


async def authenticate_user(db: AsyncSession, username: str, password: str) -> User | None:
    user = await db.get(User, username)
    if user is None or user.disabled:
        return None

    if not verify_password(password, user.password, user.password_scheme):
        return None

    # Migrate SHA1 → bcrypt on successful login
    if user.password_scheme == "sha1":
        user.password = bcrypt_hash(password)
        user.password_scheme = "bcrypt"
        await db.commit()

    return user


async def change_password(db: AsyncSession, user: User, new_password: str) -> None:
    user.password = bcrypt_hash(new_password)
    user.password_scheme = "bcrypt"
    await db.commit()


async def initiate_recovery(db: AsyncSession, user: User) -> tuple[str, datetime]:
    """Admin-triggered recovery. Returns a signed time-limited recovery token."""
    token, expires_at = create_recovery_token(user.user_id)
    # Token is self-contained — no temp password stored; user sets new password via /auth/recover
    return token, expires_at


async def complete_recovery(db: AsyncSession, user: User, new_password: str) -> None:
    user.password = bcrypt_hash(new_password)
    user.password_scheme = "bcrypt"
    await db.commit()


async def _apply_settings(db: AsyncSession, user: User, data: UserSettingsUpdate) -> None:
    settings = user.settings
    if settings is None:
        if data.store_id is None:
            return  # store is NOT NULL — cannot create UserSettings without a store
        settings = UserSettings(user_id=user.user_id, store_id=data.store_id)
        db.add(settings)
        user.settings = settings
    else:
        if data.store_id is not None:
            settings.store_id = data.store_id

    if data.point_sale_id is not None:
        settings.point_sale_id = data.point_sale_id
    if data.cash_drawer_id is not None:
        settings.cash_drawer_id = data.cash_drawer_id
