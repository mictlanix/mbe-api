"""The simulated PAC (spec 039, research R6): stamps, look-ups, cancellations and one-shot outcomes.

It keeps its state in `pac_simulation` through its own sessions, so each test gives it a private
SQLite database holding that table.
"""

from collections.abc import AsyncIterator
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from lxml import etree
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.error_codes import ErrorCode
from app.core.errors import DomainError
from app.db.base import Base
from app.models.fiscal import PacSimulation
from app.services import cfdi
from app.services.pac.provider import (
    SIMULATED_PAC_RFC,
    CancellationAnswer,
    CancellationRequest,
    Csd,
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


# ── Cancellation ──────────────────────────────────────────────────────────────

_RECIPIENT = 'XAÑ010101AB1'
_UNKNOWN = '6F3A2B1C-0D4E-4F5A-8B9C-0123456789AB'


def _request(uuid: str, reason: str = '02') -> CancellationRequest:
    return CancellationRequest(
        issuer_rfc=_ISSUER,
        recipient_rfc=_RECIPIENT,
        uuid=uuid,
        total=Decimal('3160.00'),
        reason=reason,
        substitution=None,
        csd=Csd(certificate=b'cert', key=b'key', password=b'secret'),
    )


async def _status(pac: SimulatedPac, uuid: str) -> tuple[str, str]:
    status = await pac.query_status(_ISSUER, _RECIPIENT, uuid, Decimal('3160.00'))
    return status.state, status.cancellation


async def _stamped_hours_ago(pac: SimulatedPac, sessions: async_sessionmaker, hours: float) -> str:
    stamp = await pac.stamp(_sealed())
    async with sessions() as db:
        await db.execute(
            update(PacSimulation)
            .where(PacSimulation.uuid == stamp.uuid)
            .values(stamped_at=stamp.stamped_at - timedelta(hours=hours))
        )
        await db.commit()
    return stamp.uuid


@pytest.mark.parametrize(
    ('hours', 'answer', 'status'),
    [
        (0, CancellationAnswer.ACCEPTED, ('cancelled', 'accepted')),
        (23.9, CancellationAnswer.ACCEPTED, ('cancelled', 'accepted')),
        (24, CancellationAnswer.PENDING, ('valid', 'pending')),
        (48, CancellationAnswer.PENDING, ('valid', 'pending')),
    ],
)
async def test_the_default_rule_accepts_a_stamp_under_24_hours_old(
    pac: SimulatedPac,
    sessions: async_sessionmaker,
    hours: float,
    answer: CancellationAnswer,
    status: tuple[str, str],
) -> None:
    uuid = await _stamped_hours_ago(pac, sessions, hours)

    assert await pac.request_cancellation(_request(uuid)) == answer
    assert await _status(pac, uuid) == status


@pytest.mark.parametrize(
    ('outcome', 'answer', 'status'),
    [
        ('accepted', CancellationAnswer.ACCEPTED, ('cancelled', 'accepted')),
        ('pending', CancellationAnswer.PENDING, ('valid', 'pending')),
        ('not_cancellable', CancellationAnswer.NOT_CANCELLABLE, ('valid', 'none')),
    ],
)
async def test_a_chosen_cancellation_answer_applies_once(
    pac: SimulatedPac,
    outcome: str,
    answer: CancellationAnswer,
    status: tuple[str, str],
) -> None:
    uuid = (await pac.stamp(_sealed())).uuid
    await pac.set_next(_keys(), cancellation=outcome)
    assert (await pac.get_record(_keys()))['next_cancellation'] == outcome

    assert await pac.request_cancellation(_request(uuid)) == answer

    assert await _status(pac, uuid) == status
    assert (await pac.get_record(_keys()))['next_cancellation'] is None


async def test_a_chosen_rejection_raises_the_chosen_code(pac: SimulatedPac) -> None:
    uuid = (await pac.stamp(_sealed())).uuid
    await pac.set_next(
        _keys(), cancellation='rejected', pac_code='CANC205', pac_message='UUID no encontrado'
    )

    with pytest.raises(PacRejected) as caught:
        await pac.request_cancellation(_request(uuid))

    assert (caught.value.code, caught.value.message) == ('CANC205', 'UUID no encontrado')
    assert await _status(pac, uuid) == ('valid', 'none')
    # One-shot: the next request follows the default rule.
    assert await pac.request_cancellation(_request(uuid)) == CancellationAnswer.ACCEPTED


async def test_a_rejection_without_a_chosen_code_uses_the_default(pac: SimulatedPac) -> None:
    uuid = (await pac.stamp(_sealed())).uuid
    await pac.set_next(_keys(), cancellation='rejected')

    with pytest.raises(PacRejected) as caught:
        await pac.request_cancellation(_request(uuid))

    assert (caught.value.code, caught.value.message) == ('CFDI40999', 'Rechazo simulado')


async def test_unavailable_raises_and_changes_nothing(pac: SimulatedPac) -> None:
    uuid = (await pac.stamp(_sealed())).uuid
    await pac.set_next(_keys(), cancellation='unavailable')

    with pytest.raises(PacUnavailable):
        await pac.request_cancellation(_request(uuid))

    assert await _status(pac, uuid) == ('valid', 'none')


async def test_timeout_records_the_request_as_pending_then_raises(pac: SimulatedPac) -> None:
    uuid = (await pac.stamp(_sealed())).uuid
    await pac.set_next(_keys(), cancellation='timeout')

    with pytest.raises(PacTimeout):
        await pac.request_cancellation(_request(uuid))

    assert await _status(pac, uuid) == ('valid', 'pending')


@pytest.mark.parametrize(
    ('answer', 'status'),
    [
        ('accept', ('cancelled', 'accepted')),
        ('reject', ('valid', 'rejected')),
        ('expire', ('cancelled', 'expired')),
    ],
)
async def test_the_receiver_answers_a_pending_cancellation(
    pac: SimulatedPac, answer: str, status: tuple[str, str]
) -> None:
    uuid = (await pac.stamp(_sealed())).uuid
    await pac.set_next(_keys(), cancellation='pending')
    await pac.request_cancellation(_request(uuid))

    await pac.answer_as_receiver(_keys(), answer)

    assert await _status(pac, uuid) == status
    record = await pac.get_record((uuid, _ISSUER, 'RH', 11405))
    assert (record['sat_status'], record['cancellation_status']) == status


async def test_the_receiver_needs_a_pending_cancellation(pac: SimulatedPac) -> None:
    await pac.stamp(_sealed())

    with pytest.raises(DomainError) as caught:
        await pac.answer_as_receiver(_keys(), 'accept')

    assert caught.value.code == ErrorCode.FISCAL_DOCUMENT_NOTHING_TO_REFRESH
    assert caught.value.detail == 'No cancellation is pending'


async def test_an_unknown_uuid_gets_a_row_and_the_default_answers_pending(
    pac: SimulatedPac, sessions: async_sessionmaker
) -> None:
    """A document the current system stamped: the simulator never saw its stamp date."""
    assert await pac.request_cancellation(_request(_UNKNOWN.lower())) == CancellationAnswer.PENDING

    [row] = await _rows(sessions)
    assert (row.issuer, row.uuid, row.batch, row.serial, row.stamped_at) == (
        _ISSUER,
        _UNKNOWN,
        None,
        None,
        None,
    )
    assert await _status(pac, _UNKNOWN) == ('valid', 'pending')


async def test_set_next_for_an_unknown_uuid_is_followed_by_its_cancellation(
    pac: SimulatedPac, sessions: async_sessionmaker
) -> None:
    keys = (_UNKNOWN, _ISSUER, 'A', 77)
    await pac.set_next(keys, cancellation='accepted')

    assert await pac.request_cancellation(_request(_UNKNOWN)) == CancellationAnswer.ACCEPTED

    [row] = await _rows(sessions)
    assert (row.uuid, row.batch, row.serial) == (_UNKNOWN, 'A', 77)
    assert (await pac.get_record(keys))['cancellation_status'] == 'accepted'
