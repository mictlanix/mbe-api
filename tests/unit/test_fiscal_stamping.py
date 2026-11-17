"""Stamping a sealed CFDI: state checks, the two transactions and their locks (spec 039, R3, R15).

SQLite ignores row locks, so these run against spec 029's fake session, which records every
statement as MariaDB would receive it, plus every commit and rollback. Each lock is answered with
the row the database would hand over at that moment, and each read of the XML with what it holds.
"""

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
from app.services.pac.provider import CancellationAnswer, PacRejected, PacTimeout, PacUnavailable
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
