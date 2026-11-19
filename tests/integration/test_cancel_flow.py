"""Cancellation on real tables, with the simulator and with a second provider (spec 039, US2).

`receipts` and `notes` each seed the same certificate and issuer, so each test uses one of them. A
pending cancellation becomes cancelled only through a refresh, so each "once cancelled" check uses
a second, equivalent document whose cancellation is accepted at once.
"""

import io
import logging
from collections.abc import Iterator
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from mictlanix.cfdi import pac as lib
from mictlanix.cfdi import sat as libsat
from pypdf import PdfReader
from sqlalchemy import inspect, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.deps import CurrentUser
from app.enums import EntityStatus
from app.models.fiscal import FiscalDocument, FiscalDocumentCancellation, TaxpayerCertificate
from app.models.incidence import Incidence
from app.models.product import Product
from app.models.sales import SalesOrderDetail
from app.services import customer_refund_service, fiscal_stamping
from app.services import fiscal_document_service as fds
from app.services.fiscal_email import email_document
from app.services.pac.provider import (
    CancellationAnswer,
    PacProvider,
    PacRejected,
    PacTimeout,
    PacUnavailable,
    SatStatus,
)
from app.services.pac.simulated import SimulatedPac
from tests.fakes.pac import ScriptedPac, scripted_multifacturas
from tests.integration.seed import (
    ISSUER_RFC,
    seed_cancellation_reasons,
    seed_credit_note,
    seed_fiscal_document,
)
from tests.integration.test_credit_note_flow import (  # noqa: F401
    CreditNotes,
    _invoice,
    _invoiced_orders,
    _lines,
    _paid,
    notes,
)
from tests.integration.test_fiscal_email import RecordingMailer
from tests.integration.test_receipt_flow import (  # noqa: F401
    ISSUER,
    Receipts,
    _payment_applied_to,
    _ppd_invoice,
    _relation,
    receipts,
)
from tests.unit.test_csd_service import _CERTIFICATE_NUMBER, _PASSWORD, _make_csd

pytestmark = pytest.mark.usefixtures('seeded')

_SCRIPTED = {
    'accepted': CancellationAnswer.ACCEPTED,
    'pending': CancellationAnswer.PENDING,
    'not_cancellable': CancellationAnswer.NOT_CANCELLABLE,
    'unavailable': PacUnavailable(),
    'timeout': PacTimeout(),
}
_PAC_CODE, _PAC_MESSAGE = 'CANC205', 'UUID no encontrado'


@pytest.fixture(params=['simulated', 'scripted'])
def pac(request: pytest.FixtureRequest, sessions: async_sessionmaker) -> PacProvider:
    if request.param == 'simulated':
        return SimulatedPac(session_factory=sessions)
    return ScriptedPac()


@pytest.fixture(autouse=True)
def _no_csd_password_in_logs(caplog: pytest.LogCaptureFixture) -> Iterator[None]:
    """SC-007: neither the CSD's password nor its key reach a log, whatever the outcome."""
    caplog.set_level(logging.DEBUG)
    yield
    assert _PASSWORD.decode() not in caplog.text


def _new_uuid() -> str:
    return str(uuid4()).upper()


async def _choose(db: AsyncSession, pac: PacProvider, document_id: int, outcome: str) -> None:
    """The PAC's answer to the document's next cancellation request."""
    if isinstance(pac, ScriptedPac):
        answer = (
            PacRejected(_PAC_CODE, _PAC_MESSAGE) if outcome == 'rejected' else _SCRIPTED[outcome]
        )
        pac.queue('request_cancellation', answer)
        return
    db.expire_all()
    document = await db.get(FiscalDocument, document_id)
    keys = (document.stamp_uuid, document.issuer, document.batch or '', document.serial)
    if outcome == 'rejected':
        await pac.set_next(keys, cancellation=outcome, pac_code=_PAC_CODE, pac_message=_PAC_MESSAGE)
    else:
        await pac.set_next(keys, cancellation=outcome)


def _pac_calls(pac: PacProvider) -> int | None:
    return pac.calls['request_cancellation'] if isinstance(pac, ScriptedPac) else None


async def _cancel(  # noqa: ANN202
    db: AsyncSession,
    current: CurrentUser,
    document_id: int,
    pac: PacProvider,
    reason: str = '02',
    substitution: str | None = None,
):
    return await fiscal_stamping.cancel(
        db, document_id, reason, substitution, pac, current.employee_id
    )


