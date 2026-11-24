"""Stamping a sealed CFDI: state checks, the two transactions and their locks (spec 039, R3, R15).

SQLite ignores row locks, so these run against spec 029's fake session, which records every
statement as MariaDB would receive it, plus every commit and rollback. Each lock is answered with
the row the database would hand over at that moment, and each read of the XML with what it holds.
"""

import re
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from lxml import etree

from app.core.clock import local_now
from app.core.error_codes import ErrorCode
from app.core.errors import DomainError
from app.models.incidence import Incidence
from app.services import fiscal_stamping
from app.services.pac.provider import (
    CancellationAnswer,
    PacRejected,
    PacTimeout,
    PacUnavailable,
    SatStatus,
)
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


# ── Cancel (R8, R15) ──────────────────────────────────────────────────────────

_SUBSTITUTE = 'ab12cd34-0000-4000-8000-0000000000ef'
_CERTIFICATE = SimpleNamespace(
    certificate_data=b'DER', key_data=b'PKCS8', key_password=b'csd-password'
)
_INVOICE_ID = 5


def _issued(**overrides: object) -> SimpleNamespace:
    """An issued invoice as the locked read returns it."""
    values = dict(
        completed=True,
        stamp_uuid='11111111-2222-4333-8444-555555555555',
        type=0,
        issuer='EKU9003173C9',
        recipient='XAÑ010101AB1',
        cancellation_date=None,
        cancellation_reason=None,
        cancellation_substitution=None,
    )
    values.update(overrides)
    return _row(**values)


class CancelSession(StampSession):
    """`StampSession` that also answers cancel's plain reads: the reason, the substitute, the live
    related documents, the certificate, a credit note's invoice and orders, and the request's
    status (the next of `statuses` per read)."""

    def __init__(
        self,
        locks: list,
        *,
        xmls: list | None = None,
        reason: bool = True,
        substitute: bool = True,
        live: list[tuple[str, int]] = (),
        certificate: bool = True,
        statuses: list[str] = ('requested',),
        orders: list = (),
    ) -> None:
        super().__init__(locks, xmls or [_SEALED])
        self.reason = reason
        self.substitute = substitute
        self.live = list(live)
        self.certificate = certificate
        self.statuses = list(statuses)
        self.orders = list(orders)

    def _respond(self, sql: str) -> list:
        sql = ' '.join(sql.split())
        if sql.endswith('FOR UPDATE') and 'FROM sales_order ' in sql:
            return self.orders
        if 'FROM sat_reason_cancellation' in sql:
            return [('02',)] if self.reason else []
        if 'upper(fiscal_document.stamp_uuid)' in sql:
            return [(77,)] if self.substitute else []
        if 'JOIN fiscal_document_relation' in sql:
            return self.live
        if 'FROM taxpayer_certificate' in sql:
            return [_CERTIFICATE] if self.certificate else []
        if sql.startswith('SELECT fiscal_document_relation.relation'):
            return [(_INVOICE_ID,)]
        if 'sales_order_detail.sales_order' in sql:
            return [(o.sales_order_id,) for o in self.orders]
        if sql.startswith('SELECT fiscal_document_cancellation.status'):
            return [(self._next(self.statuses),)]
        return super()._respond(sql)


async def _cancel(
    session: StampSession,
    pac: ScriptedPac,
    reason: str = '02',
    substitution: str | None = None,
) -> SimpleNamespace:
    return await fiscal_stamping.cancel(session, 9, reason, substitution, pac, 7)  # type: ignore[arg-type]


async def _cancel_refused(session: StampSession, pac: ScriptedPac, *args: object) -> DomainError:
    with pytest.raises(DomainError) as caught:
        await _cancel(session, pac, *args)
    return caught.value


def _requests(session: StampSession) -> list:
    return [
        entry for entry in session.added if type(entry).__name__ == 'FiscalDocumentCancellation'
    ]


def _request_writes(session: StampSession) -> list[dict]:
    return [
        s.compile().params
        for s in session.statements
        if s.is_dml and s.table.name == 'fiscal_document_cancellation'
    ]


async def test_cancel_begins_locked() -> None:
    """Rule 1: commit the route's transaction, then lock the row with populate_existing."""
    session = CancelSession([_issued()])

    await _cancel(session, ScriptedPac())

    locks = _locks(session)
    assert len(locks) == 2
    assert session.log[0] == COMMIT and locks[0] == 1
    for lock in locks:
        assert session.log[lock - 1] == COMMIT
    locked = [s for s in session.statements if getattr(s, '_for_update_arg', None) is not None]
    for statement in locked:
        assert statement.get_execution_options().get('populate_existing') is True


@pytest.mark.parametrize(
    ('row', 'xml'),
    [
        (_row(), None),
        (_row(), _SEALED),
        (_row(stamping_since=datetime(2026, 11, 17, 9, 0)), _SEALED),
        (_issued(cancelled=True), _SEALED),
    ],
    ids=['draft', 'sealed', 'stamping', 'cancelled'],
)
async def test_cancel_refuses_a_document_that_is_not_issued(
    row: SimpleNamespace, xml: str | None
) -> None:
    pac = ScriptedPac()
    session = CancelSession([row], xmls=[xml])

    error = await _cancel_refused(session, pac)

    assert error.code == ErrorCode.FISCAL_DOCUMENT_NOT_ISSUED
    assert pac.calls['request_cancellation'] == 0
    assert not _requests(session) and not _incidences(session)


async def test_cancel_refuses_an_open_request() -> None:
    """A cancellation_pending document passes the issued check and gets `cancellation_open`."""
    pac = ScriptedPac()
    session = CancelSession([_issued(cancelling_since=datetime(2026, 11, 17, 9, 0))])

    error = await _cancel_refused(session, pac)

    assert error.code == ErrorCode.FISCAL_DOCUMENT_CANCELLATION_OPEN
    assert pac.calls['request_cancellation'] == 0
    assert not _requests(session)


