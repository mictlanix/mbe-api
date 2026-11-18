"""The PAC provider contract (spec 039, research R4): what the API needs from a PAC.

The API owns this interface; the simulated provider implements it now and the real PAC client
(mictlanix/mbe-roadmap#160) will later, with nothing outside `app/services/pac/` changing. Every
operation either returns its result or raises one of the errors below, and the API never parses a
provider's error text. An implementation must not put credentials, CSD material or raw PAC
payloads in an exception message or a log.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Protocol

#: The provider RFC only the simulator writes into a stamp.
SIMULATED_PAC_RFC = 'SIM010101SIM'


@dataclass(frozen=True)
class Stamp:
    """A TFD 1.1: the PAC's stamp of one sealed CFDI."""

    uuid: str  # upper case, 36 characters
    stamped_at: datetime  # FechaTimbrado, Mexico City local time, naive
    sat_seal: str  # SelloSAT
    sat_certificate: str  # NoCertificadoSAT
    provider_rfc: str  # RfcProvCertif
    xml: str  # the full stamped CFDI, with the TFD inside cfdi:Complemento


@dataclass(frozen=True)
class Csd:
    """The issuer's certificate, as `taxpayer_certificate` stores it (R14: never printed)."""

    certificate: bytes  # DER
    key: bytes  # encrypted PKCS#8
    password: bytes  # `key_password` as stored

    def __repr__(self) -> str:
        return f'Csd(certificate=<{len(self.certificate)} bytes>, key=<hidden>, password=<hidden>)'


@dataclass(frozen=True)
class CancellationRequest:
    issuer_rfc: str
    recipient_rfc: str
    uuid: str
    total: Decimal  # the document's total, as SAT's services expect
    reason: Literal['01', '02', '03', '04']
    substitution: str | None  # required for '01', None otherwise
    csd: Csd


class CancellationAnswer(StrEnum):
    ACCEPTED = 'accepted'  # cancelled at once
    PENDING = 'pending'  # SAT waits for the receiver
    NOT_CANCELLABLE = 'not_cancellable'


@dataclass(frozen=True)
class SatStatus:
    # `not_found`: SAT does not know the UUID, and the API leaves the document unchanged.
    state: Literal['valid', 'cancelled', 'not_found']
    cancellable: Literal['without_acceptance', 'with_acceptance', 'not_cancellable']
    cancellation: Literal['none', 'requested', 'pending', 'accepted', 'rejected', 'expired']


class PacError(Exception):
    """Base of the three errors a provider raises."""


class PacRejected(PacError):
    """The PAC or SAT refused the request; nothing was done. The API cuts `code` to 20 characters
    and `message` to 500."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(code)
        self.code = code
        self.message = message


class PacUnavailable(PacError):
    """The request did not reach the PAC; nothing was done, and a retry is safe."""


class PacTimeout(PacError):
    """The request may or may not have been processed; resolve it with `find_stamp` or
    `query_status`."""


class PacProvider(Protocol):
    async def stamp(self, sealed_xml: str) -> Stamp:
        """Stamp a sealed CFDI. A document the PAC already stamped (SAT 307) returns its existing
        stamp rather than raising."""
        ...

    async def find_stamp(self, sealed_xml: str) -> Stamp | None:
        """The stamp issued for this exact sealed document, or None. Never stamps."""
        ...

    async def request_cancellation(self, request: CancellationRequest) -> CancellationAnswer:
        """Ask SAT, through the PAC, to cancel the UUID. Any other refusal is `PacRejected`."""
        ...

    async def query_status(
        self, issuer_rfc: str, recipient_rfc: str, uuid: str, total: Decimal
    ) -> SatStatus:
        """SAT's current answer for the UUID. It decides whether the receiver must accept."""
        ...
