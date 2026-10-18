"""The password-strength policy, applied wherever a password is set and published to clients (#241).

One validator for every path, read from settings at validation time, so a deployment that changes
the policy changes enforcement and `GET /auth/password-policy` together. Existing passwords are
never re-checked: login compares hashes and never sees this.
"""

from typing import Annotated

from pydantic import AfterValidator
from pydantic_core import PydanticCustomError

from app.core.config import settings


def current_policy() -> dict[str, int | bool]:
    return {
        'min_length': settings.password_min_length,
        'require_upper': settings.password_require_upper,
        'require_lower': settings.password_require_lower,
        'require_digit': settings.password_require_digit,
        'require_symbol': settings.password_require_symbol,
    }


def failed_rules(password: str) -> list[str]:
    """The names of the rules `password` breaks, in policy order — the client maps each to text."""
    policy = current_policy()
    checks = {
        'min_length': len(password) >= policy['min_length'],
        'require_upper': not policy['require_upper'] or any(c.isupper() for c in password),
        'require_lower': not policy['require_lower'] or any(c.islower() for c in password),
        'require_digit': not policy['require_digit'] or any(c.isdigit() for c in password),
        'require_symbol': not policy['require_symbol']
        or any(not c.isalnum() and not c.isspace() for c in password),
    }
    return [rule for rule, passed in checks.items() if not passed]


def _enforce(password: str) -> str:
    failed = failed_rules(password)
    if failed:
        # A typed error rather than free text: it reaches the client as
        # `{"type": "password_policy", "loc": [...], "ctx": {"failed": [...]}}`.
        raise PydanticCustomError(
            'password_policy',
            'Password does not meet the policy: {failed}',
            {'failed': failed},
        )
    return password


#: A password being set. Use on every request field that sets one, never a bare `min_length`.
NewPassword = Annotated[str, AfterValidator(_enforce)]