async def test_cancel_refuses_an_unknown_reason() -> None:
    pac = ScriptedPac()
    session = CancelSession([_issued()], reason=False)

    error = await _cancel_refused(session, pac, '09')

    assert error.code == ErrorCode.FISCAL_DOCUMENT_UNKNOWN_CANCELLATION_REASON
    assert error.params == {'reason': '09'}
    assert pac.calls['request_cancellation'] == 0
    assert not _requests(session)


async def test_reason_01_needs_a_substitute() -> None:
    pac = ScriptedPac()
    session = CancelSession([_issued()])

    error = await _cancel_refused(session, pac, '01', None)

    assert error.code == ErrorCode.FISCAL_DOCUMENT_SUBSTITUTION_REQUIRED
    assert pac.calls['request_cancellation'] == 0
    assert not _requests(session)


async def test_reason_01_refuses_an_invalid_substitute() -> None:
    pac = ScriptedPac()
    session = CancelSession([_issued()], substitute=False)

    error = await _cancel_refused(session, pac, '01', _SUBSTITUTE)

    assert error.code == ErrorCode.FISCAL_DOCUMENT_SUBSTITUTION_INVALID
    assert error.params == {'substitution': _SUBSTITUTE}
    assert pac.calls['request_cancellation'] == 0
    assert not _requests(session)


async def test_the_substitute_is_a_live_stamped_document_of_the_same_parties() -> None:
    session = CancelSession([_issued()])

    await _cancel(session, ScriptedPac(), '01', _SUBSTITUTE)

    [query] = [sql for sql in session.log if 'upper(fiscal_document.stamp_uuid)' in sql]
    query = ' '.join(query.split())
    assert f"upper(fiscal_document.stamp_uuid) = '{_SUBSTITUTE.upper()}'" in query
    assert "fiscal_document.issuer = 'EKU9003173C9'" in query
    assert "fiscal_document.recipient = 'XAÑ010101AB1'" in query
    assert 'fiscal_document.completed IS true' in query
    assert 'fiscal_document.cancelled IS false' in query


async def test_reason_01_sends_and_stores_the_substitute_in_upper_case() -> None:
    pac = ScriptedPac()
    session = CancelSession([_issued()])

    await _cancel(session, pac, '01', _SUBSTITUTE)

    [request] = pac.requests
    assert (request.reason, request.substitution) == ('01', _SUBSTITUTE.upper())
    [stored] = _requests(session)
    assert stored.substitution == _SUBSTITUTE.upper()


@pytest.mark.parametrize('reason', ['02', '03', '04'])
async def test_a_substitute_with_another_reason_is_ignored(reason: str) -> None:
    pac = ScriptedPac()
    session = CancelSession([_issued()], substitute=False)

    document = await _cancel(session, pac, reason, _SUBSTITUTE)

    assert not [sql for sql in session.log if 'upper(fiscal_document.stamp_uuid)' in sql]
    assert pac.requests[0].substitution is None
    assert _requests(session)[0].substitution is None
    assert document.cancellation_substitution is None


async def test_an_invoice_with_live_related_documents_is_refused() -> None:
    pac = ScriptedPac()
    session = CancelSession([_issued()], live=[('P', 12), ('NC', 3)])

    error = await _cancel_refused(session, pac)

    assert error.code == ErrorCode.FISCAL_DOCUMENT_HAS_LIVE_DOCUMENTS
    assert error.params == {'documents': ['P12', 'NC3']}
    assert pac.calls['request_cancellation'] == 0
    assert not _requests(session)
    [query] = [' '.join(s.split()) for s in session.log if 'JOIN fiscal_document_relation' in s]
    assert 'fiscal_document_relation.relation = 9' in query
    assert 'fiscal_document.type IN (200, 100)' in query
    assert 'fiscal_document.cancelled IS false' in query  # live()


async def test_cancel_needs_a_certificate_valid_now() -> None:
    pac = ScriptedPac()
    session = CancelSession([_issued()], certificate=False)

    error = await _cancel_refused(session, pac)

    assert error.code == ErrorCode.FISCAL_DOCUMENT_NO_VALID_CERTIFICATE
    assert pac.calls['request_cancellation'] == 0
    assert not _requests(session)


async def test_the_checks_run_in_r8s_order() -> None:
    """Every refusal applies at once: the first in R8's order wins."""
    session = CancelSession(
        [_issued(cancelling_since=datetime(2026, 11, 17, 9, 0))],
        reason=False,
        substitute=False,
        live=[('P', 1)],
        certificate=False,
    )
    assert (await _cancel_refused(session, ScriptedPac())).code == (
        ErrorCode.FISCAL_DOCUMENT_CANCELLATION_OPEN
    )
    session = CancelSession([_issued()], reason=False, live=[('P', 1)], certificate=False)
    assert (await _cancel_refused(session, ScriptedPac(), '01')).code == (
        ErrorCode.FISCAL_DOCUMENT_UNKNOWN_CANCELLATION_REASON
    )
    session = CancelSession([_issued()], substitute=False, live=[('P', 1)], certificate=False)
    assert (await _cancel_refused(session, ScriptedPac(), '01')).code == (
        ErrorCode.FISCAL_DOCUMENT_SUBSTITUTION_REQUIRED
    )
    assert (await _cancel_refused(session, ScriptedPac(), '01', _SUBSTITUTE)).code == (
        ErrorCode.FISCAL_DOCUMENT_SUBSTITUTION_INVALID
    )
    session = CancelSession([_issued()], live=[('P', 1)], certificate=False)
    assert (await _cancel_refused(session, ScriptedPac())).code == (
        ErrorCode.FISCAL_DOCUMENT_HAS_LIVE_DOCUMENTS
    )


async def test_transaction_a_records_the_request_before_calling_the_pac() -> None:
    pac = ScriptedPac()
    pac.queue('request_cancellation', CancellationAnswer.PENDING)
    row = _issued()
    session = CancelSession([row])

    await _cancel(session, pac, '03')

    [request] = _requests(session)
    assert (request.fiscal_document, request.reason, request.status) == (9, '03', 'requested')
    assert (request.requested_by, request.substitution) == (7, None)
    assert request.requested_at == row.cancelling_since
    assert request.requested_at.microsecond == 0
    assert _incidences(session)[0] == 'Cancellation requested (03)'
    # Committed before the PAC call: transaction B is the second lock, after that commit.
    assert session.log[_locks(session)[1] - 1] == COMMIT
    [sent] = pac.requests
    assert (sent.issuer_rfc, sent.recipient_rfc, sent.uuid) == (
        'EKU9003173C9',
        'XAÑ010101AB1',
        row.stamp_uuid,
    )
    assert sent.total == Decimal('116.00')
    assert (sent.csd.certificate, sent.csd.key, sent.csd.password) == (
        b'DER',
        b'PKCS8',
        b'csd-password',
    )