async def _cancelled_as(
    db: AsyncSession,
    current: CurrentUser,
    document_id: int,
    pac: PacProvider,
    outcome: str,
) -> None:
    await _choose(db, pac, document_id, outcome)
    await _cancel(db, current, document_id, pac)
    assert (await _load(db, document_id)).status == (
        'cancelled' if outcome == 'accepted' else 'cancellation_pending'
    )


async def _refused(call, code: str, params: dict | None = None) -> HTTPException:  # noqa: ANN001
    with pytest.raises(HTTPException) as caught:
        await call
    assert caught.value.code == code, caught.value.detail
    if params is not None:
        assert caught.value.params == params
    return caught.value


async def _load(db: AsyncSession, document_id: int) -> FiscalDocument:
    db.expire_all()
    return await fds.get_document(db, document_id)


async def _requests(db: AsyncSession, document_id: int) -> list[FiscalDocumentCancellation]:
    db.expire_all()
    query = (
        select(FiscalDocumentCancellation)
        .where(FiscalDocumentCancellation.fiscal_document == document_id)
        .order_by(FiscalDocumentCancellation.fiscal_document_cancellation_id)
    )
    return list((await db.execute(query)).scalars())


async def _reasons(db: AsyncSession, document_id: int) -> list[str]:
    query = (
        select(Incidence.comment)
        .where(Incidence.instance_id == document_id)
        .order_by(Incidence.incidence_id)
    )
    return list((await db.execute(query)).scalars())


async def _receipts_ready(receipts: Receipts) -> None:  # noqa: F811
    """Cancellation reasons, and the SAT key credit-note confirm needs (as `notes` sets it)."""
    await seed_cancellation_reasons(receipts.db)
    await receipts.db.execute(update(Product).where(Product.product_id == 1).values(key='01010101'))
    await receipts.db.commit()


async def _stamped(db: AsyncSession, current: CurrentUser, sealed: FiscalDocument, pac) -> int:  # noqa: ANN001
    document_id = inspect(sealed).identity[0]
    await fiscal_stamping.stamp(db, document_id, pac, current.employee_id)
    return document_id


# ── /cancel's table (contracts/endpoints.md), T025 ────────────────────────────


async def test_accepted_cancels_the_document_with_its_substitute(
    receipts: Receipts,  # noqa: F811
    pac: PacProvider,
) -> None:
    await _receipts_ready(receipts)
    db, current = receipts.db, receipts.current
    invoice = await _ppd_invoice(db, stamp_uuid=_new_uuid())
    substitute_uuid = _new_uuid()
    await _ppd_invoice(db, stamp_uuid=substitute_uuid, serial=11406)
    await _choose(db, pac, invoice, 'accepted')

    await _cancel(db, current, invoice, pac, '01', substitute_uuid.lower())

    document = await _load(db, invoice)
    assert document.status == 'cancelled'
    assert document.cancelled is True
    assert document.cancellation_date is not None
    assert (document.cancellation_reason, document.cancellation_substitution) == (
        '01',
        substitute_uuid,
    )
    assert document.cancelling_since is None
    [request] = await _requests(db, invoice)
    assert (request.status, request.reason, request.substitution) == (
        'accepted',
        '01',
        substitute_uuid,
    )
    assert request.requested_by == current.employee_id
    assert request.answered_at is not None and request.resolved_at is not None
    assert await _reasons(db, invoice) == ['Cancellation requested (01)', 'CFDI cancelled']
    if isinstance(pac, ScriptedPac):
        [sent] = pac.requests
        assert (sent.reason, sent.substitution, sent.total) == (
            '01',
            substitute_uuid,
            Decimal('3160.00'),
        )
        assert sent.csd.password == _PASSWORD


async def test_pending_leaves_the_document_cancellation_pending(
    receipts: Receipts,  # noqa: F811
    pac: PacProvider,
) -> None:
    await _receipts_ready(receipts)
    db, current = receipts.db, receipts.current
    invoice = await _ppd_invoice(db)
    await _choose(db, pac, invoice, 'pending')

    await _cancel(db, current, invoice, pac)

    document = await _load(db, invoice)
    assert document.status == 'cancellation_pending'
    assert (document.cancelled, document.cancellation_date) == (False, None)
    assert document.cancelling_since is not None
    [request] = await _requests(db, invoice)
    assert (request.status, request.resolved_at) == ('pending', None)
    assert request.answered_at is not None
    assert await _reasons(db, invoice) == [
        'Cancellation requested (02)',
        'Cancellation pending the receiver',
    ]
    # An open request: a second cancel is refused before the PAC.
    calls = _pac_calls(pac)
    await _refused(_cancel(db, current, invoice, pac), 'fiscal_document.cancellation_open')
    assert _pac_calls(pac) == calls
    assert len(await _requests(db, invoice)) == 1


