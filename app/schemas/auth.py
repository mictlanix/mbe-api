from pydantic import BaseModel

from app.core.password_policy import NewPassword


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = 'bearer'
    #: Saves a round trip to `/auth/me`: the client can send the user straight to change it (#240).
    must_change_password: bool = False


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
