from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = 'bearer'


class RecoverPasswordAdminResponse(BaseModel):
    """Returned to admin after triggering a password recovery for a user."""

    recovery_token: str
    expires_at: str  # ISO-8601 datetime


class ConfirmRecoveryRequest(BaseModel):
    recovery_token: str
    new_password: str = Field(min_length=6)