async def test_only_an_issued_document_can_be_cancelled(
    receipts: Receipts,  # noqa: F811
    pac: PacProvider,
) -> None:
    await _receipts_ready(receipts)
    db, current = receipts.db, receipts.current
    invoice = await _ppd_invoice(db)
    draft = (await receipts.ready(invoice, '1000.00')).fiscal_document_id
    sealed = (await receipts.confirm(await receipts.ready(invoice, '1000.00'))).fiscal_document_id
    cancelled = await _ppd_invoice(db, cancelled=True, serial=11407)

    for document_id in (draft, sealed, cancelled):
        await _refused(_cancel(db, current, document_id, pac), 'fiscal_document.not_issued')
        assert await _requests(db, document_id) == []
    assert _pac_calls(pac) in (0, None)


async def test_an_invoice_with_live_receipts_or_credit_notes_is_refused(
    receipts: Receipts,  # noqa: F811
    pac: PacProvider,
) -> None:
    await _receipts_ready(receipts)
    db, current = receipts.db, receipts.current
    invoice = await _ppd_invoice(db)
    receipt = await receipts.confirm(await receipts.ready(invoice, '1000.00'))
    note = await seed_credit_note(
        db, invoice, '160.00', issuer=ISSUER, cancelling_since=datetime(2026, 11, 17, 9, 0)
    )
    note_row = await db.get(FiscalDocument, note)
    expected = [f'P{receipt.serial}', f'{note_row.batch}{note_row.serial}']

    await _refused(
        _cancel(db, current, invoice, pac),
        'fiscal_document.has_live_documents',
        {'documents': expected},
    )
    assert await _requests(db, invoice) == []
    assert _pac_calls(pac) in (0, None)


async def test_refusals_before_the_pac(
    receipts: Receipts,  # noqa: F811
    pac: PacProvider,
) -> None:
    await _receipts_ready(receipts)
    db, current = receipts.db, receipts.current
    invoice = await _ppd_invoice(db, stamp_uuid=_new_uuid())
    unstamped = _new_uuid()
    await _ppd_invoice(db, stamp_uuid=unstamped, serial=11410, completed=False)
    cancelled = _new_uuid()
    await _ppd_invoice(db, stamp_uuid=cancelled, serial=11411, cancelled=True)
    other_issuer = _new_uuid()
    await _ppd_invoice(db, stamp_uuid=other_issuer, serial=11412, issuer=ISSUER_RFC)
    other_recipient = _new_uuid()
    await _ppd_invoice(db, stamp_uuid=other_recipient, serial=11413, recipient='XAXX010101000')

    await _refused(
        _cancel(db, current, invoice, pac, '05'),
        'fiscal_document.unknown_cancellation_reason',
        {'reason': '05'},
    )
    await _refused(
        _cancel(db, current, invoice, pac, '01'), 'fiscal_document.substitution_required'
    )
    for substitute in (_new_uuid(), unstamped, cancelled, other_issuer, other_recipient):
        await _refused(
            _cancel(db, current, invoice, pac, '01', substitute),
            'fiscal_document.substitution_invalid',
            {'substitution': substitute},
        )
    await db.execute(update(TaxpayerCertificate).values(valid_to=datetime(2026, 1, 1)))
    await db.commit()
    await _refused(_cancel(db, current, invoice, pac), 'fiscal_document.no_valid_certificate')

    assert await _requests(db, invoice) == []
    assert (await _load(db, invoice)).status == 'issued'
    assert _pac_calls(pac) in (0, None)


