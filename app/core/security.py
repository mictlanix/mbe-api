import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from jose import jwt

from app.core.config import settings


def sha1_hash(password: str) -> str:
    return hashlib.sha1(password.encode()).hexdigest()


def verify_password(plain: str, hashed: str) -> bool:
    return sha1_hash(plain).upper() == hashed.upper()


def create_access_token(
    user_id: str, session_version: int, administrator: bool, facility_id: int | None
) -> str:
    expire = datetime.now(UTC) + timedelta(minutes=settings.jwt_access_token_expire_minutes)
    payload = {
        "sub": user_id,
        "session_version": session_version,
        "administrator": administrator,
        "facility_id": facility_id,
        "exp": expire,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_recovery_token(user_id: str) -> tuple[str, datetime]:
    expire = datetime.now(UTC) + timedelta(hours=settings.jwt_recovery_token_expire_hours)
    payload = {"sub": user_id, "type": "recovery", "exp": expire}
    token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return token, expire


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])


def random_password(length: int = 12) -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))
