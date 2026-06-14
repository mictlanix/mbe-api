from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_token
from app.db.session import get_db
from app.enums import AccessRight, SystemObject
from app.models.user import AccessPrivilege, User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


@dataclass
class CurrentUser:
    user_id: str
    session_version: int
    administrator: bool
    store_id: int | None


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> CurrentUser:
    exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_token(token)
        user_id: str | None = payload.get("sub")
        if not user_id:
            raise exc
    except JWTError:
        raise exc

    user = await db.get(User, user_id)
    if user is None or user.disabled:
        raise exc

    # Validate session_version on every request to support immediate invalidation
    if user.session_version != payload.get("session_version", -1):
        raise exc

    return CurrentUser(
        user_id=user_id,
        session_version=user.session_version,
        administrator=user.administrator,
        store_id=payload.get("store_id"),
    )


def require_admin(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not current_user.administrator:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Administrator access required"
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
                status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient privileges"
            )
        return current_user

    return _check