@pytest.mark.parametrize(
    ('outcome', 'code', 'status', 'reason'),
    [
        (
            'not_cancellable',
            'fiscal_document.not_cancellable',
            'not_cancellable',
            'Cancellation refused: not cancellable',
        ),
        ('rejected', 'pac.rejected', 'rejected', f'Cancellation refused by the PAC ({_PAC_CODE})'),
        ('unavailable', 'pac.unavailable', 'failed', 'Cancellation failed: PAC unavailable'),
    ],
)
async def test_a_refusal_by_the_pac_leaves_the_document_issued(
    receipts: Receipts,  # noqa: F811
    pac: PacProvider,
    outcome: str,
    code: str,
    status: str,
    reason: str,
) -> None:
    await _receipts_ready(receipts)
    db, current = receipts.db, receipts.current
    invoice = await _ppd_invoice(db)
    await _choose(db, pac, invoice, outcome)

    error = await _refused(_cancel(db, current, invoice, pac), code)

    if outcome == 'rejected':
        assert error.params == {'pac_code': _PAC_CODE, 'pac_message': _PAC_MESSAGE}
    document = await _load(db, invoice)
    assert (document.status, document.cancelling_since) == ('issued', None)
    [request] = await _requests(db, invoice)
    assert request.status == status
    assert request.resolved_at is not None
    if outcome == 'rejected':
        assert (request.pac_code, request.pac_message) == (_PAC_CODE, _PAC_MESSAGE)
    assert await _reasons(db, invoice) == ['Cancellation requested (02)', reason]
    # Closed, so a new request can be made.
    await _choose(db, pac, invoice, 'pending')
    await _cancel(db, current, invoice, pac)
    assert [r.status for r in await _requests(db, invoice)] == [status, 'pending']


async def test_a_timeout_leaves_the_request_open(
    receipts: Receipts,  # noqa: F811
    pac: PacProvider,
) -> None:
    await _receipts_ready(receipts)
    db, current = receipts.db, receipts.current
    invoice = await _ppd_invoice(db)
    await _choose(db, pac, invoice, 'timeout')

    error = await _refused(_cancel(db, current, invoice, pac), 'pac.timeout')

    assert error.status_code == 504
    assert error.params == {'state': 'cancellation_pending'}
    assert (await _load(db, invoice)).status == 'cancellation_pending'
    [request] = await _requests(db, invoice)
    assert (request.status, request.answered_at) == ('requested', None)
    await _refused(_cancel(db, current, invoice, pac), 'fiscal_document.cancellation_open')


async def test_a_document_the_current_system_stamped_can_be_cancelled(
    db: AsyncSession, current_user: CurrentUser, pac: PacProvider
) -> None:
    await seed_cancellation_reasons(db)
    document_id = await seed_fiscal_document(db, 'invoice')
    issuer = (await db.get(FiscalDocument, document_id)).issuer
    certificate_data, key_data = _make_csd(rfc=issuer)
    db.add(
        TaxpayerCertificate(
            taxpayer_certificate_id=_CERTIFICATE_NUMBER,
            taxpayer=issuer,
            certificate_data=certificate_data,
            key_data=key_data,
            key_password=_PASSWORD,
            valid_from=datetime(2024, 1, 1),
            valid_to=datetime(2027, 12, 31),
            status=EntityStatus.ACTIVE,
        )
    )
    await db.commit()

    await _cancel(db, current_user, document_id, pac)

    # The simulator never saw its stamp date, so SAT waits for the receiver; the fake accepts.
    expected = 'cancellation_pending' if isinstance(pac, SimulatedPac) else 'cancelled'
    assert (await _load(db, document_id)).status == expected
    [request] = await _requests(db, document_id)
    assert request.status == ('pending' if isinstance(pac, SimulatedPac) else 'accepted')


# ── Holds while pending, freed once cancelled (SC-005, FR-016, FR-020), T026 ──


async def test_an_invoice_holds_its_units_until_cancelled(
    notes: CreditNotes,  # noqa: F811
    pac: PacProvider,
) -> None:
    db, current = notes.db, notes.current
    await seed_cancellation_reasons(db)
    _first, _second, pending, lines = await _invoiced_orders(notes)
    _first, _second, twin, twin_lines = await _invoiced_orders(notes)
    # Its own UUID and folio: the simulator keys a document by both.
    await db.execute(
        update(FiscalDocument)
        .where(FiscalDocument.fiscal_document_id == twin)
        .values(stamp_uuid=_new_uuid(), serial=11430)
    )
    await db.commit()

    async def refundable(line) -> Decimal:  # noqa: ANN001
        order_line = await db.get(SalesOrderDetail, line.order_detail)
        return await customer_refund_service.line_refundable(db, order_line)

    await _cancelled_as(db, current, pending, pac, 'pending')
    assert await refundable(lines[0]) == 0

    await _cancelled_as(db, current, twin, pac, 'accepted')
    assert await refundable(twin_lines[0]) == Decimal(10)