@pytest.mark.parametrize(
    ('answer', 'status', 'state', 'code', 'reason'),
    [
        (CancellationAnswer.ACCEPTED, 'accepted', 'cancelled', None, 'CFDI cancelled'),
        (
            CancellationAnswer.PENDING,
            'pending',
            'cancellation_pending',
            None,
            'Cancellation pending the receiver',
        ),
        (
            CancellationAnswer.NOT_CANCELLABLE,
            'not_cancellable',
            'issued',
            ErrorCode.FISCAL_DOCUMENT_NOT_CANCELLABLE,
            'Cancellation refused: not cancellable',
        ),
        (
            PacRejected('CANC205', 'UUID no encontrado'),
            'rejected',
            'issued',
            ErrorCode.PAC_REJECTED,
            'Cancellation refused by the PAC (CANC205)',
        ),
        (
            PacUnavailable(),
            'failed',
            'issued',
            ErrorCode.PAC_UNAVAILABLE,
            'Cancellation failed: PAC unavailable',
        ),
        (PacTimeout(), None, 'cancellation_pending', ErrorCode.PAC_TIMEOUT, None),
    ],
    ids=['accepted', 'pending', 'not_cancellable', 'rejected', 'unavailable', 'timeout'],
)
async def test_transaction_b_applies_the_answer(
    answer: object, status: str | None, state: str, code: ErrorCode | None, reason: str | None
) -> None:
    pac = ScriptedPac()
    pac.queue('request_cancellation', answer)
    row = _issued()
    session = CancelSession([row])

    if code is None:
        document = await _cancel(session, pac)
        assert document is row
    else:
        error = await _cancel_refused(session, pac)
        assert error.code == code
        if code == ErrorCode.PAC_TIMEOUT:
            assert error.params == {'state': 'cancellation_pending'}
        if code == ErrorCode.PAC_REJECTED:
            assert error.params == {'pac_code': 'CANC205', 'pac_message': 'UUID no encontrado'}

    assert fiscal_stamping.fds.document_status(row, has_xml=True) == state
    writes = _request_writes(session)
    expected = ['Cancellation requested (02)'] + ([reason] if reason else [])
    assert _incidences(session) == expected
    if status is None:
        # A timeout leaves the request `requested` and the document pending; refresh resolves it.
        assert writes == [] and len(_locks(session)) == 1
        return
    [write] = writes
    assert write['status'] == status
    closes = status not in ('pending',)
    assert (row.cancelling_since is None) == closes
    assert ('resolved_at' in write) == closes
    assert ('answered_at' in write) == (status != 'failed')
    if status == 'rejected':
        assert (write['pac_code'], write['pac_message']) == ('CANC205', 'UUID no encontrado')
    if status == 'accepted':
        assert row.cancelled is True
        assert (row.cancellation_reason, row.cancellation_substitution) == ('02', None)
        assert row.cancellation_date is not None and row.cancellation_date.microsecond == 0
    else:
        assert row.cancelled is False
    # Rule 4: the answer is committed before any error is raised.
    assert session.log[-1] == COMMIT


async def test_a_rejection_is_cut_to_the_columns() -> None:
    pac = ScriptedPac()
    pac.queue('request_cancellation', PacRejected('C' * 30, 'M' * 600))
    session = CancelSession([_issued()])

    error = await _cancel_refused(session, pac)

    assert error.params == {'pac_code': 'C' * 20, 'pac_message': 'M' * 500}
    [write] = _request_writes(session)
    assert (write['pac_code'], write['pac_message']) == ('C' * 20, 'M' * 500)
    assert _incidences(session)[-1] == f'Cancellation refused by the PAC ({"C" * 20})'


async def test_transaction_b_reads_the_request_status_from_the_row() -> None:
    """Rules 7 and 11: a refresh already resolved the request, though the object transaction A
    inserted still says `requested`; B leaves it and the document alone."""
    pac = ScriptedPac()
    pac.queue('request_cancellation', CancellationAnswer.ACCEPTED)
    row = _issued()
    session = CancelSession([row], statuses=['accepted'])

    document = await _cancel(session, pac)

    assert document is row
    assert _requests(session)[0].status == 'requested'
    assert _request_writes(session) == []
    assert row.cancelled is False
    assert _incidences(session) == ['Cancellation requested (02)']
    status_reads = [
        i for i, sql in enumerate(session.log) if 'fiscal_document_cancellation.status' in sql
    ]
    assert status_reads and status_reads[0] > _locks(session)[1]


# ── Cancelling a credit note: the paid flags (spec 033 FR-011, R15 rule 10) ───


def _orders() -> list[SimpleNamespace]:
    return [SimpleNamespace(sales_order_id=31, paid=True), SimpleNamespace(sales_order_id=32)]


def _balances(*values: str) -> dict[int, SimpleNamespace]:
    return {
        oid: SimpleNamespace(balance=Decimal(value))
        for oid, value in zip((31, 32), values, strict=True)
    }


async def test_cancelling_a_credit_note_reopens_the_orders_it_settled() -> None:
    note = _issued(type=100)
    invoice = _issued(fiscal_document_id=_INVOICE_ID)
    orders = _orders()
    session = CancelSession([note, note, invoice], orders=orders)
    seen: list[bool] = []

    async def balances(_db: object, given: list) -> dict:
        seen.append(note.cancelled)
        assert given == orders
        return _balances('0', '5') if len(seen) == 1 else _balances('100', '105')

    with (
        patch.object(fiscal_stamping.sales_order_service, 'order_balances', side_effect=balances),
        patch.object(fiscal_stamping.customer_payment_service, 'reopen') as reopen,
    ):
        await _cancel(session, ScriptedPac())

    # `before` while the note is still live, `after` once it is cancelled and flushed.
    assert seen == [False, True]
    assert session.flushes >= 1
    assert [c.args for c in reopen.call_args_list] == [
        (orders[0], Decimal('0'), Decimal('100')),
        (orders[1], Decimal('5'), Decimal('105')),
    ]


