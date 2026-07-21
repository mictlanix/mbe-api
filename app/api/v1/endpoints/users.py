from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require_admin
from app.db.session import get_db
from app.enums import EntityStatus
from app.schemas.auth import RecoverPasswordAdminResponse
from app.schemas.user import UserCreate, UserListResponse, UserResponse, UserUpdate
from app.services import user_service

router = APIRouter()


@router.get('', response_model=UserListResponse)
async def list_users(
    search: str | None = Query(None, description='Search by username or email'),
    status: EntityStatus | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    _: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> UserListResponse:
    users, total = await user_service.list_users(
        db, search=search, status=status, skip=skip, limit=limit
    )
    return UserListResponse(items=list(users), total=total)


@router.post('', response_model=UserResponse, status_code=http_status.HTTP_201_CREATED)
async def create_user(
    data: UserCreate,
    _: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    existing = await user_service.get_user(db, data.user_id)
    if existing is not None:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT, detail='Username already exists'
        )
    user = await user_service.create_user(db, data)
    return UserResponse.model_validate(user)


@router.get('/{user_id}', response_model=UserResponse)
async def get_user(
    user_id: str,
    _: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    user = await user_service.get_user(db, user_id)
    if user is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail='User not found')
    return UserResponse.model_validate(user)


@router.put('/{user_id}', response_model=UserResponse)
async def update_user(
    user_id: str,
    data: UserUpdate,
    _: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    user = await user_service.get_user(db, user_id)
    if user is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail='User not found')
    user = await user_service.update_user(db, user, data)
    return UserResponse.model_validate(user)


@router.delete('/{user_id}', status_code=http_status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: str,
    _: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    user = await user_service.get_user(db, user_id)
    if user is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail='User not found')
    await user_service.delete_user(db, user)


@router.post('/{user_id}/recover-password', response_model=RecoverPasswordAdminResponse)
async def recover_password(
    user_id: str,
    _: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> RecoverPasswordAdminResponse:
    """Admin-triggered: generate a signed time-limited recovery token for the user."""
    user = await user_service.get_user(db, user_id)
    if user is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail='User not found')
    token, expires_at = await user_service.initiate_recovery(db, user)
    return RecoverPasswordAdminResponse(recovery_token=token, expires_at=expires_at.isoformat())