async def test_a_pending_receipt_still_counts_on_its_invoice(
    receipts: Receipts,  # noqa: F811
    pac: PacProvider,
) -> None:
    await _receipts_ready(receipts)
    db, current = receipts.db, receipts.current

    async def receipt_on(invoice: int, outcome: str) -> int:
        payment = await _payment_applied_to(db, invoice)
        receipt = await receipts.confirm(await receipts.create(customer_payment=payment))
        receipt_id = await _stamped(db, current, receipt, pac)
        await _cancelled_as(db, current, receipt_id, pac, outcome)
        return payment

    invoice = await _ppd_invoice(db, stamp_uuid=_new_uuid())
    payment = await receipt_on(invoice, 'pending')
    # It still lowers the next receipt's previous balance, and still holds its payment.
    later = await receipts.ready(invoice, '100.00')
    assert _relation(later, invoice)['previous_balance'] == Decimal('2000.00')
    await _refused(
        receipts.create(customer_payment=payment), 'fiscal_document.payment_already_receipted'
    )

    twin = await _ppd_invoice(db, stamp_uuid=_new_uuid(), serial=11420)
    twin_payment = await receipt_on(twin, 'accepted')
    later = await receipts.ready(twin, '100.00')
    assert _relation(later, twin)['previous_balance'] == Decimal('3160.00')
    assert (await receipts.create(customer_payment=twin_payment)).customer_payment == twin_payment


async def test_a_pending_credit_note_still_limits_its_invoice(
    notes: CreditNotes,  # noqa: F811
    pac: PacProvider,
) -> None:
    db, current = notes.db, notes.current
    await seed_cancellation_reasons(db)

    async def note_on(invoice: int, outcome: str) -> FiscalDocument:
        """Lines 1 and 2 credited and cancelled as `outcome`; a second note for line 3."""
        lines = await _lines(db, invoice)
        first = await notes.create(invoice)
        for line in lines[:2]:
            first = await notes.copy(first, line.fiscal_document_detail_id)
        note_id = await _stamped(db, current, await notes.confirm(first), pac)
        await _cancelled_as(db, current, note_id, pac, outcome)
        return await notes.copy(await notes.create(invoice), lines[2].fiscal_document_detail_id)

    invoice = await _invoice(db)
    second = await note_on(invoice, 'pending')
    await _refused(
        notes.confirm(second),
        'fiscal_document.credit_exceeds_invoice',
        {'invoice_id': invoice, 'remaining': '1750.00'},
    )

    twin = await _invoice(db, stamp_uuid=_new_uuid(), serial=11421)
    second = await note_on(twin, 'accepted')
    assert (await notes.confirm(second)).status == 'sealed'


async def test_an_invoice_being_cancelled_takes_no_receipt_or_credit_note(
    receipts: Receipts,  # noqa: F811
    pac: PacProvider,
) -> None:
    await _receipts_ready(receipts)
    db, current = receipts.db, receipts.current
    credit_notes = CreditNotes(db, current)
    invoice = await _ppd_invoice(db)
    receipt_draft = await receipts.ready(invoice, '100.00')
    line = (await _lines(db, invoice))[0].fiscal_document_detail_id
    note_draft = await credit_notes.copy(await credit_notes.create(invoice), line)

    await _cancelled_as(db, current, invoice, pac, 'pending')

    await _refused(
        receipts.pay(await receipts.create(), invoice), 'fiscal_document.invoice_not_payable'
    )
    await _refused(receipts.confirm(receipt_draft), 'fiscal_document.invoice_not_payable')
    await _refused(credit_notes.create(invoice), 'fiscal_document.invoice_not_creditable')
    await _refused(credit_notes.confirm(note_draft), 'fiscal_document.invoice_not_creditable')

    # Email: a pending document can be sent, a cancelled one cannot.
    mailer = RecordingMailer()
    await email_document(db, await _load(db, invoice), ['a@b.mx'], mailer=mailer, current=current)
    assert len(mailer.sent) == 1
    twin = await _ppd_invoice(db, stamp_uuid=_new_uuid(), serial=11422)
    await _cancelled_as(db, current, twin, pac, 'accepted')
    await _refused(
        email_document(db, await _load(db, twin), ['a@b.mx'], mailer=mailer, current=current),
        'fiscal_document.not_emailable',
    )


async def test_cancelling_a_credit_note_reopens_the_order_it_settled(
    notes: CreditNotes,  # noqa: F811
    pac: PacProvider,
) -> None:
    db, current = notes.db, notes.current
    await seed_cancellation_reasons(db)

    async def settled(outcome: str) -> int:
        first, _second, invoice, lines = await _invoiced_orders(notes)
        note = await notes.confirm(
            await notes.copy(await notes.create(invoice), lines[0].fiscal_document_detail_id)
        )
        note_id = await _stamped(db, current, note, pac)
        assert await _paid(db, first) is True
        await _cancelled_as(db, current, note_id, pac, outcome)
        return first

    assert await _paid(db, await settled('pending')) is True
    assert await _paid(db, await settled('accepted')) is False