async def test_the_credit_note_locks_come_before_any_plain_read() -> None:
    """Rule 10: commit, then note → invoice → orders by primary key, then the status read."""
    note = _issued(type=100)
    session = CancelSession([note, note, _issued(fiscal_document_id=_INVOICE_ID)], orders=_orders())

    with (
        patch.object(
            fiscal_stamping.sales_order_service, 'order_balances', return_value=_balances('0', '0')
        ),
        patch.object(fiscal_stamping.customer_payment_service, 'reopen'),
    ):
        await _cancel(session, ScriptedPac())

    log = [' '.join(sql.split()) for sql in session.log]
    # Transaction A read the invoice and its orders before the PAC call.
    relation = next(i for i, sql in enumerate(log) if 'fiscal_document_relation.relation' in sql)
    order_ids = next(i for i, sql in enumerate(log) if 'sales_order_detail.sales_order' in sql)
    locks = _locks(session)
    assert relation < locks[1] and order_ids < locks[1]
    b = locks[1]
    assert log[b - 1] == COMMIT
    assert log[b].endswith('WHERE fiscal_document.fiscal_document_id = 9 FOR UPDATE')
    assert log[b + 1].endswith(
        f'WHERE fiscal_document.fiscal_document_id = {_INVOICE_ID} FOR UPDATE'
    )
    assert log[b + 2].endswith(
        'WHERE sales_order.sales_order_id IN (31, 32) '
        'ORDER BY sales_order.sales_order_id FOR UPDATE'
    )
    assert log[b + 3].startswith('SELECT fiscal_document_cancellation.status')
    locked = [s for s in session.statements if getattr(s, '_for_update_arg', None) is not None]
    assert len(locked) == 4
    for statement in locked:
        assert statement.get_execution_options().get('populate_existing') is True


@pytest.mark.parametrize('type_', [0, 200])
async def test_cancelling_an_invoice_or_a_receipt_leaves_the_paid_flags(type_: int) -> None:
    session = CancelSession([_issued(type=type_)])

    with (
        patch.object(fiscal_stamping.sales_order_service, 'order_balances') as balances,
        patch.object(fiscal_stamping.customer_payment_service, 'reopen') as reopen,
    ):
        await _cancel(session, ScriptedPac())

    balances.assert_not_called()
    reopen.assert_not_called()
    assert not [sql for sql in session.log if 'FROM sales_order ' in sql]


# ── Refresh (R8 "Refresh", R15 rules 1, 6, 7, 10, 11) ─────────────────────────

_NOW = datetime(2026, 11, 17, 12, 0, 0)
_REQUEST_ID = 40


def _pending(**overrides: object) -> SimpleNamespace:
    """An issued document with an open cancellation request."""
    return _issued(cancelling_since=_NOW - timedelta(hours=1), **overrides)


class RefreshSession(CancelSession):
    """`CancelSession` that also answers refresh's read of the open request (`request`, a row of
    id, status, requested_at, reason and substitution), the status and `sat_pending_at` read by id
    (`by_id`, `sat_pending_at`) and the look for an earlier request SAT rejected
    (`rejected_before`); `rejected_by_pac` is an earlier one the PAC refused (with `pac_code`)."""

    def __init__(
        self,
        locks: list,
        *,
        request: tuple | None = None,
        by_id: dict[int, list[str]] | None = None,
        sat_pending_at: datetime | None = None,
        rejected_before: bool = False,
        rejected_by_pac: bool = False,
        **kwargs: object,
    ) -> None:
        super().__init__(locks, **kwargs)
        self.request = request
        self.by_id = {key: list(value) for key, value in (by_id or {}).items()}
        self.sat_pending_at = sat_pending_at
        self.rejected_before = rejected_before
        self.rejected_by_pac = rejected_by_pac

    def _respond(self, sql: str) -> list:
        flat = ' '.join(sql.split())
        if flat.startswith('SELECT fiscal_document_cancellation.fiscal_document_cancellation_id'):
            if 'fiscal_document_cancellation_id <' in flat:
                by_pac = self.rejected_by_pac and 'pac_code IS NULL' not in flat
                return [(39,)] if self.rejected_before or by_pac else []
            return [self.request] if self.request is not None else []
        if flat.startswith('SELECT fiscal_document_cancellation.status'):
            request_id = int(re.search(r'fiscal_document_cancellation_id = (\d+)', flat).group(1))
            return [(self._next(self.by_id[request_id]), self.sat_pending_at)]
        return super()._respond(sql)


def _open(
    status: str = 'pending',
    age: timedelta = timedelta(hours=1),
    reason: str = '02',
    answered_age: timedelta | None = None,
):  # noqa: ANN202
    """The open request as refresh reads it: id, status, requested_at, reason, substitution and
    answered_at. A `pending` request was answered when it was made, unless `answered_age` says."""
    answered = None if status == 'requested' else _NOW - (answered_age or age)
    return (_REQUEST_ID, status, _NOW - age, reason, None, answered)


def _sat(state: str, cancellation: str) -> SatStatus:
    return SatStatus(state=state, cancellable='with_acceptance', cancellation=cancellation)


async def _refresh(session: StampSession, pac: ScriptedPac) -> SimpleNamespace:
    with patch('app.services.fiscal_stamping.local_now', return_value=_NOW):
        return await fiscal_stamping.refresh(session, 9, pac, 7)  # type: ignore[arg-type]


async def _refresh_refused(session: StampSession, pac: ScriptedPac) -> DomainError:
    with pytest.raises(DomainError) as caught:
        await _refresh(session, pac)
    return caught.value


def _status_session(row: SimpleNamespace, status: str = 'pending', **kwargs: object):  # noqa: ANN202
    request = kwargs.pop('request', _open(status))
    by_id = kwargs.pop('by_id', {_REQUEST_ID: [status]})
    return RefreshSession([row], request=request, by_id=by_id, **kwargs)


