"""The PAC provider in use (specs 039, 040): `provider.py` is the interface, and the one
implementation is `adapter.CfdiPacProvider` over the CFDI library's clients, picked here by
`PAC_PROVIDER`."""

from mictlanix.cfdi.pac import MultifacturasPac, MultifacturasSettings, SimulatedPac
from mictlanix.cfdi.sat import SatConsultaClient, SimulatedSatStatusClient

from app.core.config import settings
from app.core.error_codes import ErrorCode
from app.core.errors import DomainError
from app.services.pac.adapter import CfdiPacProvider
from app.services.pac.provider import PacProvider
from app.services.pac.simulator_store import DatabaseSimulatorStore


def get_pac_provider() -> PacProvider | None:
    """The configured provider, or None while there is none. A FastAPI dependency."""
    if settings.pac_provider == 'simulated':
        # The library's simulated PAC, its state on the database (spec 040, R10).
        pac = SimulatedPac(DatabaseSimulatorStore())
        return CfdiPacProvider(
            pac, SimulatedSatStatusClient(pac), name='simulator', resolves_by_resubmitting=False
        )
    if settings.pac_provider == 'multifacturas':
        password = settings.multifacturas_password
        return CfdiPacProvider(
            MultifacturasPac(
                MultifacturasSettings(
                    user=settings.multifacturas_user or '',
                    password=password.get_secret_value() if password is not None else '',
                    production=settings.multifacturas_production,
                    base_url=settings.multifacturas_url,
                    connect_timeout=settings.multifacturas_connect_timeout,
                    read_timeout=settings.multifacturas_read_timeout,
                )
            ),
            SatConsultaClient(),
            name='multifacturas',
            resolves_by_resubmitting=True,
        )
    return None


def require_provider(provider: PacProvider | None) -> PacProvider:
    if provider is None:
        raise DomainError(ErrorCode.PAC_NOT_CONFIGURED, 'No PAC provider is configured')
    return provider
