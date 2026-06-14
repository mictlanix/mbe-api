import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from jose import jwt
from passlib.context import CryptContext

from app.core.config import settings

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def sha1_hash(password: str) -> str:
    return hashlib.sha1(password.encode()).hexdigest()


def verify_sha1(plain: str, hashed: str) -> bool:
    return sha1_hash(plain) == hashed


def bcrypt_hash(password: str) -> str:
    return _pwd_context.hash(password)


def verify_bcrypt(plain: str, hashed: str) -> bool:
    return _pwd_context.verify(plain, hashed)


def verify_password(plain: str, hashed: str, scheme: str) -> bool:
    if scheme == "sha1":
        return verify_sha1(plain, hashed)
    return verify_bcrypt(plain, hashed)


def create_access_token(
    user_id: str, session_version: int, administrator: bool, store_id: int | None
) -> str:
    expire = datetime.now(UTC) + timedelta(minutes=settings.jwt_access_token_expire_minutes)
    payload = {
        "sub": user_id,
        "session_version": session_version,
        "administrator": administrator,
        "store_id": store_id,
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