async def test_xml_and_pdf_while_issued_pending_and_cancelled(
    notes: CreditNotes,  # noqa: F811
    sessions: async_sessionmaker,
    client: AsyncClient,
) -> None:
    db, current = notes.db, notes.current
    await seed_cancellation_reasons(db)
    pac = SimulatedPac(session_factory=sessions)
    invoice = await _invoice(db)
    lines = await _lines(db, invoice)

    async def stamped_note(line) -> int:  # noqa: ANN001
        draft = await notes.copy(await notes.create(invoice), line.fiscal_document_detail_id)
        return await _stamped(db, current, await notes.confirm(draft), pac)

    async def readable(document_id: int) -> None:
        uuid = (await _load(db, document_id)).stamp_uuid
        xml = await client.get(f'/api/v1/fiscal-documents/{document_id}/xml')
        assert xml.status_code == 200, xml.text
        assert uuid in xml.text
        pdf = await client.get(f'/api/v1/fiscal-documents/{document_id}/pdf')
        assert pdf.status_code == 200, pdf.text
        text = ''.join(
            ''.join(page.extract_text().split())
            for page in PdfReader(io.BytesIO(pdf.content)).pages
        )
        assert uuid in text

    pending = await stamped_note(lines[0])
    await readable(pending)
    await _cancelled_as(db, current, pending, pac, 'pending')
    await readable(pending)
    cancelled = await stamped_note(lines[1])
    await _cancelled_as(db, current, cancelled, pac, 'accepted')
    await readable(cancelled)


# ── Refresh resolves a pending cancellation (US3), T034 ───────────────────────

_RECEIVER_STATUS = {
    'accept': SatStatus(state='cancelled', cancellable='with_acceptance', cancellation='accepted'),
    'reject': SatStatus(state='valid', cancellable='with_acceptance', cancellation='rejected'),
    'expire': SatStatus(state='cancelled', cancellable='with_acceptance', cancellation='expired'),
}


async def _receiver(db: AsyncSession, pac: PacProvider, document_id: int, answer: str) -> None:
    """The receiver answers the pending cancellation; the fake reports it on its next query."""
    if isinstance(pac, ScriptedPac):
        pac.queue('query_status', _RECEIVER_STATUS[answer])
        return
    db.expire_all()
    document = await db.get(FiscalDocument, document_id)
    keys = (document.stamp_uuid, document.issuer, document.batch or '', document.serial)
    await pac.answer_as_receiver(keys, answer)


async def _refresh(db: AsyncSession, current: CurrentUser, document_id: int, pac) -> None:  # noqa: ANN001
    await fiscal_stamping.refresh(db, document_id, pac, current.employee_id)


@pytest.mark.parametrize(
    ('answer', 'status', 'state', 'reason'),
    [
        ('accept', 'accepted', 'cancelled', 'CFDI cancelled'),
        ('reject', 'rejected', 'issued', 'Cancellation rejected'),
        ('expire', 'expired', 'cancelled', 'CFDI cancelled'),
    ],
)
async def test_refresh_follows_the_receivers_answer(
    receipts: Receipts,  # noqa: F811
    pac: PacProvider,
    answer: str,
    status: str,
    state: str,
    reason: str,
) -> None:
    await _receipts_ready(receipts)
    db, current = receipts.db, receipts.current
    invoice = await _ppd_invoice(db)
    await _cancelled_as(db, current, invoice, pac, 'pending')
    await _receiver(db, pac, invoice, answer)

    await _refresh(db, current, invoice, pac)

    document = await _load(db, invoice)
    assert (document.status, document.cancelling_since) == (state, None)
    assert document.cancelled is (state == 'cancelled')
    [request] = await _requests(db, invoice)
    assert request.status == status
    assert request.resolved_at is not None and request.last_checked_at is not None
    assert await _reasons(db, invoice) == [
        'Cancellation requested (02)',
        'Cancellation pending the receiver',
        reason,
    ]
    # Resolved: nothing is left to refresh.
    await _refused(_refresh(db, current, invoice, pac), 'fiscal_document.nothing_to_refresh')
    if answer == 'reject':
        # Rejected by the receiver, the document can be cancelled again.
        await _cancelled_as(db, current, invoice, pac, 'accepted')
        assert [r.status for r in await _requests(db, invoice)] == ['rejected', 'accepted']