def _stamping(age: timedelta) -> SimpleNamespace:
    return _row(stamping_since=_NOW - age)


async def test_refresh_runs_two_transactions_around_the_pac_call() -> None:
    """Rule 1: transaction 1 (lock, read, commit), the PAC call with none open, transaction 2."""
    pac = ScriptedPac()
    session = RefreshSession([_stamping(timedelta(minutes=1))])
    seen: list[list[str]] = []
    original = pac.find_stamp

    async def find_stamp(sealed_xml: str):  # noqa: ANN202
        seen.append(list(session.log))
        return await original(sealed_xml)

    pac.find_stamp = find_stamp  # type: ignore[method-assign]

    await _refresh(session, pac)

    locks = _locks(session)
    assert len(locks) == 2
    assert session.log[0] == COMMIT and locks[0] == 1
    [log] = seen
    assert log[-1] == COMMIT and len(log) <= locks[1]
    assert len([i for i in _locks(session) if i < len(log)]) == 1
    assert session.log[locks[1] - 1] == COMMIT
    locked = [s for s in session.statements if getattr(s, '_for_update_arg', None) is not None]
    for statement in locked:
        assert statement.get_execution_options().get('populate_existing') is True
    assert session.log[-1] == COMMIT


async def test_refresh_of_a_credit_note_locks_note_invoice_orders_before_any_read() -> None:
    """Rule 10: transaction 2 locks note → invoice → orders, and only then reads the status."""
    note = _pending(type=100)
    session = RefreshSession(
        [note, note, _issued(fiscal_document_id=_INVOICE_ID)],
        request=_open('pending'),
        by_id={_REQUEST_ID: ['pending']},
        orders=_orders(),
    )
    pac = ScriptedPac()
    pac.queue('query_status', _sat('cancelled', 'accepted'))

    with (
        patch.object(
            fiscal_stamping.sales_order_service, 'order_balances', return_value=_balances('0', '0')
        ),
        patch.object(fiscal_stamping.customer_payment_service, 'reopen') as reopen,
    ):
        await _refresh(session, pac)

    log = [' '.join(sql.split()) for sql in session.log]
    locks = _locks(session)
    relation = next(i for i, sql in enumerate(log) if 'fiscal_document_relation.relation' in sql)
    order_ids = next(i for i, sql in enumerate(log) if 'sales_order_detail.sales_order' in sql)
    assert relation < locks[1] and order_ids < locks[1]
    b = locks[1]
    assert log[b - 1] == COMMIT
    assert log[b].endswith('WHERE fiscal_document.fiscal_document_id = 9 FOR UPDATE')
    assert log[b + 1].endswith(
        f'WHERE fiscal_document.fiscal_document_id = {_INVOICE_ID} FOR UPDATE'
    )
    assert log[b + 2].endswith(
        'WHERE sales_order.sales_order_id IN (31, 32) '
        'ORDER BY sales_order.sales_order_id FOR UPDATE'
    )
    assert log[b + 3].startswith('SELECT fiscal_document_cancellation.status')
    assert note.cancelled is True
    assert reopen.call_count == 2


async def test_a_stamp_found_is_stored() -> None:
    pac = ScriptedPac()
    found = pac._make_stamp(_SEALED)
    pac.queue('find_stamp', found)
    row = _stamping(timedelta(minutes=1))
    session = RefreshSession([row])

    document = await _refresh(session, pac)

    assert document is row
    assert (row.completed, row.stamp_uuid, row.stamping_since) == (True, found.uuid, None)
    [write] = _xml_writes(session)
    assert found.uuid in write
    assert _incidences(session) == [f'CFDI stamp found {found.uuid}']
    assert pac.calls['find_stamp'] == 1 and pac.calls['stamp'] == 0


@pytest.mark.parametrize(
    ('age', 'sealed'),
    [(timedelta(minutes=4, seconds=59), False), (timedelta(minutes=5, seconds=1), True)],
    ids=['4:59', '5:01'],
)
async def test_a_stamp_not_found_seals_again_only_past_the_grace(
    age: timedelta, sealed: bool
) -> None:
    pac = ScriptedPac()
    row = _stamping(age)
    session = RefreshSession([row])

    document = await _refresh(session, pac)

    assert document is row
    state = fiscal_stamping.fds.document_status(row, has_xml=True)
    assert state == ('sealed' if sealed else 'stamping')
    expected = ['CFDI stamp not found, sealed again'] if sealed else []
    assert _incidences(session) == expected
    assert not _xml_writes(session)
    assert session.log[-1] == COMMIT


async def test_a_stamping_marker_that_changed_is_left_alone() -> None:
    """Rule 6: another stamp set its own marker after `find_stamp` was called."""
    pac = ScriptedPac()
    newer = _NOW - timedelta(seconds=10)
    later = _row(stamping_since=newer)
    session = RefreshSession([_stamping(timedelta(minutes=6)), later])

    await _refresh(session, pac)

    assert later.stamping_since == newer
    assert not _incidences(session)


# ── Resolving by resubmission (spec 040, R4, FR-008 to FR-010), T011 ──────────


def _resubmitting() -> ScriptedPac:
    pac = ScriptedPac()
    pac.resolves_by_resubmitting = True
    return pac


async def test_within_the_grace_a_resubmitting_pac_is_not_called() -> None:
    pac = _resubmitting()
    row = _stamping(timedelta(minutes=4, seconds=59))
    session = RefreshSession([row])

    document = await _refresh(session, pac)

    assert document is row
    assert fiscal_stamping.fds.document_status(row, has_xml=True) == 'stamping'
    assert pac.calls['stamp'] == pac.calls['find_stamp'] == 0
    assert not _incidences(session) and not _xml_writes(session)


async def test_past_the_grace_the_stored_xml_is_resubmitted_and_its_stamp_stored() -> None:
    pac = _resubmitting()
    row = _stamping(timedelta(minutes=5, seconds=1))
    session = RefreshSession([row])

    document = await _refresh(session, pac)

    stamp = pac.stamped['SELLO-EMISOR']
    assert document is row
    assert (row.completed, row.stamp_uuid, row.stamping_since) == (True, stamp.uuid, None)
    assert pac.calls['stamp'] == 1 and pac.calls['find_stamp'] == 0
    assert _incidences(session) == [f'CFDI stamp resolved {stamp.uuid}']
    [write] = _xml_writes(session)
    assert stamp.uuid in write


