"""The one PAC implementation: the contract over the CFDI library's clients (spec 040, R2, R3).

`get_pac_provider()` builds it over the library's Multifacturas and SAT status clients, or over its
simulated PAC and simulated SAT status. Every value is translated here, and every error the library
documents becomes one of the contract's three, raised `from None` so no payload rides along.
"""

import logging
from decimal import Decimal
from zoneinfo import ZoneInfo

from lxml import etree
from mictlanix.cfdi import ValidationError
from mictlanix.cfdi import pac as lib
from mictlanix.cfdi import sat as libsat

from app.services.pac.provider import (
    CancellationAnswer,
    CancellationRequest,
    PacRejected,
    PacTimeout,
    PacUnavailable,
    SatStatus,
    Stamp,
)

log = logging.getLogger(__name__)

_CFDI = 'http://www.sat.gob.mx/cfd/4'
_MEXICO_CITY = ZoneInfo('America/Mexico_City')


def _identity(sealed_xml: str) -> str:
    """`{issuer} {series}{folio}` of a sealed CFDI, for the stamp log line."""
    root = etree.fromstring(sealed_xml.encode('utf-8'))
    issuer = root.find(f'{{{_CFDI}}}Emisor')
    rfc = issuer.get('Rfc', '') if issuer is not None else ''
    return f'{rfc} {root.get("Serie") or ""}{root.get("Folio") or ""}'


def _stamp(stamp: lib.Stamp) -> Stamp:
    timbre = stamp.timbre
    stamped_at = timbre.FechaTimbrado
    if stamped_at.tzinfo is not None:
        stamped_at = stamped_at.astimezone(_MEXICO_CITY).replace(tzinfo=None)
    return Stamp(
        uuid=timbre.UUID.upper(),
        stamped_at=stamped_at,
        sat_seal=timbre.SelloSAT,
        sat_certificate=timbre.NoCertificadoSAT,
        provider_rfc=timbre.RfcProvCertif,
        xml=stamp.xml.decode('utf-8'),
    )


class _Translated:
    """Turns the library's errors into the contract's, with nothing from the original attached."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, kind, error, traceback) -> bool:  # noqa: ANN001
        if error is None:
            return False
        if isinstance(error, (lib.PacRejected, libsat.SatRejected)):
            raise PacRejected(error.code, error.message) from None
        if isinstance(error, (lib.PacUnavailable, libsat.SatUnavailable)):
            raise PacUnavailable() from None
        if isinstance(error, (lib.PacTimeout, libsat.SatTimeout)):
            raise PacTimeout() from None
        return False


class CfdiPacProvider:
    def __init__(
        self,
        pac: lib.PacClient,
        sat: libsat.SatStatusClient,
        *,
        name: str,
        resolves_by_resubmitting: bool,
    ) -> None:
        self._pac = pac
        self._sat = sat
        self.name = name
        self.resolves_by_resubmitting = resolves_by_resubmitting

    def __repr__(self) -> str:
        return f'CfdiPacProvider({self._pac!r}, {self._sat!r}, name={self.name!r})'

    async def stamp(self, sealed_xml: str) -> Stamp:
        # One line per call and never the XML: spec 039's quickstart counts these lines (SC-002).
        log.warning('pac %s: stamp call %s', self.name, _identity(sealed_xml))
        with _Translated():
            return _stamp(await self._pac.stamp(sealed_xml.encode('utf-8')))

    async def find_stamp(self, sealed_xml: str) -> Stamp | None:
        with _Translated():
            found = await self._pac.find_stamp(sealed_xml.encode('utf-8'))
        return None if found is None else _stamp(found)

    async def request_cancellation(self, request: CancellationRequest) -> CancellationAnswer:
        try:
            library_request = lib.CancellationRequest(
                issuer_rfc=request.issuer_rfc,
                recipient_rfc=request.recipient_rfc,
                uuid=request.uuid,
                total=request.total,
                reason=request.reason,
                substitute_uuid=request.substitution,
                csd=lib.CsdCredentials(
                    certificate=request.csd.certificate,
                    private_key=request.csd.key,
                    password=request.csd.password.decode('utf-8'),
                ),
            )
        except ValidationError as error:
            # The summary names rules and nodes, never values (research R3).
            raise PacRejected(error.violations[0].rule, str(error)) from None
        with _Translated():
            answer = await self._pac.request_cancellation(library_request)
        return CancellationAnswer[answer.name]

    async def query_status(
        self, issuer_rfc: str, recipient_rfc: str, uuid: str, total: Decimal
    ) -> SatStatus:
        with _Translated():
            status = await self._sat.query_status(issuer_rfc, recipient_rfc, uuid, total)
        return SatStatus(
            state=status.state.value,
            cancellable=status.cancellability.value,
            cancellation=status.cancellation.value,
        )
