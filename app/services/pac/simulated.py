"""The simulated PAC (spec 039, research R6). Its cancellation operations arrive with US2.

It keeps one `pac_simulation` row per document, written in its own session and committed at once,
so a stamp it issued survives even when the API never stores it (a timeout, FR-007). The stamps it
issues carry `SIM010101SIM` as provider RFC, which marks the document as simulated.
"""

import base64
import logging
import secrets
import uuid as uuid_lib

from lxml import etree
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import local_now
from app.db.session import AsyncSessionLocal
from app.models.fiscal import PacSimulation
from app.services.pac.provider import (
    SIMULATED_PAC_RFC,
    PacRejected,
    PacTimeout,
    PacUnavailable,
    Stamp,
)

log = logging.getLogger(__name__)

_CFDI = 'http://www.sat.gob.mx/cfd/4'
_TFD = 'http://www.sat.gob.mx/TimbreFiscalDigital'
_XSI = 'http://www.w3.org/2001/XMLSchema-instance'
_TFD_XSD = 'http://www.sat.gob.mx/sitio_internet/cfd/TimbreFiscalDigital/TimbreFiscalDigitalv11.xsd'
SAT_CERTIFICATE = '00001000000000000000'
DEFAULT_PAC_CODE = 'CFDI40999'
DEFAULT_PAC_MESSAGE = 'Rechazo simulado'

#: How the simulator routes name a document: (stamp_uuid or None, issuer, batch or '', serial).
Keys = tuple[str | None, str, str, int | None]


def _identity(sealed_xml: str) -> tuple[str, str, int | None, str]:
    """Issuer RFC, series ('' when absent), folio and the issuer's seal of a sealed CFDI."""
    root = etree.fromstring(sealed_xml.encode('utf-8'))
    issuer = root.find(f'{{{_CFDI}}}Emisor')
    folio = root.get('Folio')
    return (
        issuer.get('Rfc', '') if issuer is not None else '',
        root.get('Serie') or '',
        int(folio) if folio else None,
        root.get('Sello', ''),
    )


def _stamped_xml(sealed_xml: str, uuid: str, stamped_at: str, sat_seal: str) -> str:
    """The sealed CFDI with a TFD 1.1 inside `cfdi:Complemento`, created if absent."""
    root = etree.fromstring(sealed_xml.encode('utf-8'))
    complement = root.find(f'{{{_CFDI}}}Complemento')
    if complement is None:
        complement = etree.SubElement(root, f'{{{_CFDI}}}Complemento')
    timbre = etree.SubElement(
        complement, f'{{{_TFD}}}TimbreFiscalDigital', nsmap={'tfd': _TFD, 'xsi': _XSI}
    )
    timbre.set(f'{{{_XSI}}}schemaLocation', f'{_TFD} {_TFD_XSD}')
    for name, value in (
        ('Version', '1.1'),
        ('UUID', uuid),
        ('FechaTimbrado', stamped_at),
        ('RfcProvCertif', SIMULATED_PAC_RFC),
        ('SelloCFD', root.get('Sello', '')),
        ('NoCertificadoSAT', SAT_CERTIFICATE),
        ('SelloSAT', sat_seal),
    ):
        timbre.set(name, value)
    return '<?xml version="1.0" encoding="utf-8"?>' + etree.tostring(root, encoding='unicode')


def _stamp_of(row: PacSimulation, seal: str) -> Stamp | None:
    """The row's stamp, when it is the stamp of the document sealed with `seal`."""
    if row.uuid is None or row.stamped_xml is None or row.stamped_at is None:
        return None
    timbre = etree.fromstring(row.stamped_xml.encode('utf-8')).find(
        f'.//{{{_TFD}}}TimbreFiscalDigital'
    )
    if timbre is None or timbre.get('SelloCFD') != seal:
        return None
    return Stamp(
        uuid=row.uuid,
        stamped_at=row.stamped_at,
        sat_seal=timbre.get('SelloSAT', ''),
        sat_certificate=timbre.get('NoCertificadoSAT', ''),
        provider_rfc=timbre.get('RfcProvCertif', ''),
        xml=row.stamped_xml,
    )


