from fastapi import APIRouter, Depends, Query
from fastapi import status as http_status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require_admin
from app.core.error_codes import ErrorCode
from app.core.errors import DomainError, not_found
from app.db.session import get_db
from app.enums import EntityStatus
from app.models.user import User, UserProfile
from app.schemas.auth import RecoverPasswordAdminResponse
from app.schemas.user import UserCreate, UserListResponse, UserResponse, UserUpdate
from app.services import user_profile_service, user_service

router = APIRouter()


@router.get('', response_model=UserListResponse)
async def list_users(
    search: str | None = Query(None, description='Search by username or email'),
    status: EntityStatus | None = Query(None),
    profile_id: int | None = Query(None, description='Only accounts provisioned from this profile'),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    _: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> UserListResponse:
    users, total = await user_service.list_users(
        db, search=search, status=status, profile_id=profile_id, skip=skip, limit=limit
    )
    return UserListResponse(items=await user_service.to_list_items(db, users), total=total)


@router.post('', response_model=UserResponse, status_code=http_status.HTTP_201_CREATED)
async def create_user(
    data: UserCreate,
    _: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    existing = await user_service.get_user(db, data.user_id)
    if existing is not None:
        raise DomainError(
            ErrorCode.RESOURCE_ALREADY_EXISTS,
            'Username already exists',
            {'resource': 'user', 'field': 'username'},
        )

    # Resolved and validated BEFORE anything is staged, so a bad profile leaves no user behind
    # (FR-011). #154 shipped the opposite ordering — work after `commit()` — and a 500 there meant
    # "already created".
    profile = None
    if data.profile_id is not None:
        profile = await user_profile_service.get_profile(db, data.profile_id)
        if profile is None:
            raise not_found(UserProfile, data.profile_id, detail='Profile not found')
        user_profile_service.assert_applyable(profile)

    user = await user_service.create_user(db, data, profile)
    return await user_service.to_response(db, user)


@router.get('/{user_id}', response_model=UserResponse)
async def get_user(
    user_id: str,
    _: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    user = await user_service.get_user(db, user_id)
    if user is None:
        raise not_found(User, user_id)
    return await user_service.to_response(db, user)


@router.put('/{user_id}', response_model=UserResponse)
async def update_user(
    user_id: str,
    data: UserUpdate,
    current: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    user = await user_service.get_user(db, user_id)
    if user is None:
        raise not_found(User, user_id)
    user = await user_service.update_user(db, user, data, updater=current.employee_id)
    return await user_service.to_response(db, user)


@router.delete('/{user_id}', status_code=http_status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: str,
    current: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    user = await user_service.get_user(db, user_id)
    if user is None:
        raise not_found(User, user_id)
    await user_service.delete_user(db, user, updater=current.employee_id)


@router.post('/{user_id}/recover-password', response_model=RecoverPasswordAdminResponse)
async def recover_password(
    user_id: str,
    current: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> RecoverPasswordAdminResponse:
    """Admin-triggered: generate a signed time-limited recovery token for the user."""
    user = await user_service.get_user(db, user_id)
    if user is None:
        raise not_found(User, user_id)
    token, expires_at = await user_service.initiate_recovery(db, user, updater=current.employee_id)
    return RecoverPasswordAdminResponse(recovery_token=token, expires_at=expires_at.isoformat())
