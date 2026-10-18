from pydantic import BaseModel

from app.core.password_policy import NewPassword


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = 'bearer'


class PasswordPolicyResponse(BaseModel):
    min_length: int
    require_upper: bool
    require_lower: bool
    require_digit: bool
    require_symbol: bool


class RecoverPasswordAdminResponse(BaseModel):
    """Returned to admin after triggering a password recovery for a user."""

    recovery_token: str
    expires_at: str  # ISO-8601 datetime


class ConfirmRecoveryRequest(BaseModel):
    recovery_token: str
    new_password: NewPassword
