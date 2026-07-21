from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_current_user
from app.core.security import create_access_token, decode_token
from app.db.session import get_db
from app.models.user import User
from app.schemas.auth import ConfirmRecoveryRequest, TokenResponse
from app.schemas.user import ChangePasswordRequest, UserResponse
from app.services import user_service

router = APIRouter()


@router.post('/login', response_model=TokenResponse)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    user = await user_service.authenticate_user(db, form_data.username, form_data.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Invalid username or password',
            headers={'WWW-Authenticate': 'Bearer'},
        )
    facility_id = user.settings.facility_id if user.settings else None
    token = create_access_token(user.user_id, user.session_version, user.administrator, facility_id)
    return TokenResponse(access_token=token)


@router.get('/me', response_model=UserResponse)
async def get_me(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """Return the authenticated caller's own profile, settings, and privileges."""
    user = await user_service.get_user(db, current_user.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='User not found')
    return UserResponse.model_validate(user)


@router.post('/recover', status_code=status.HTTP_204_NO_CONTENT)
async def confirm_recovery(
    data: ConfirmRecoveryRequest,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Complete an admin-triggered password recovery using a signed recovery token."""
    bad_token = HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid or expired recovery token'
    )
    try:
        payload = decode_token(data.recovery_token)
        if payload.get('type') != 'recovery':
            raise bad_token
        user_id: str | None = payload.get('sub')
        if not user_id:
            raise bad_token
    except JWTError:
        raise bad_token

    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='User not found')

    await user_service.complete_recovery(db, user, data.new_password)


@router.post('/change-password', status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    data: ChangePasswordRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Change the authenticated user's own password (requires old password verification)."""
    user = await db.get(User, current_user.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='User not found')

    from app.core.security import verify_password

    if not verify_password(data.old_password, user.password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail='Incorrect current password'
        )

    await user_service.change_password(db, user, data.new_password)