class SimulatedPac:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession] = AsyncSessionLocal
    ) -> None:
        self.session_factory = session_factory

    @staticmethod
    async def _row(db: AsyncSession, keys: Keys) -> PacSimulation:
        """The document's row: by UUID when it has one, else by issuer, series and folio. Created
        when missing."""
        stamp_uuid, issuer, batch, serial = keys
        if stamp_uuid is not None:
            query = select(PacSimulation).where(PacSimulation.uuid == stamp_uuid.upper())
        else:
            query = select(PacSimulation).where(
                PacSimulation.issuer == issuer,
                PacSimulation.batch == batch,
                PacSimulation.serial == serial,
            )
        row = (await db.execute(query)).scalars().first()
        if row is None:
            row = PacSimulation(
                issuer=issuer,
                batch=batch,
                serial=serial,
                uuid=stamp_uuid.upper() if stamp_uuid is not None else None,
                sat_status='valid',
                cancellation_status='none',
                updated_at=local_now().replace(microsecond=0),
            )
            db.add(row)
        return row

    async def stamp(self, sealed_xml: str) -> Stamp:
        issuer, batch, serial, seal = _identity(sealed_xml)
        # One line per call and never the XML: quickstart §3 counts these lines (SC-002).
        log.warning('pac simulator: stamp call %s %s%s', issuer, batch, serial)
        async with self.session_factory() as db:
            row = await self._row(db, (None, issuer, batch, serial))
            outcome = row.next_stamp or 'stamped'
            code = row.pac_code or DEFAULT_PAC_CODE
            message = row.pac_message or DEFAULT_PAC_MESSAGE
            row.next_stamp = None
            if outcome == 'rejected':
                row.pac_code = row.pac_message = None
            stamp = None
            if outcome in ('stamped', 'timeout_stamped'):
                # A document it already stamped gets that stamp back, as SAT's 307 does.
                stamp = _stamp_of(row, seal)
                if stamp is None:
                    stamped_at = local_now().replace(microsecond=0)
                    sat_seal = base64.b64encode(secrets.token_bytes(256)).decode('ascii')
                    row.uuid = str(uuid_lib.uuid4()).upper()
                    row.stamped_at = stamped_at
                    row.stamped_xml = _stamped_xml(
                        sealed_xml, row.uuid, stamped_at.isoformat(), sat_seal
                    )
                    stamp = _stamp_of(row, seal)
            row.updated_at = local_now().replace(microsecond=0)
            await db.commit()
        if outcome == 'rejected':
            raise PacRejected(code, message)
        if outcome == 'unavailable':
            raise PacUnavailable()
        if outcome in ('timeout_stamped', 'timeout_not_stamped'):
            raise PacTimeout()
        return stamp

    async def find_stamp(self, sealed_xml: str) -> Stamp | None:
        issuer, batch, serial, seal = _identity(sealed_xml)
        async with self.session_factory() as db:
            query = select(PacSimulation).where(
                PacSimulation.issuer == issuer,
                PacSimulation.batch == batch,
                PacSimulation.serial == serial,
            )
            row = (await db.execute(query)).scalars().first()
            return _stamp_of(row, seal) if row is not None else None

    async def set_next(
        self,
        keys: Keys,
        *,
        stamp: str | None = None,
        cancellation: str | None = None,
        pac_code: str | None = None,
        pac_message: str | None = None,
    ) -> None:
        """The outcome of this document's next stamp or cancellation call only (R6)."""
        async with self.session_factory() as db:
            row = await self._row(db, keys)
            if stamp is not None:
                row.next_stamp = stamp
            if cancellation is not None:
                row.next_cancellation = cancellation
            row.pac_code, row.pac_message = pac_code, pac_message
            row.updated_at = local_now().replace(microsecond=0)
            await db.commit()

    async def get_record(self, keys: Keys) -> dict[str, str | None]:
        """What the simulator holds for the document."""
        async with self.session_factory() as db:
            row = await self._row(db, keys)
            await db.commit()
            return {
                'uuid': row.uuid,
                'sat_status': row.sat_status,
                'cancellation_status': row.cancellation_status,
                'next_stamp': row.next_stamp,
                'next_cancellation': row.next_cancellation,
            }
