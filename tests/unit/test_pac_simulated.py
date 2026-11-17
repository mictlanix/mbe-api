"""The simulated PAC (spec 039, research R6): stamps, look-ups and one-shot outcomes.

It keeps its state in `pac_simulation` through its own sessions, so each test gives it a private
SQLite database holding that table.
"""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from lxml import etree
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models.fiscal import PacSimulation
from app.services import cfdi
from app.services.pac.provider import (
    SIMULATED_PAC_RFC,
    PacRejected,
    PacTimeout,
    PacUnavailable,
)
from app.services.pac.simulated import SimulatedPac

_FIXTURE = Path(__file__).parent.parent / 'fixtures' / 'cfdi' / 'invoice.xml'
_ISSUER = 'EKU9003173C9'


def _sealed(*, serie: str | None = 'RH', folio: str = '11405') -> str:
    """Fixture invoice RH 11405 without its stamp: sealed, waiting for the PAC."""
    root = etree.fromstring(_FIXTURE.read_bytes())
    timbre = root.find('.//{*}TimbreFiscalDigital')
    complement = timbre.getparent()
    complement.remove(timbre)
    if len(complement) == 0:
        root.remove(complement)
    if serie is None:
        del root.attrib['Serie']
    else:
        root.set('Serie', serie)
    root.set('Folio', folio)
    return '<?xml version="1.0" encoding="utf-8"?>' + etree.tostring(root, encoding='unicode')


def _keys(batch: str = 'RH', serial: int = 11405) -> tuple[None, str, str, int]:
    return (None, _ISSUER, batch, serial)


