"""An in-memory `PacProvider` that is not the simulator (spec 039, research R13, SC-006).

Results and raised errors are queued per operation (`queue('stamp', PacTimeout())`); an empty queue
falls back to a plain default, so a flow that scripts nothing still runs. Calls are counted per
operation. The stamps it issues carry a provider RFC other than `SIM010101SIM`, so a print of one
is never taken for simulated.
"""

import re
from collections import Counter, defaultdict, deque
from decimal import Decimal

from lxml import etree

from app.core.clock import local_now
from app.services.pac.provider import CancellationAnswer, CancellationRequest, SatStatus, Stamp

SCRIPTED_PAC_RFC = 'SCR010101SCR'
_TFD = 'http://www.sat.gob.mx/TimbreFiscalDigital'
_CFDI = 'http://www.sat.gob.mx/cfd/4'

OPERATIONS = ('stamp', 'find_stamp', 'request_cancellation', 'query_status')


class ScriptedPac:
    def __init__(self) -> None:
        self.queues: dict[str, deque] = defaultdict(deque)
        self.calls: Counter[str] = Counter()
        self.stamped: dict[str, Stamp] = {}  # by the sealed XML's `Sello`
        self.requests: list[CancellationRequest] = []

    def queue(self, operation: str, *outcomes: object) -> None:
        """Queue results or exceptions for `operation`; each is used once, in order."""
        assert operation in OPERATIONS, operation
        self.queues[operation].extend(outcomes)

    def _next(self, operation: str) -> object | None:
        self.calls[operation] += 1
        queue = self.queues[operation]
        outcome = queue.popleft() if queue else None
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def _make_stamp(self, sealed_xml: str) -> Stamp:
        root = etree.fromstring(sealed_xml.encode('utf-8'))
        seal = root.get('Sello', '')
        stamped_at = local_now().replace(microsecond=0)
        uuid = f'{len(self.stamped) + 1:08X}-5C0A-4D1E-9F00-000000000000'
        timbre = etree.Element(
            f'{{{_TFD}}}TimbreFiscalDigital',
            nsmap={'tfd': _TFD},
            Version='1.1',
            UUID=uuid,
            FechaTimbrado=stamped_at.isoformat(timespec='seconds'),
            RfcProvCertif=SCRIPTED_PAC_RFC,
            SelloCFD=seal,
            NoCertificadoSAT='30001000000500003456',
            SelloSAT='S' * 344,
        )
        complement = root.find(f'{{{_CFDI}}}Complemento')
        if complement is None:
            complement = etree.SubElement(root, f'{{{_CFDI}}}Complemento')
        complement.append(timbre)
        xml = etree.tostring(root, encoding='unicode')
        return Stamp(
            uuid=uuid,
            stamped_at=stamped_at,
            sat_seal='S' * 344,
            sat_certificate='30001000000500003456',
            provider_rfc=SCRIPTED_PAC_RFC,
            xml=xml,
        )

    @staticmethod
    def _seal(sealed_xml: str) -> str:
        match = re.search(r'\sSello="([^"]*)"', sealed_xml)
        return match.group(1) if match else sealed_xml

    async def stamp(self, sealed_xml: str) -> Stamp:
        outcome = self._next('stamp')
        if isinstance(outcome, Stamp):
            self.stamped[self._seal(sealed_xml)] = outcome
            return outcome
        existing = self.stamped.get(self._seal(sealed_xml))
        if existing is not None:
            return existing
        stamp = self._make_stamp(sealed_xml)
        self.stamped[self._seal(sealed_xml)] = stamp
        return stamp

    async def find_stamp(self, sealed_xml: str) -> Stamp | None:
        outcome = self._next('find_stamp')
        if isinstance(outcome, Stamp):
            return outcome
        return self.stamped.get(self._seal(sealed_xml))

    async def request_cancellation(self, request: CancellationRequest) -> CancellationAnswer:
        outcome = self._next('request_cancellation')
        self.requests.append(request)
        return outcome if isinstance(outcome, CancellationAnswer) else CancellationAnswer.ACCEPTED

    async def query_status(
        self, issuer_rfc: str, recipient_rfc: str, uuid: str, total: Decimal
    ) -> SatStatus:
        outcome = self._next('query_status')
        if isinstance(outcome, SatStatus):
            return outcome
        return SatStatus(state='valid', cancellable='without_acceptance', cancellation='none')
