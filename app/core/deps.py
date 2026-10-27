from collections.abc import Sequence
from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_token
from app.db.session import get_db
from app.enums import AccessRight, EntityStatus, SystemObject
from app.models.user import AccessPrivilege, User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl='/api/v1/auth/login')


@dataclass
class CurrentUser:
    user_id: str
    session_version: int
    administrator: bool
    facility_id: int | None
    # Sourced from the User row and its eager-loaded settings, not from the JWT, so existing
    # tokens keep working. `employee` is NOT NULL since migration 012 (#127), so every
    # authenticated user has one — no service checks for its absence. `point_sale` is still
    # optional and is refused by the services that need it (FR-004a).
    employee_id: int
    point_sale_id: int | None = None
    cash_drawer_id: int | None = None
    must_change_password: bool = False


async def get_signed_in_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> CurrentUser:
    """A valid session, whether or not the user still has to change their password.

    Only `/auth/me` and `/auth/change-password` take this directly: they are what a user with an
    administrator-issued password needs to get rid of it. Everything else goes through
    `get_current_user`.
    """
    exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail='Invalid or expired credentials',
        headers={'WWW-Authenticate': 'Bearer'},
    )
    try:
        payload = decode_token(token)
        user_id: str | None = payload.get('sub')
        if not user_id:
            raise exc
    except JWTError:
        raise exc

    user = await db.get(User, user_id)
    if user is None or user.status != EntityStatus.ACTIVE:
        raise exc

    # Validate session_version on every request to support immediate invalidation
    if user.session_version != payload.get('session_version', -1):
        raise exc

    settings = user.settings
    return CurrentUser(
        user_id=user_id,
        session_version=user.session_version,
        administrator=user.administrator,
        facility_id=payload.get('facility_id'),
        employee_id=user.employee_id,
        point_sale_id=settings.point_sale_id if settings else None,
        cash_drawer_id=settings.cash_drawer_id if settings else None,
        must_change_password=user.must_change_password,
    )


async def get_current_user(
    current_user: CurrentUser = Depends(get_signed_in_user),
) -> CurrentUser:
    """A signed-in user who may use the API. Refused while a password change is pending (#240):
    otherwise the client's prompt could be skipped by calling the API directly."""
    if current_user.must_change_password:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail='password_change_required'
        )
    return current_user


def require_admin(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not current_user.administrator:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail='Administrator access required'
        )
    return current_user


def require_privilege(system_object: SystemObject, right: AccessRight = AccessRight.READ):
    """Dependency factory — administrators bypass all privilege checks."""

    async def _check(
        current_user: CurrentUser = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> CurrentUser:
        if current_user.administrator:
            return current_user
        from sqlalchemy import select

        result = await db.execute(
            select(AccessPrivilege).where(
                AccessPrivilege.user_id == current_user.user_id,
                AccessPrivilege.system_object == int(system_object),
            )
        )
        priv = result.scalar_one_or_none()
        if priv is None or not (priv.privileges & int(right)):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail='Insufficient privileges'
            )
        return current_user

    _check.grants = ((system_object, right),)
    return _check


async def has_any_privilege(
    db: AsyncSession, user_id: str, grants: Sequence[tuple[SystemObject, AccessRight]]
) -> bool:
    """True when the user holds the right paired with at least one of the objects."""
    rights = {int(obj): int(right) for obj, right in grants}
    result = await db.execute(
        select(AccessPrivilege.system_object, AccessPrivilege.privileges).where(
            AccessPrivilege.user_id == user_id,
            AccessPrivilege.system_object.in_(list(rights)),
        )
    )
    return any(mask & rights[obj] for obj, mask in result.all())


async def can_see_every_facility(
    db: AsyncSession, current: CurrentUser, system_object: SystemObject
) -> bool:
    """Whether the caller may act on another facility's records (spec 034, FR-009, FR-014).

    Administrators always may. Anyone else needs read on the path's cross-facility privilege:
    PaymentsEditor (100) for payments and credit notes, SearchCreditsFromAllStores (101) for sales
    orders and accounts receivable.
    """
    return current.administrator or await has_any_privilege(
        db, current.user_id, [(system_object, AccessRight.READ)]
    )


def require_any_privilege(*grants: tuple[SystemObject, AccessRight]):
    """Like `require_privilege`, but any one of several grants opens the route.

    A lookup also accepts Read on the modules whose screens make it (spec 022 FR-005).
    """

    async def _check(
        current_user: CurrentUser = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> CurrentUser:
        if current_user.administrator:
            return current_user
        if not await has_any_privilege(db, current_user.user_id, grants):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail='Insufficient privileges'
            )
        return current_user

    _check.grants = grants
    return _check
