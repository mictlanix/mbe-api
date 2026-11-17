"""The PAC provider in use (spec 039): `provider.py` is the interface, this picks the class."""

from app.core.config import settings
from app.core.error_codes import ErrorCode
from app.core.errors import DomainError
from app.services.pac.provider import PacProvider


def get_pac_provider() -> PacProvider | None:
    """The configured provider, or None while there is none. A FastAPI dependency."""
    if settings.pac_provider == 'simulated':
        from app.services.pac.simulated import SimulatedPac

        return SimulatedPac()
    return None


def require_provider(provider: PacProvider | None) -> PacProvider:
    if provider is None:
        raise DomainError(ErrorCode.PAC_NOT_CONFIGURED, 'No PAC provider is configured')
    return provider