@pytest.fixture
async def sessions(tmp_path: Path) -> AsyncIterator[async_sessionmaker]:
    engine = create_async_engine(
        f'sqlite+aiosqlite:///{tmp_path / "pac.sqlite"}',
        poolclass=StaticPool,
        connect_args={'check_same_thread': False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all, tables=[PacSimulation.__table__])
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def pac(sessions: async_sessionmaker) -> SimulatedPac:
    return SimulatedPac(session_factory=sessions)


async def _rows(sessions: async_sessionmaker) -> list[PacSimulation]:
    async with sessions() as db:
        return list((await db.execute(select(PacSimulation))).scalars())


# ── Stamp ─────────────────────────────────────────────────────────────────────


async def test_stamp_returns_a_tfd_11_that_parse_reads_back(pac: SimulatedPac) -> None:
    sealed = _sealed()

    stamp = await pac.stamp(sealed)

    doc = cfdi.parse(stamp.xml)
    timbre = doc.timbre
    assert timbre['Version'] == '1.1'
    assert timbre['UUID'] == stamp.uuid
    assert len(stamp.uuid) == 36 and stamp.uuid == stamp.uuid.upper()
    assert timbre['SelloCFD'] == etree.fromstring(sealed.encode()).get('Sello')
    assert timbre['SelloSAT'] == stamp.sat_seal and len(stamp.sat_seal) == 344
    assert timbre['NoCertificadoSAT'] == stamp.sat_certificate
    assert len(stamp.sat_certificate) == 20 and stamp.sat_certificate.isdigit()
    assert timbre['RfcProvCertif'] == stamp.provider_rfc == SIMULATED_PAC_RFC == 'SIM010101SIM'
    assert timbre['FechaTimbrado'] == stamp.stamped_at.isoformat()
    assert stamp.stamped_at.microsecond == 0
    # The stamp goes inside cfdi:Complemento, and the document is otherwise unchanged.
    root = etree.fromstring(stamp.xml.encode())
    assert root.find('{*}Complemento/{*}TimbreFiscalDigital') is not None
    assert doc.comprobante['Sello'] == timbre['SelloCFD']
    assert (doc.comprobante['Serie'], doc.comprobante['Folio']) == ('RH', '11405')


async def test_stamp_keys_its_row_by_issuer_series_and_folio(
    pac: SimulatedPac, sessions: async_sessionmaker
) -> None:
    stamp = await pac.stamp(_sealed())

    [row] = await _rows(sessions)
    assert (row.issuer, row.batch, row.serial, row.uuid) == (_ISSUER, 'RH', 11405, stamp.uuid)
    assert row.stamped_xml == stamp.xml
    assert (await pac.get_record(_keys()))['uuid'] == stamp.uuid


async def test_a_cfdi_without_series_is_keyed_with_an_empty_batch(
    pac: SimulatedPac, sessions: async_sessionmaker
) -> None:
    stamp = await pac.stamp(_sealed(serie=None, folio='7'))

    [row] = await _rows(sessions)
    assert (row.batch, row.serial) == ('', 7)
    assert (await pac.get_record(_keys('', 7)))['uuid'] == stamp.uuid


async def test_find_stamp_returns_the_stamp_only_after_stamping(pac: SimulatedPac) -> None:
    sealed = _sealed()
    assert await pac.find_stamp(sealed) is None

    stamp = await pac.stamp(sealed)

    assert await pac.find_stamp(sealed) == stamp


async def test_a_second_stamp_of_the_same_sealed_xml_returns_the_stored_stamp(
    pac: SimulatedPac, sessions: async_sessionmaker
) -> None:
    sealed = _sealed()
    first = await pac.stamp(sealed)

    second = await pac.stamp(sealed)

    assert second == first
    assert len(await _rows(sessions)) == 1


async def test_each_stamp_call_logs_one_warning_without_the_xml(
    pac: SimulatedPac, caplog: pytest.LogCaptureFixture
) -> None:
    sealed = _sealed()
    with caplog.at_level('WARNING'):
        await pac.stamp(sealed)
        await pac.stamp(sealed)

    lines = [r.getMessage() for r in caplog.records if 'pac simulator' in r.getMessage()]
    assert lines == [f'pac simulator: stamp call {_ISSUER} RH11405'] * 2
    assert all(r.levelname == 'WARNING' for r in caplog.records)
    assert 'Sello' not in caplog.text


# ── One-shot outcomes ─────────────────────────────────────────────────────────


async def test_rejected_uses_the_default_code_and_message(pac: SimulatedPac) -> None:
    await pac.set_next(_keys(), stamp='rejected')

    with pytest.raises(PacRejected) as caught:
        await pac.stamp(_sealed())

    assert (caught.value.code, caught.value.message) == ('CFDI40999', 'Rechazo simulado')
    assert await pac.find_stamp(_sealed()) is None


async def test_rejected_uses_a_chosen_code_and_message(pac: SimulatedPac) -> None:
    await pac.set_next(_keys(), stamp='rejected', pac_code='CFDI40102', pac_message='Sello')

    with pytest.raises(PacRejected) as caught:
        await pac.stamp(_sealed())

    assert (caught.value.code, caught.value.message) == ('CFDI40102', 'Sello')


async def test_unavailable_raises_and_stamps_nothing(pac: SimulatedPac) -> None:
    await pac.set_next(_keys(), stamp='unavailable')

    with pytest.raises(PacUnavailable):
        await pac.stamp(_sealed())

    assert await pac.find_stamp(_sealed()) is None


async def test_timeout_stamped_stores_the_stamp_then_raises(pac: SimulatedPac) -> None:
    await pac.set_next(_keys(), stamp='timeout_stamped')

    with pytest.raises(PacTimeout):
        await pac.stamp(_sealed())

    found = await pac.find_stamp(_sealed())
    assert found is not None
    assert (await pac.get_record(_keys()))['uuid'] == found.uuid
    # The next stamp returns that same stamp: the PAC never stamps twice.
    assert await pac.stamp(_sealed()) == found


async def test_timeout_not_stamped_raises_without_storing(pac: SimulatedPac) -> None:
    await pac.set_next(_keys(), stamp='timeout_not_stamped')

    with pytest.raises(PacTimeout):
        await pac.stamp(_sealed())

    assert await pac.find_stamp(_sealed()) is None


@pytest.mark.parametrize(
    'outcome', ['rejected', 'unavailable', 'timeout_not_stamped', 'timeout_stamped']
)
async def test_an_outcome_applies_once_then_reverts_to_stamped(
    pac: SimulatedPac, outcome: str
) -> None:
    await pac.set_next(_keys(), stamp=outcome)
    assert (await pac.get_record(_keys()))['next_stamp'] == outcome

    with pytest.raises((PacRejected, PacUnavailable, PacTimeout)):
        await pac.stamp(_sealed())

    assert (await pac.get_record(_keys()))['next_stamp'] is None
    stamp = await pac.stamp(_sealed())
    assert stamp.provider_rfc == SIMULATED_PAC_RFC


async def test_get_record_reports_the_row(pac: SimulatedPac) -> None:
    assert await pac.get_record(_keys()) == {
        'uuid': None,
        'sat_status': 'valid',
        'cancellation_status': 'none',
        'next_stamp': None,
        'next_cancellation': None,
    }