async def test_the_resubmission_is_the_stored_sealed_xml_byte_for_byte() -> None:
    pac = _resubmitting()
    sent: list[str] = []
    original = pac.stamp

    async def recording(sealed_xml: str):  # noqa: ANN202
        sent.append(sealed_xml)
        return await original(sealed_xml)

    pac.stamp = recording  # type: ignore[method-assign]
    session = RefreshSession([_stamping(timedelta(minutes=6))])

    await _refresh(session, pac)

    assert sent == [_SEALED]


async def test_a_rejected_resubmission_seals_the_document_again() -> None:
    pac = _resubmitting()
    pac.queue('stamp', PacRejected('CFDI40102', 'El sello no corresponde'))
    row = _stamping(timedelta(minutes=6))
    session = RefreshSession([row])

    error = await _refresh_refused(session, pac)

    assert error.code == ErrorCode.PAC_REJECTED
    assert error.params == {'pac_code': 'CFDI40102', 'pac_message': 'El sello no corresponde'}
    assert row.stamping_since is None
    assert session.log[-1] == COMMIT


async def test_a_rejected_resubmission_leaves_a_newer_marker_alone() -> None:
    pac = _resubmitting()
    pac.queue('stamp', PacRejected('X', 'Y'))
    newer = _NOW - timedelta(seconds=10)
    later = _row(stamping_since=newer)
    session = RefreshSession([_stamping(timedelta(minutes=6)), later])

    await _refresh_refused(session, pac)

    assert later.stamping_since == newer


@pytest.mark.parametrize(
    ('failure', 'code'),
    [(PacUnavailable(), ErrorCode.PAC_UNAVAILABLE), (PacTimeout(), ErrorCode.PAC_TIMEOUT)],
)
async def test_an_unanswered_resubmission_keeps_the_document_stamping(
    failure: Exception, code: ErrorCode
) -> None:
    pac = _resubmitting()
    pac.queue('stamp', failure)
    row = _stamping(timedelta(minutes=6))
    marker = row.stamping_since
    session = RefreshSession([row])

    error = await _refresh_refused(session, pac)

    assert error.code == code
    assert row.stamping_since == marker
    assert not _incidences(session)


async def test_the_job_resolving_a_stamp_leaves_the_audit_columns_alone() -> None:
    pac = _resubmitting()
    row = _stamping(timedelta(minutes=6))
    row.updater, row.modification_time = 3, datetime(2020, 1, 1)
    before = (row.updater, row.modification_time)
    session = RefreshSession([row])

    with patch('app.services.fiscal_stamping.local_now', return_value=_NOW):
        await fiscal_stamping.refresh(session, 9, pac, 7, touch=False)  # type: ignore[arg-type]

    assert row.completed is True
    assert (row.updater, row.modification_time) == before


@pytest.mark.parametrize(
    ('status', 'sat', 'closed_as', 'state', 'reason'),
    [
        ('pending', _sat('cancelled', 'accepted'), 'accepted', 'cancelled', 'CFDI cancelled'),
        ('pending', _sat('cancelled', 'expired'), 'expired', 'cancelled', 'CFDI cancelled'),
        ('requested', _sat('cancelled', 'accepted'), 'accepted', 'cancelled', 'CFDI cancelled'),
        ('pending', _sat('valid', 'rejected'), 'rejected', 'issued', 'Cancellation rejected'),
    ],
    ids=['accepted', 'expired', 'requested_accepted', 'rejected'],
)
async def test_a_result_that_closes_the_request(
    status: str, sat: SatStatus, closed_as: str, state: str, reason: str
) -> None:
    pac = ScriptedPac()
    pac.queue('query_status', sat)
    row = _pending()
    session = _status_session(row, status)

    document = await _refresh(session, pac)

    assert document is row
    assert fiscal_stamping.fds.document_status(row, has_xml=True) == state
    assert row.cancelling_since is None
    [write] = _request_writes(session)
    assert write['status'] == closed_as
    assert write['resolved_at'] == _NOW and write['last_checked_at'] == _NOW
    assert _incidences(session) == [reason]
    if state == 'cancelled':
        assert (row.cancelled, row.cancellation_reason, row.cancellation_date) == (True, '02', _NOW)
    assert session.log[-1] == COMMIT
    assert pac.calls['query_status'] == 1


async def test_the_query_names_the_document() -> None:
    pac = ScriptedPac()
    calls: list[tuple] = []

    async def query_status(*args: object) -> SatStatus:
        calls.append(args)
        return _sat('valid', 'pending')

    pac.query_status = query_status  # type: ignore[method-assign]
    row = _pending()

    await _refresh(_status_session(row), pac)

    assert calls == [('EKU9003173C9', 'XAÑ010101AB1', row.stamp_uuid, Decimal('116.00'))]


async def test_not_received_within_the_grace_of_a_requested_request_is_unchanged() -> None:
    """Rule 7: the cancel that sent it may still be waiting on the PAC."""
    pac = ScriptedPac()
    pac.queue('query_status', _sat('valid', 'none'))
    row = _pending()
    request = _open('requested', age=timedelta(minutes=4, seconds=59))
    session = _status_session(row, 'requested', request=request)

    await _refresh(session, pac)

    assert row.cancelling_since is not None
    [write] = _request_writes(session)
    assert write == {'last_checked_at': _NOW, 'fiscal_document_cancellation_id_1': _REQUEST_ID}
    assert not _incidences(session)


async def test_not_received_past_the_grace_of_a_requested_request_fails_it() -> None:
    pac = ScriptedPac()
    pac.queue('query_status', _sat('valid', 'none'))
    row = _pending()
    request = _open('requested', age=timedelta(minutes=5, seconds=1))

    await _refresh(session := _status_session(row, 'requested', request=request), pac)

    assert row.cancelling_since is None
    assert _request_writes(session)[0]['status'] == 'failed'
    assert _incidences(session) == ['Cancellation not received by SAT']


