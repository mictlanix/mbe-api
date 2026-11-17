"""Stamping a sealed CFDI: state checks, the two transactions and their locks (spec 039, R3, R15).

SQLite ignores row locks, so these run against spec 029's fake session, which records every
statement as MariaDB would receive it, plus every commit and rollback. Each lock is answered with
the row the database would hand over at that moment, and each read of the XML with what it holds.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from lxml import etree

from app.core.clock import local_now
from app.core.error_codes import ErrorCode
from app.core.errors import DomainError
from app.models.incidence import Incidence
from app.services import fiscal_stamping
from app.services.pac.provider import PacRejected, PacTimeout, PacUnavailable
from tests.fakes.pac import SCRIPTED_PAC_RFC, ScriptedPac
from tests.unit.test_inventory_locks import COMMIT, FakeSession

_SEALED = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4" Version="4.0" Serie="A"'
    ' Folio="7" Sello="SELLO-EMISOR" Total="116.00"/>'
)
_OTHER = _SEALED.replace('SELLO-EMISOR', 'OTRO-SELLO')


def _row(**overrides: object) -> SimpleNamespace:
    """A sealed document as the locked read returns it."""
    values = dict(
        fiscal_document_id=9,
        completed=False,
        cancelled=False,
        stamping_since=None,
        cancelling_since=None,
        issued=local_now().replace(microsecond=0) - timedelta(hours=1),
        stamp_uuid=None,
        stamped=None,
        authority_digital_seal=None,
        authority_certificate_number=None,
        rfc_pac=None,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


class StampSession(FakeSession):
    """Each lock returns the next of `locks` and each XML read the next of `xmls`; the last one of
    each repeats, as a row nobody else touches."""

    def __init__(self, locks: list, xmls: list) -> None:
        super().__init__(self._respond)
        self.locks = list(locks)
        self.xmls = list(xmls)
        self.flushes = 0

    @staticmethod
    def _next(answers: list):  # noqa: ANN205
        return answers.pop(0) if len(answers) > 1 else answers[0]

    def _respond(self, sql: str) -> list:
        sql = ' '.join(sql.split())
        if sql.endswith('FOR UPDATE'):
            return [self._next(self.locks)]
        if sql.startswith('SELECT fiscal_document_xml.data'):
            xml = self._next(self.xmls)
            return [(xml,)] if xml is not None else []
        return []

    async def flush(self) -> None:
        self.flushes += 1

    async def get(self, _model, _key):  # noqa: ANN001, ANN201
        return None

    async def delete(self, _obj) -> None:  # noqa: ANN001
        pass


def _session(row: SimpleNamespace | None = None, xml: str | None = _SEALED) -> StampSession:
    return StampSession([row or _row()], [xml])


async def _stamp(session: StampSession, pac: ScriptedPac) -> SimpleNamespace:
    return await fiscal_stamping.stamp(session, 9, pac, 7)  # type: ignore[arg-type]


async def _refused(session: StampSession, pac: ScriptedPac) -> DomainError:
    with pytest.raises(DomainError) as caught:
        await _stamp(session, pac)
    return caught.value


def _locks(session: StampSession) -> list[int]:
    return [i for i, sql in enumerate(session.log) if sql.rstrip().endswith('FOR UPDATE')]


def _incidences(session: StampSession) -> list[str]:
    return [entry.comment for entry in session.added if isinstance(entry, Incidence)]


def _xml_writes(session: StampSession) -> list[str]:
    return [sql for sql in session.log if sql.startswith('UPDATE fiscal_document_xml')]


# ── State checks, in /stamp's order ───────────────────────────────────────────


async def test_a_draft_is_not_sealed() -> None:
    pac = ScriptedPac()

    error = await _refused(_session(xml=None), pac)

    assert error.code == ErrorCode.FISCAL_DOCUMENT_NOT_SEALED
    assert pac.calls['stamp'] == 0


@pytest.mark.parametrize(
    'row',
    [
        _row(completed=True, stamp_uuid='U1'),
        _row(completed=True, stamp_uuid='U1', cancelling_since=datetime(2026, 11, 1)),
        _row(completed=True, cancelled=True, stamp_uuid='U1'),
    ],
    ids=['issued', 'cancellation_pending', 'cancelled'],
)
async def test_a_stamped_document_is_returned_unchanged(row: SimpleNamespace) -> None:
    pac = ScriptedPac()
    before = dict(vars(row))
    session = _session(row)

    document = await _stamp(session, pac)

    assert document is row
    assert vars(row) == before
    assert pac.calls['stamp'] == 0
    assert not _xml_writes(session) and not _incidences(session)


async def test_a_stamp_in_process_is_refused_without_calling_the_pac() -> None:
    pac = ScriptedPac()

    error = await _refused(_session(_row(stamping_since=datetime(2026, 11, 17, 9, 0))), pac)

    assert error.code == ErrorCode.FISCAL_DOCUMENT_STAMPING
    assert pac.calls['stamp'] == 0


async def test_an_issue_date_over_72_hours_old_is_refused_without_calling_the_pac() -> None:
    pac = ScriptedPac()
    row = _row(issued=local_now() - timedelta(hours=73))

    error = await _refused(_session(row), pac)

    assert error.code == ErrorCode.FISCAL_DOCUMENT_ISSUE_DATE_OUT_OF_RANGE
    assert pac.calls['stamp'] == 0
    assert row.stamping_since is None


# ── Outcomes ──────────────────────────────────────────────────────────────────


async def test_a_stamp_is_stored_and_the_document_issued() -> None:
    pac = ScriptedPac()
    row = _row()
    session = _session(row)

    document = await _stamp(session, pac)

    stamp = pac.stamped['SELLO-EMISOR']
    assert document is row
    assert (row.stamp_uuid, row.stamped, row.authority_digital_seal) == (
        stamp.uuid,
        stamp.stamped_at,
        stamp.sat_seal,
    )
    assert (row.authority_certificate_number, row.rfc_pac) == (
        stamp.sat_certificate,
        SCRIPTED_PAC_RFC,
    )
    assert (row.completed, row.stamping_since) == (True, None)
    [write] = _xml_writes(session)
    assert stamp.uuid in write
    assert _incidences(session) == [f'CFDI stamped {stamp.uuid}']
    assert session.log[-1] == COMMIT
    assert pac.calls['stamp'] == 1


async def test_rejected_clears_the_marker_and_reports_the_pacs_code() -> None:
    pac = ScriptedPac()
    pac.queue('stamp', PacRejected('CFDI40102', 'El sello no corresponde'))
    row = _row()
    session = _session(row)

    error = await _refused(session, pac)

    assert error.code == ErrorCode.PAC_REJECTED
    assert error.params == {'pac_code': 'CFDI40102', 'pac_message': 'El sello no corresponde'}
    assert row.stamping_since is None
    # Rule 4: the cleared marker is committed, then the error raised.
    assert session.log[-1] == COMMIT
    assert not _incidences(session)


async def test_rejected_cuts_the_pacs_code_and_message_to_their_columns() -> None:
    pac = ScriptedPac()
    pac.queue('stamp', PacRejected('C' * 30, 'M' * 600))

    error = await _refused(_session(), pac)

    assert error.params == {'pac_code': 'C' * 20, 'pac_message': 'M' * 500}


async def test_unavailable_clears_the_marker() -> None:
    pac = ScriptedPac()
    pac.queue('stamp', PacUnavailable())
    row = _row()
    session = _session(row)

    error = await _refused(session, pac)

    assert error.code == ErrorCode.PAC_UNAVAILABLE
    assert row.stamping_since is None
    assert session.log[-1] == COMMIT


@pytest.mark.parametrize('failure', [PacRejected('X', 'Y'), PacUnavailable()])
async def test_a_failure_clears_only_the_marker_this_request_set(failure: Exception) -> None:
    """A refresh reset the document and another stamp set a new marker meanwhile."""
    pac = ScriptedPac()
    pac.queue('stamp', failure)
    newer = datetime(2030, 1, 1, 0, 0)
    later = _row(stamping_since=newer)
    session = StampSession([_row(), later], [_SEALED])

    await _refused(session, pac)

    assert later.stamping_since == newer


async def test_timeout_keeps_the_marker() -> None:
    pac = ScriptedPac()
    pac.queue('stamp', PacTimeout())
    row = _row()
    session = _session(row)

    error = await _refused(session, pac)

    assert error.code == ErrorCode.PAC_TIMEOUT
    assert error.params == {'state': 'stamping'}
    assert row.stamping_since is not None
    assert len(_locks(session)) == 1


async def test_the_marker_is_whole_seconds() -> None:
    """R15 rule 11: DATETIME drops fractions, so the kept value must equal the stored one."""
    pac = ScriptedPac()
    pac.queue('stamp', PacTimeout())
    now = datetime(2026, 11, 17, 10, 0, 0, 654321)
    row = _row(issued=datetime(2026, 11, 17, 9, 0))
    with patch('app.services.fiscal_stamping.local_now', return_value=now):
        await _refused(_session(row), pac)

    assert row.stamping_since == now.replace(microsecond=0)


# ── Locks (R15) ───────────────────────────────────────────────────────────────


async def test_each_transaction_commits_then_locks_with_populate_existing() -> None:
    session = _session()

    await _stamp(session, ScriptedPac())

    locks = _locks(session)
    assert len(locks) == 2
    for lock in locks:
        assert session.log[lock - 1] == COMMIT
    locked = [s for s in session.statements if getattr(s, '_for_update_arg', None) is not None]
    assert len(locked) == 2
    for statement in locked:
        assert statement.get_execution_options().get('populate_existing') is True
    # The PAC is called between the two transactions, after transaction A committed its marker.
    assert session.log[locks[1] - 2] == COMMIT


async def test_the_state_comes_from_the_locked_row() -> None:
    """Rule 3: the row the lock returns is stamping, whatever the route read before it."""
    pac = ScriptedPac()
    session = _session(_row(stamping_since=datetime(2026, 11, 17, 9, 0)))

    error = await _refused(session, pac)

    assert error.code == ErrorCode.FISCAL_DOCUMENT_STAMPING
    assert session.log[0] == COMMIT
    assert pac.calls['stamp'] == 0


async def test_transaction_b_returns_a_document_a_refresh_completed() -> None:
    pac = ScriptedPac()
    completed = _row(completed=True, stamp_uuid='FOUND')
    session = StampSession([_row(), completed], [_SEALED])

    document = await _stamp(session, pac)

    assert document is completed
    assert completed.stamp_uuid == 'FOUND'
    assert not _xml_writes(session) and not _incidences(session)


async def test_transaction_b_keeps_a_changed_document_as_it_is() -> None:
    """Rule 5: reset past the grace and reopened, the document holds another XML."""
    pac = ScriptedPac()
    reset = _row()
    session = StampSession([_row(), reset], [_SEALED, _OTHER])

    error = await _refused(session, pac)

    stamp = pac.stamped['SELLO-EMISOR']
    assert error.code == ErrorCode.PAC_TIMEOUT
    assert error.params == {'state': 'sealed'}
    assert (reset.completed, reset.stamp_uuid) == (False, None)
    assert not _xml_writes(session)
    assert _incidences(session) == [f'CFDI stamp {stamp.uuid} not stored: the document changed']
    assert session.log[-1] == COMMIT


async def test_the_stamped_xml_replaces_the_sealed_one() -> None:
    pac = ScriptedPac()
    session = _session()

    await _stamp(session, pac)

    [write] = [s for s in session.statements if s.is_dml]
    stamped = write.compile().params['data']
    assert etree.fromstring(stamped.encode()).find('.//{*}TimbreFiscalDigital') is not None