async def test_refresh_closes_a_request_sat_never_received(
    receipts: Receipts,  # noqa: F811
) -> None:
    """Only the fake can answer `valid`/`none` for an open request; the simulator never does."""
    await _receipts_ready(receipts)
    db, current = receipts.db, receipts.current
    pac = ScriptedPac()
    invoice = await _ppd_invoice(db)
    await _cancelled_as(db, current, invoice, pac, 'pending')
    pac.queue(
        'query_status',
        SatStatus(state='valid', cancellable='with_acceptance', cancellation='none'),
    )

    # Within 72 hours of the PAC's answer SAT may still be processing it (spec 040, FR-014).
    await _refresh(db, current, invoice, pac)
    assert (await _load(db, invoice)).status == 'cancellation_pending'
    [request] = await _requests(db, invoice)
    assert (request.status, request.resolved_at) == ('pending', None)

    await db.execute(
        update(FiscalDocumentCancellation)
        .where(FiscalDocumentCancellation.fiscal_document == invoice)
        .values(answered_at=request.answered_at - timedelta(hours=72))
    )
    await db.commit()
    pac.queue(
        'query_status',
        SatStatus(state='valid', cancellable='with_acceptance', cancellation='none'),
    )
    await _refresh(db, current, invoice, pac)

    document = await _load(db, invoice)
    assert (document.status, document.cancelling_since) == ('issued', None)
    [request] = await _requests(db, invoice)
    assert (request.status, request.resolved_at is not None) == ('failed', True)
    assert await _reasons(db, invoice) == [
        'Cancellation requested (02)',
        'Cancellation pending the receiver',
        'Cancellation not received by SAT',
    ]


async def test_refresh_of_a_timed_out_cancel_reports_it_pending(
    receipts: Receipts,  # noqa: F811
    pac: PacProvider,
) -> None:
    await _receipts_ready(receipts)
    db, current = receipts.db, receipts.current
    invoice = await _ppd_invoice(db)
    await _choose(db, pac, invoice, 'timeout')
    await _refused(_cancel(db, current, invoice, pac), 'pac.timeout')
    if isinstance(pac, ScriptedPac):
        pac.queue(
            'query_status',
            SatStatus(state='valid', cancellable='with_acceptance', cancellation='pending'),
        )

    await _refresh(db, current, invoice, pac)

    assert (await _load(db, invoice)).status == 'cancellation_pending'
    [request] = await _requests(db, invoice)
    assert request.status == 'pending'
    assert request.answered_at is not None and request.last_checked_at is not None
    assert (await _reasons(db, invoice))[-1] == 'Cancellation pending the receiver'


# ── Multifacturas through the adapter (spec 040, US3), T016 ───────────────────


def _sat_says(state: str, cancellation: str) -> libsat.SatStatus:
    return libsat.SatStatus(
        state=libsat.SatState(state),
        cancellability=libsat.Cancellability.WITH_ACCEPTANCE,
        cancellation=libsat.CancellationStatus(cancellation),
    )


@pytest.mark.parametrize(
    ('sat', 'status', 'closed_as', 'last_reason'),
    [
        (('cancelled', 'accepted'), 'cancelled', 'accepted', 'CFDI cancelled'),
        (('cancelled', 'expired'), 'cancelled', 'expired', 'CFDI cancelled'),
        (('valid', 'rejected'), 'issued', 'rejected', 'Cancellation rejected'),
    ],
    ids=['cancelled', 'expired', 'receiver_rejected'],
)
async def test_multifacturas_201_waits_for_sat_then_follows_it(
    receipts: Receipts,  # noqa: F811
    sat: tuple[str, str],
    status: str,
    closed_as: str,
    last_reason: str,
) -> None:
    """US3 scenarios 1, 3 and 5: the PAC's 201 leaves the request open; SAT's status settles it."""
    await _receipts_ready(receipts)
    db, current = receipts.db, receipts.current
    provider, client = scripted_multifacturas()
    invoice = await _ppd_invoice(db, stamp_uuid=_new_uuid())

    await _cancel(db, current, invoice, provider)

    assert (await _load(db, invoice)).status == 'cancellation_pending'
    # SAT still shows it pending the receiver: nothing changes.
    client.queue('query_status', _sat_says('valid', 'pending'))
    await _refresh(db, current, invoice, provider)
    assert (await _load(db, invoice)).status == 'cancellation_pending'

    client.queue('query_status', _sat_says(*sat))
    await _refresh(db, current, invoice, provider)

    document = await _load(db, invoice)
    assert (document.status, document.cancelling_since) == (status, None)
    [request] = await _requests(db, invoice)
    assert request.status == closed_as
    assert (await _reasons(db, invoice))[-1] == last_reason