@pytest.mark.parametrize(
    'answered_age', [timedelta(hours=1), timedelta(hours=71, minutes=59, seconds=59)]
)
async def test_not_received_within_72_hours_of_a_pending_answer_is_unchanged(
    answered_age: timedelta,
) -> None:
    """Spec 040, FR-014: after the PAC's 201, SAT may take a while to show the cancellation."""
    pac = ScriptedPac()
    pac.queue('query_status', _sat('valid', 'none'))
    row = _pending()
    request = _open('pending', age=answered_age)
    session = _status_session(row, 'pending', request=request)

    await _refresh(session, pac)

    assert row.cancelling_since is not None
    [write] = _request_writes(session)
    assert write == {'last_checked_at': _NOW, 'fiscal_document_cancellation_id_1': _REQUEST_ID}
    assert not _incidences(session)


@pytest.mark.parametrize('answered_age', [timedelta(hours=72), timedelta(days=5)])
async def test_not_received_72_hours_after_a_pending_answer_fails_it(
    answered_age: timedelta,
) -> None:
    pac = ScriptedPac()
    pac.queue('query_status', _sat('valid', 'none'))
    row = _pending()
    request = _open('pending', age=answered_age)
    session = _status_session(row, 'pending', request=request)

    await _refresh(session, pac)

    assert fiscal_stamping.fds.document_status(row, has_xml=True) == 'issued'
    assert row.cancelling_since is None
    [write] = _request_writes(session)
    assert (write['status'], write['resolved_at']) == ('failed', _NOW)
    assert _incidences(session) == ['Cancellation not received by SAT']


async def test_a_timed_out_request_sat_holds_becomes_pending() -> None:
    pac = ScriptedPac()
    pac.queue('query_status', _sat('valid', 'pending'))
    row = _pending()
    session = _status_session(row, 'requested')

    await _refresh(session, pac)

    assert fiscal_stamping.fds.document_status(row, has_xml=True) == 'cancellation_pending'
    [write] = _request_writes(session)
    assert (write['status'], write['answered_at'], write['last_checked_at']) == (
        'pending',
        _NOW,
        _NOW,
    )
    assert 'resolved_at' not in write
    assert _incidences(session) == ['Cancellation pending the receiver']


@pytest.mark.parametrize(
    ('status', 'sat'),
    [
        ('pending', _sat('not_found', 'none')),
        ('pending', _sat('valid', 'pending')),
        ('pending', _sat('valid', 'requested')),
        ('requested', _sat('valid', 'requested')),
    ],
    ids=['not_found', 'still_pending', 'pending_requested', 'requested_requested'],
)
async def test_anything_else_only_records_the_check(status: str, sat: SatStatus) -> None:
    pac = ScriptedPac()
    pac.queue('query_status', sat)
    row = _pending()
    before = dict(vars(row))
    session = _status_session(row, status, sat_pending_at=_NOW - timedelta(hours=1))

    document = await _refresh(session, pac)

    assert document is row
    assert vars(row) == before
    [write] = _request_writes(session)
    assert write == {'last_checked_at': _NOW, 'fiscal_document_cancellation_id_1': _REQUEST_ID}
    assert not _incidences(session)


@pytest.mark.parametrize('kept', ['requested', 'pending'])
async def test_sat_pending_is_recorded_the_first_time_it_is_seen(kept: str) -> None:
    """#349: when SAT first shows the request in process, whoever answered it."""
    pac = ScriptedPac()
    pac.queue('query_status', _sat('valid', 'pending'))
    row = _pending()
    session = _status_session(row, kept)

    await _refresh(session, pac)

    [write] = _request_writes(session)
    assert write['sat_pending_at'] == _NOW
    assert row.cancelling_since is not None


async def test_sat_pending_seen_before_is_not_overwritten() -> None:
    pac = ScriptedPac()
    pac.queue('query_status', _sat('valid', 'pending'))
    row = _pending()
    session = _status_session(row, 'pending', sat_pending_at=_NOW - timedelta(minutes=10))

    await _refresh(session, pac)

    [write] = _request_writes(session)
    assert 'sat_pending_at' not in write


@pytest.mark.parametrize('rejected_before', [False, True])
async def test_a_rejection_within_the_grace_of_a_requested_request_is_unchanged(
    rejected_before: bool,
) -> None:
    """#349: the PAC hasn't answered, so SAT's rejection may be an earlier request's; it is no
    news, as valid/none is (rule 7)."""
    pac = ScriptedPac()
    pac.queue('query_status', _sat('valid', 'rejected'))
    row = _pending()
    request = _open('requested', age=timedelta(seconds=1))
    session = _status_session(row, 'requested', request=request, rejected_before=rejected_before)

    await _refresh(session, pac)

    assert fiscal_stamping.fds.document_status(row, has_xml=True) == 'cancellation_pending'
    [write] = _request_writes(session)
    assert write == {'last_checked_at': _NOW, 'fiscal_document_cancellation_id_1': _REQUEST_ID}
    assert not _incidences(session)


async def test_a_rejection_past_the_grace_of_a_requested_request_fails_it() -> None:
    pac = ScriptedPac()
    pac.queue('query_status', _sat('valid', 'rejected'))
    row = _pending()
    request = _open('requested', age=timedelta(minutes=5, seconds=1))
    session = _status_session(row, 'requested', request=request)

    await _refresh(session, pac)

    assert fiscal_stamping.fds.document_status(row, has_xml=True) == 'issued'
    [write] = _request_writes(session)
    assert (write['status'], write['resolved_at']) == ('failed', _NOW)
    assert _incidences(session) == ['Cancellation not received by SAT']


