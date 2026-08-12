from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require_admin
from app.db.session import get_db
from app.enums import EntityStatus
from app.schemas.user import (
    UserProfileCreate,
    UserProfileListResponse,
    UserProfileResponse,
    UserProfileUpdate,
    UserResponse,
)
from app.services import user_profile_service, user_service

router = APIRouter()

# Administrator-gated rather than governed by a SystemObject, matching the existing /users
# endpoints. Gating these on a system object would mean extending the permission vocabulary this
# feature is otherwise meant to leave alone (FR-028, spec Assumptions).


@router.get('', response_model=UserProfileListResponse)
async def list_user_profiles(
    search: str | None = Query(None, description='Search by profile name'),
    status: EntityStatus | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    _: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> UserProfileListResponse:
    profiles, total = await user_profile_service.list_profiles(
        db, search=search, status=status, skip=skip, limit=limit
    )
    return UserProfileListResponse(items=list(profiles), total=total)


@router.post('', response_model=UserProfileResponse, status_code=http_status.HTTP_201_CREATED)
async def create_user_profile(
    data: UserProfileCreate,
    _: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> UserProfileResponse:
    profile = await user_profile_service.create_profile(db, data)
    return UserProfileResponse.model_validate(profile)


@router.get('/{profile_id}', response_model=UserProfileResponse)
async def get_user_profile(
    profile_id: int,
    _: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> UserProfileResponse:
    profile = await _require_profile(db, profile_id)
    return UserProfileResponse.model_validate(profile)


@router.put('/{profile_id}', response_model=UserProfileResponse)
async def update_user_profile(
    profile_id: int,
    data: UserProfileUpdate,
    _: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> UserProfileResponse:
    profile = await _require_profile(db, profile_id)
    profile = await user_profile_service.update_profile(db, profile, data)
    return UserProfileResponse.model_validate(profile)


@router.delete('/{profile_id}', status_code=http_status.HTTP_204_NO_CONTENT)
async def delete_user_profile(
    profile_id: int,
    _: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    profile = await _require_profile(db, profile_id)
    await user_profile_service.delete_profile(db, profile)


@router.post('/{profile_id}/apply/{user_id}', response_model=UserResponse)
async def apply_user_profile(
    profile_id: int,
    user_id: str,
    _: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """Copy a profile's permissions onto a user, replacing everything they held.

    Mounted on the profile rather than the user because the profile is the action's subject: it is
    what gets copied and what can refuse by being inactive. Returns the full updated user, matching
    `PUT /users/{id}` — a caller that just replaced 107 permission rows wants to see them.
    """
    profile = await _require_profile(db, profile_id)
    user_profile_service.assert_applyable(profile)

    user = await user_service.get_user(db, user_id)
    if user is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail='User not found')

    user = await user_service.apply_profile(db, user, profile)
    return await user_service.to_response(db, user)


async def _require_profile(db: AsyncSession, profile_id: int):  # noqa: ANN202
    profile = await user_profile_service.get_profile(db, profile_id)
    if profile is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail='Profile not found')
    return profile