async def test_multifacturas_202_cancels_at_once(
    receipts: Receipts,  # noqa: F811
) -> None:
    """US3 scenario 2."""
    await _receipts_ready(receipts)
    db, current = receipts.db, receipts.current
    provider, client = scripted_multifacturas()
    client.queue('request_cancellation', lib.CancellationAnswer.ACCEPTED)
    invoice = await _ppd_invoice(db, stamp_uuid=_new_uuid())

    await _cancel(db, current, invoice, provider)

    assert (await _load(db, invoice)).status == 'cancelled'
    [request] = await _requests(db, invoice)
    assert request.status == 'accepted'


async def test_a_multifacturas_refusal_leaves_the_document_issued(
    receipts: Receipts,  # noqa: F811
) -> None:
    """US3 scenario 7: the code and message are stored and returned; no "not cancellable" is
    inferred from them (FR-015)."""
    await _receipts_ready(receipts)
    db, current = receipts.db, receipts.current
    provider, client = scripted_multifacturas()
    client.queue('request_cancellation', lib.PacRejected('CSF101', 'El CFDI no es cancelable'))
    invoice = await _ppd_invoice(db, stamp_uuid=_new_uuid())

    await _refused(
        _cancel(db, current, invoice, provider),
        'pac.rejected',
        {'pac_code': 'CSF101', 'pac_message': 'El CFDI no es cancelable'},
    )

    assert (await _load(db, invoice)).status == 'issued'
    [request] = await _requests(db, invoice)
    assert (request.status, request.pac_code, request.pac_message) == (
        'rejected',
        'CSF101',
        'El CFDI no es cancelable',
    )


async def test_multifacturas_receives_reason_01_with_its_substitute(
    receipts: Receipts,  # noqa: F811
) -> None:
    await _receipts_ready(receipts)
    db, current = receipts.db, receipts.current
    provider, client = scripted_multifacturas()
    invoice = await _ppd_invoice(db, stamp_uuid=_new_uuid())
    substitute = _new_uuid()
    await _ppd_invoice(db, stamp_uuid=substitute, serial=11406)

    await _cancel(db, current, invoice, provider, '01', substitute)

    [sent] = client.requests
    assert (sent.reason, sent.substitute_uuid) == ('01', substitute)
    assert sent.csd.password == _PASSWORD.decode()


async def test_a_multifacturas_receipt_cancelled_frees_the_invoices_balance(
    receipts: Receipts,  # noqa: F811
) -> None:
    """Spec 028's hold is released once SAT cancels the receipt (US3 scenario 3)."""
    await _receipts_ready(receipts)
    db, current = receipts.db, receipts.current
    provider, client = scripted_multifacturas()
    invoice = await _ppd_invoice(db, stamp_uuid=_new_uuid())
    payment = await _payment_applied_to(db, invoice)
    receipt = await receipts.confirm(await receipts.create(customer_payment=payment))
    receipt_id = await _stamped(db, current, receipt, provider)

    await _cancel(db, current, receipt_id, provider)
    later = await receipts.ready(invoice, '100.00')
    assert _relation(later, invoice)['previous_balance'] == Decimal('2000.00')
    client.queue('query_status', _sat_says('cancelled', 'accepted'))
    await _refresh(db, current, receipt_id, provider)

    assert (await _load(db, receipt_id)).status == 'cancelled'
    later = await receipts.ready(invoice, '100.00')
    assert _relation(later, invoice)['previous_balance'] == Decimal('3160.00')


async def test_a_multifacturas_credit_note_cancelled_reopens_its_order(
    notes: CreditNotes,  # noqa: F811
) -> None:
    db, current = notes.db, notes.current
    await seed_cancellation_reasons(db)
    provider, client = scripted_multifacturas()
    first, _second, invoice, lines = await _invoiced_orders(notes)
    note = await notes.confirm(
        await notes.copy(await notes.create(invoice), lines[0].fiscal_document_detail_id)
    )
    note_id = await _stamped(db, current, note, provider)

    await _cancel(db, current, note_id, provider)
    assert await _paid(db, first) is True
    client.queue('query_status', _sat_says('cancelled', 'accepted'))
    await _refresh(db, current, note_id, provider)

    assert (await _load(db, note_id)).status == 'cancelled'
    assert await _paid(db, first) is False