@pytest.mark.parametrize(
    'answered_age', [timedelta(seconds=1), timedelta(hours=71, minutes=59, seconds=59)]
)
async def test_an_earlier_requests_rejection_leaves_a_pending_request_open(
    answered_age: timedelta,
) -> None:
    """#349: an earlier request was rejected and SAT hasn't shown this one in process yet."""
    pac = ScriptedPac()
    pac.queue('query_status', _sat('valid', 'rejected'))
    row = _pending()
    request = _open('pending', age=answered_age)
    session = _status_session(row, 'pending', request=request, rejected_before=True)

    await _refresh(session, pac)

    assert fiscal_stamping.fds.document_status(row, has_xml=True) == 'cancellation_pending'
    [write] = _request_writes(session)
    assert write == {'last_checked_at': _NOW, 'fiscal_document_cancellation_id_1': _REQUEST_ID}
    assert not _incidences(session)
    [earlier] = [sql for sql in session.log if 'fiscal_document_cancellation_id <' in sql]
    assert "status = 'rejected'" in earlier and 'fiscal_document = 9' in earlier


async def test_an_earlier_requests_rejection_fails_a_pending_request_after_72_hours() -> None:
    """The rejection never became this request's: FR-014's 72 hours apply, as to valid/none."""
    pac = ScriptedPac()
    pac.queue('query_status', _sat('valid', 'rejected'))
    row = _pending()
    request = _open('pending', age=timedelta(hours=72))
    session = _status_session(row, 'pending', request=request, rejected_before=True)

    await _refresh(session, pac)

    assert fiscal_stamping.fds.document_status(row, has_xml=True) == 'issued'
    [write] = _request_writes(session)
    assert (write['status'], write['resolved_at']) == ('failed', _NOW)
    assert _incidences(session) == ['Cancellation not received by SAT']


async def test_an_earlier_refusal_by_the_pac_does_not_hold_a_rejection() -> None:
    """A request the PAC refused (`pac_code`) never reached SAT: SAT's rejection is this one's."""
    pac = ScriptedPac()
    pac.queue('query_status', _sat('valid', 'rejected'))
    row = _pending()
    session = _status_session(row, 'pending', rejected_by_pac=True)

    await _refresh(session, pac)

    assert fiscal_stamping.fds.document_status(row, has_xml=True) == 'issued'
    [write] = _request_writes(session)
    assert (write['status'], write['resolved_at']) == ('rejected', _NOW)
    assert _incidences(session) == ['Cancellation rejected']


async def test_a_rejection_after_sat_showed_the_request_in_process_closes_it() -> None:
    """Once SAT has shown this request pending, its rejection is this request's."""
    pac = ScriptedPac()
    pac.queue('query_status', _sat('valid', 'rejected'))
    row = _pending()
    session = _status_session(
        row, 'pending', sat_pending_at=_NOW - timedelta(minutes=10), rejected_before=True
    )

    await _refresh(session, pac)

    assert fiscal_stamping.fds.document_status(row, has_xml=True) == 'issued'
    [write] = _request_writes(session)
    assert (write['status'], write['resolved_at']) == ('rejected', _NOW)
    assert _incidences(session) == ['Cancellation rejected']
    assert not any('fiscal_document_cancellation_id <' in sql for sql in session.log)


async def test_a_request_whose_status_changed_is_left_alone() -> None:
    """Rules 6 and 11: the kept request was resolved after `query_status` (the status read by id
    says `rejected`); refresh changes nothing but its `last_checked_at`."""
    pac = ScriptedPac()
    pac.queue('query_status', _sat('cancelled', 'accepted'))
    row = _pending()
    session = _status_session(row, 'pending', by_id={_REQUEST_ID: ['rejected']})

    await _refresh(session, pac)

    assert row.cancelled is False
    [write] = _request_writes(session)
    assert write == {'last_checked_at': _NOW, 'fiscal_document_cancellation_id_1': _REQUEST_ID}
    assert not _incidences(session)
    reads = [
        i
        for i, sql in enumerate(session.log)
        if sql.startswith('SELECT fiscal_document_cancellation.status')
    ]
    assert reads and reads[0] > _locks(session)[1]


async def test_another_open_request_with_the_same_status_is_left_alone() -> None:
    """Rule 6, by id: the kept request (40) closed and a new cancel opened 41, also `pending`."""
    pac = ScriptedPac()
    pac.queue('query_status', _sat('valid', 'rejected'))
    row = _pending()
    session = _status_session(row, 'pending', by_id={_REQUEST_ID: ['rejected'], 41: ['pending']})

    await _refresh(session, pac)

    assert row.cancelling_since is not None
    [write] = _request_writes(session)
    assert write == {'last_checked_at': _NOW, 'fiscal_document_cancellation_id_1': _REQUEST_ID}
    assert not _incidences(session)


@pytest.mark.parametrize(
    ('row', 'xml'),
    [
        (_row(), None),
        (_row(), _SEALED),
        (_issued(), _SEALED),
        (_issued(cancelled=True), _SEALED),
    ],
    ids=['draft', 'sealed', 'issued', 'cancelled'],
)
async def test_nothing_to_refresh(row: SimpleNamespace, xml: str | None) -> None:
    pac = ScriptedPac()
    session = RefreshSession([row], xmls=[xml])

    error = await _refresh_refused(session, pac)

    assert error.code == ErrorCode.FISCAL_DOCUMENT_NOTHING_TO_REFRESH
    assert sum(pac.calls.values()) == 0
    assert len(_locks(session)) == 1


@pytest.mark.parametrize(
    ('failure', 'code'),
    [(PacUnavailable(), ErrorCode.PAC_UNAVAILABLE), (PacTimeout(), ErrorCode.PAC_TIMEOUT)],
    ids=['unavailable', 'timeout'],
)
@pytest.mark.parametrize('state', ['stamping', 'cancellation_pending'])
async def test_a_pac_failure_leaves_the_document_and_raises(
    failure: Exception, code: ErrorCode, state: str
) -> None:
    pac = ScriptedPac()
    if state == 'stamping':
        row = _stamping(timedelta(minutes=6))
        session = RefreshSession([row])
        pac.queue('find_stamp', failure)
    else:
        row = _pending()
        session = _status_session(row)
        pac.queue('query_status', failure)
    before = dict(vars(row))

    error = await _refresh_refused(session, pac)

    assert error.code == code
    if code == ErrorCode.PAC_TIMEOUT:
        assert error.params == {'state': state}
    assert vars(row) == before
    assert len(_locks(session)) == 1
    assert not _request_writes(session) and not _incidences(session)
