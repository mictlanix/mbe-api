"""Stamping and cancelling a CFDI through the PAC provider (spec 039, research R3, R8 and R15).

Each step is its own transaction, and no lock is held while the PAC answers:

- transaction A locks the document, checks it is sealed and commits `stamping_since`, the durable
  record that a stamp is with the PAC (FR-011);
- the PAC call runs with no transaction open;
- transaction B locks the document again and stores the stamp (`_store_stamp`).

A second request finds the marker on the locked row and never reaches the PAC (FR-012). Neither
the XML, the CSD nor the PAC's raw errors are logged or returned; `pac.rejected` carries only the
PAC's code and message.

Cancel follows the same pattern: transaction A checks the document and commits the request and
`cancelling_since`; the PAC answers with no transaction open; transaction B applies the answer.
"""

from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import local_now
from app.core.error_codes import ErrorCode
from app.core.errors import DomainError
from app.enums import FiscalDocumentType, SourceType
from app.models.fiscal import (
    FiscalDocument,
    FiscalDocumentCancellation,
    FiscalDocumentDetail,
    FiscalDocumentRelation,
    FiscalDocumentXml,
)
from app.models.sales import SalesOrder, SalesOrderDetail
from app.models.sat_catalog import SatReasonCancellation
from app.services import customer_payment_service, incidences, sales_order_service
from app.services import fiscal_document_service as fds
from app.services.inventory_documents import busy_as_conflict
from app.services.invoice_service import _certificate, issue_date_ok
from app.services.pac.provider import (
    CancellationAnswer,
    CancellationRequest,
    Csd,
    PacProvider,
    PacRejected,
    PacTimeout,
    PacUnavailable,
    SatStatus,
    Stamp,
)

BUSY = 'The fiscal document is busy, retry'
RESOURCE = 'fiscal_document'
_STAMPED = ('issued', 'cancellation_pending', 'cancelled')
# The `fiscal_document_cancellation` columns the PAC's text is kept in (contracts/pac-provider.md).
PAC_CODE_LENGTH = 20
PAC_MESSAGE_LENGTH = 500


async def _begin_locked(db: AsyncSession, document_id: int) -> FiscalDocument:
    """R15 rule 1: end the transaction the route opened, then lock the row and read it fresh.

    The plain reads that follow open their snapshot after the lock was granted, so they see what
    was committed before it.
    """
    await db.commit()
    query = (
        select(FiscalDocument)
        .where(FiscalDocument.fiscal_document_id == document_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return (await db.execute(query)).scalar_one()


def _pac_timeout(state: str) -> DomainError:
    detail = 'The PAC did not answer in time; refresh the document'
    return DomainError(ErrorCode.PAC_TIMEOUT, detail, {'state': state})


async def stamp(
    db: AsyncSession, document_id: int, provider: PacProvider, employee_id: int
) -> FiscalDocument:
    """Stamp a sealed document. A stamped one (issued, pending or cancelled) comes back as it is."""
    async with busy_as_conflict(db, BUSY, resource=RESOURCE):
        document = await _begin_locked(db, document_id)
        sealed_xml = await fds.get_xml(db, document_id)
        state = fds.document_status(document, has_xml=sealed_xml is not None)
        if state == 'draft':
            raise DomainError(ErrorCode.FISCAL_DOCUMENT_NOT_SEALED, 'Fiscal document is not sealed')
        if state in _STAMPED:
            await db.commit()
            return document
        if state == 'stamping':
            raise DomainError(
                ErrorCode.FISCAL_DOCUMENT_STAMPING,
                'A stamp is in process; refresh the document instead',
            )
        if not issue_date_ok(document.issued, now=local_now()):
            raise DomainError(
                ErrorCode.FISCAL_DOCUMENT_ISSUE_DATE_OUT_OF_RANGE,
                'The issue date must be within the last 72 hours and not in the future',
            )
        marker = local_now().replace(microsecond=0)
        document.stamping_since = marker
        await db.commit()

    try:
        issued = await provider.stamp(sealed_xml)
    except PacRejected as error:
        await _clear_marker(db, document_id, marker)
        raise DomainError(
            ErrorCode.PAC_REJECTED,
            'The PAC rejected the document',
            {
                'pac_code': error.code[:PAC_CODE_LENGTH],
                'pac_message': error.message[:PAC_MESSAGE_LENGTH],
            },
        ) from None
    except PacUnavailable:
        await _clear_marker(db, document_id, marker)
        raise DomainError(ErrorCode.PAC_UNAVAILABLE, 'The PAC could not be reached') from None
    except PacTimeout:
        raise _pac_timeout('stamping') from None
    return await _store_stamp(
        db, document_id, sealed_xml, issued, employee_id, reason=f'CFDI stamped {issued.uuid}'
    )


async def _clear_marker(db: AsyncSession, document_id: int, marker: datetime) -> None:
    """Sealed again, unless another request has set its own marker since (R3)."""
    async with busy_as_conflict(db, BUSY, resource=RESOURCE):
        document = await _begin_locked(db, document_id)
        if document.stamping_since == marker:
            document.stamping_since = None
        await db.commit()


async def _store_stamp(
    db: AsyncSession,
    document_id: int,
    sealed_xml: str,
    issued: Stamp,
    employee_id: int,
    *,
    reason: str,
) -> FiscalDocument:
    """Transaction B (R15 rule 5): store `issued` if the document still holds `sealed_xml`.

    A document a refresh already completed comes back as it is. One whose XML changed (reset past
    the look-up grace and reopened) keeps it: the UUID is audited and `pac.timeout` reports the
    document's state. `reason` is the incidence recorded with the stamp.
    """
    changed_state = None
    async with busy_as_conflict(db, BUSY, resource=RESOURCE):
        document = await _begin_locked(db, document_id)
        if document.completed:
            await db.commit()
            return document
        # Rule 11: the XML as the row holds it now, never an object loaded earlier.
        current_xml = await fds.get_xml(db, document_id)
        if current_xml != sealed_xml:
            changed_state = fds.document_status(document, has_xml=current_xml is not None)
            reason = f'CFDI stamp {issued.uuid} not stored: the document changed'
        else:
            document.stamp_uuid = issued.uuid
            document.stamped = issued.stamped_at
            document.authority_digital_seal = issued.sat_seal
            document.authority_certificate_number = issued.sat_certificate
            document.rfc_pac = issued.provider_rfc
            document.completed = True
            document.stamping_since = None
            await db.execute(
                update(FiscalDocumentXml)
                .where(FiscalDocumentXml.fiscal_document_xml_id == document_id)
                .values(data=issued.xml)
            )
        incidences.record(
            db,
            source=SourceType.FISCAL_DOCUMENT,
            instance_id=document_id,
            updater=employee_id,
            reason=reason,
        )
        await db.commit()
    if changed_state is not None:
        raise _pac_timeout(changed_state)
    return document


# ── Cancel (research R8) ──────────────────────────────────────────────────────


def _now() -> datetime:
    """Whole seconds, as DATETIME stores them (R15 rule 11)."""
    return local_now().replace(microsecond=0)


def _record(db: AsyncSession, document_id: int, employee_id: int, reason: str) -> None:
    incidences.record(
        db,
        source=SourceType.FISCAL_DOCUMENT,
        instance_id=document_id,
        updater=employee_id,
        reason=reason,
    )


async def _live_related(db: AsyncSession, invoice_id: int) -> list[str]:
    """`batch + serial` of the live receipts and credit notes that relate the invoice."""
    rows = await db.execute(
        select(FiscalDocument.batch, FiscalDocument.serial)
        .join(
            FiscalDocumentRelation,
            FiscalDocumentRelation.document == FiscalDocument.fiscal_document_id,
        )
        .where(
            FiscalDocumentRelation.relation == invoice_id,
            FiscalDocument.type.in_((fds.PAYMENT_RECEIPT, fds.CREDIT_NOTE)),
            fds.live(),
        )
        .order_by(FiscalDocument.fiscal_document_id)
    )
    return [f'{batch or ""}{serial}' for batch, serial in rows.all()]


async def _credited_ids(db: AsyncSession, note_id: int) -> tuple[int | None, list[int]]:
    """A credit note's invoice and that invoice's orders, read before the PAC call (R15 rule 10).
    A sealed note's relation and a completed invoice's lines never change."""
    invoice = (
        await db.execute(
            select(FiscalDocumentRelation.relation)
            .where(FiscalDocumentRelation.document == note_id)
            .order_by(FiscalDocumentRelation.fiscal_document_relation_id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if invoice is None:
        return None, []
    order_ids = (
        await db.execute(
            select(SalesOrderDetail.sales_order)
            .distinct()
            .join(
                FiscalDocumentDetail,
                FiscalDocumentDetail.order_detail == SalesOrderDetail.sales_order_detail_id,
            )
            .where(FiscalDocumentDetail.document == invoice)
        )
    ).scalars()
    return invoice, sorted(order_ids.all())


async def _lock_credited(
    db: AsyncSession, invoice_id: int | None, order_ids: list[int]
) -> list[SalesOrder]:
    """After the note's lock: its invoice, then its orders by primary key (R15 rule 10)."""
    if invoice_id is None:
        return []
    await db.execute(
        select(FiscalDocument)
        .where(FiscalDocument.fiscal_document_id == invoice_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not order_ids:
        return []
    query = (
        select(SalesOrder)
        .where(SalesOrder.sales_order_id.in_(order_ids))
        .order_by(SalesOrder.sales_order_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return list((await db.execute(query)).scalars().all())


async def _apply_cancelled(
    db: AsyncSession,
    document: FiscalDocument,
    reason: str,
    substitution: str | None,
    orders: Sequence[SalesOrder],
    employee_id: int,
) -> None:
    """The document becomes cancelled; shared by cancel and refresh.

    `orders` are a credit note's invoice's orders, already locked (R15 rule 10): an order the note
    settled is flagged unpaid once the note stops counting (spec 033, FR-011), as `reopen` does.
    `before` is computed while the note is still live.
    """
    before = await sales_order_service.order_balances(db, orders) if orders else {}
    document.cancelled = True
    document.cancellation_date = _now()
    document.cancellation_reason = reason
    document.cancellation_substitution = substitution
    document.cancelling_since = None
    if orders:
        await db.flush()
        after = await sales_order_service.order_balances(db, orders)
        for order in orders:
            oid = order.sales_order_id
            customer_payment_service.reopen(order, before[oid].balance, after[oid].balance)
    _record(db, document.fiscal_document_id, employee_id, 'CFDI cancelled')


async def cancel(
    db: AsyncSession,
    document_id: int,
    reason: str,
    substitution: UUID | str | None,
    provider: PacProvider,
    employee_id: int,
) -> FiscalDocument:
    """Ask SAT to cancel an issued document: it is cancelled at once or left pending (R8)."""
    async with busy_as_conflict(db, BUSY, resource=RESOURCE):
        document = await _begin_locked(db, document_id)
        xml = await fds.get_xml(db, document_id)
        state = fds.document_status(document, has_xml=xml is not None)
        if state not in ('issued', 'cancellation_pending'):
            raise DomainError(
                ErrorCode.FISCAL_DOCUMENT_NOT_ISSUED, 'Fiscal document has not been issued'
            )
        if document.cancelling_since is not None:
            raise DomainError(
                ErrorCode.FISCAL_DOCUMENT_CANCELLATION_OPEN,
                'A cancellation request is already in process',
            )
        known = await db.execute(
            select(SatReasonCancellation.sat_reason_cancellation_id).where(
                SatReasonCancellation.sat_reason_cancellation_id == reason
            )
        )
        if known.first() is None:
            raise DomainError(
                ErrorCode.FISCAL_DOCUMENT_UNKNOWN_CANCELLATION_REASON,
                'Unknown cancellation reason',
                {'reason': reason},
            )
        if reason != '01':
            substitution = None
        elif substitution is None:
            raise DomainError(
                ErrorCode.FISCAL_DOCUMENT_SUBSTITUTION_REQUIRED,
                "Reason 01 needs the substitute document's UUID",
            )
        else:
            given, substitution = str(substitution), str(substitution).upper()
            found = await db.execute(
                select(FiscalDocument.fiscal_document_id).where(
                    FiscalDocument.issuer == document.issuer,
                    FiscalDocument.recipient == document.recipient,
                    FiscalDocument.completed.is_(True),
                    FiscalDocument.cancelled.is_(False),
                    func.upper(FiscalDocument.stamp_uuid) == substitution,
                )
            )
            if found.first() is None:
                raise DomainError(
                    ErrorCode.FISCAL_DOCUMENT_SUBSTITUTION_INVALID,
                    'The substitute is not a stamped, live document of the same issuer and '
                    'recipient',
                    {'substitution': given},
                )
        if document.type == FiscalDocumentType.INVOICE:
            live = await _live_related(db, document_id)
            if live:
                raise DomainError(
                    ErrorCode.FISCAL_DOCUMENT_HAS_LIVE_DOCUMENTS,
                    'Cancel the payment receipts and credit notes related to the invoice first',
                    {'documents': live},
                )
        certificate = await _certificate(db, document.issuer, local_now())
        csd = Csd(
            certificate=certificate.certificate_data,
            key=certificate.key_data,
            password=certificate.key_password,
        )
        credit_note = document.type == FiscalDocumentType.CREDIT_NOTE
        invoice_id, order_ids = await _credited_ids(db, document_id) if credit_note else (None, [])
        requested_at = _now()
        request = FiscalDocumentCancellation(
            fiscal_document=document_id,
            reason=reason,
            substitution=substitution,
            status='requested',
            requested_by=employee_id,
            requested_at=requested_at,
        )
        db.add(request)
        document.cancelling_since = requested_at
        _record(db, document_id, employee_id, f'Cancellation requested ({reason})')
        await db.commit()
        request_id = request.fiscal_document_cancellation_id
        cancellation = CancellationRequest(
            issuer_rfc=document.issuer,
            recipient_rfc=document.recipient,
            uuid=document.stamp_uuid,
            total=fds.xml_total(xml) or Decimal(0),
            reason=reason,
            substitution=substitution,
            csd=csd,
        )

    rejected: PacRejected | None = None
    try:
        answer = await provider.request_cancellation(cancellation)
    except PacRejected as error:
        answer, rejected = 'rejected', error
    except PacUnavailable:
        answer = 'failed'
    except PacTimeout:
        # The request stays `requested` and the document pending; a refresh resolves it.
        raise _pac_timeout('cancellation_pending') from None
    return await _apply_answer(
        db,
        document_id,
        request_id,
        answer,
        rejected,
        reason=reason,
        substitution=substitution,
        credited=(invoice_id, order_ids) if credit_note else None,
        employee_id=employee_id,
    )


async def _apply_answer(
    db: AsyncSession,
    document_id: int,
    request_id: int,
    answer: CancellationAnswer | str,
    rejected: PacRejected | None,
    *,
    reason: str,
    substitution: str | None,
    credited: tuple[int | None, list[int]] | None,
    employee_id: int,
) -> FiscalDocument:
    """Transaction B (R8's table), applied only while the request is still `requested` (rule 7).
    Otherwise a refresh resolved it, and the document comes back as it now is."""
    error = None
    async with busy_as_conflict(db, BUSY, resource=RESOURCE):
        document = await _begin_locked(db, document_id)
        orders: list[SalesOrder] = []
        if credited is not None and answer == CancellationAnswer.ACCEPTED:
            orders = await _lock_credited(db, *credited)
        # Rule 11: the status as the row holds it now, not the object transaction A inserted.
        status = (
            await db.execute(
                select(FiscalDocumentCancellation.status).where(
                    FiscalDocumentCancellation.fiscal_document_cancellation_id == request_id
                )
            )
        ).scalar_one()
        if status == 'requested':
            now = _now()
            values: dict[str, object] = {'status': str(answer), 'answered_at': now}
            if answer == CancellationAnswer.ACCEPTED:
                await _apply_cancelled(db, document, reason, substitution, orders, employee_id)
            elif answer == CancellationAnswer.PENDING:
                _record(db, document_id, employee_id, 'Cancellation pending the receiver')
            elif answer == CancellationAnswer.NOT_CANCELLABLE:
                _record(db, document_id, employee_id, 'Cancellation refused: not cancellable')
                error = DomainError(
                    ErrorCode.FISCAL_DOCUMENT_NOT_CANCELLABLE,
                    'SAT reports the document cannot be cancelled',
                )
            elif answer == 'rejected':
                code = rejected.code[:PAC_CODE_LENGTH]
                message = rejected.message[:PAC_MESSAGE_LENGTH]
                values.update(pac_code=code, pac_message=message)
                _record(db, document_id, employee_id, f'Cancellation refused by the PAC ({code})')
                error = DomainError(
                    ErrorCode.PAC_REJECTED,
                    'The PAC rejected the cancellation',
                    {'pac_code': code, 'pac_message': message},
                )
            else:  # failed: the request never reached the PAC
                del values['answered_at']
                _record(db, document_id, employee_id, 'Cancellation failed: PAC unavailable')
                error = DomainError(ErrorCode.PAC_UNAVAILABLE, 'The PAC could not be reached')
            if answer != CancellationAnswer.PENDING:
                values['resolved_at'] = now
                document.cancelling_since = None
            await db.execute(
                update(FiscalDocumentCancellation)
                .where(FiscalDocumentCancellation.fiscal_document_cancellation_id == request_id)
                .values(**values)
            )
        await db.commit()
    if error is not None:
        raise error
    return document


# ── Refresh (research R8 "Refresh", R15 rules 6 and 7) ───────────────────────

# A stamp not found returns the document to sealed, and SAT's "never received" closes a
# `requested` request, only past this grace: the request that sent it may still be waiting (R3).
STAMP_LOOKUP_GRACE = timedelta(minutes=5)
_OPEN = ('requested', 'pending')


async def refresh(
    db: AsyncSession, document_id: int, provider: PacProvider, employee_id: int
) -> FiscalDocument:
    """Resolve a stamping document or an open cancellation request from the PAC's answer (R8).

    Transaction 1 reads what to ask and commits; the PAC answers with no transaction open;
    transaction 2 writes only if what transaction 1 read is still true (R15 rule 6).
    """
    async with busy_as_conflict(db, BUSY, resource=RESOURCE):
        document = await _begin_locked(db, document_id)
        xml = await fds.get_xml(db, document_id)
        state = fds.document_status(document, has_xml=xml is not None)
        request = None
        if state == 'cancellation_pending':
            request = (
                await db.execute(
                    select(
                        FiscalDocumentCancellation.fiscal_document_cancellation_id,
                        FiscalDocumentCancellation.status,
                        FiscalDocumentCancellation.requested_at,
                        FiscalDocumentCancellation.reason,
                        FiscalDocumentCancellation.substitution,
                    ).where(
                        FiscalDocumentCancellation.fiscal_document == document_id,
                        FiscalDocumentCancellation.status.in_(_OPEN),
                    )
                )
            ).first()
        if state != 'stamping' and request is None:
            raise DomainError(
                ErrorCode.FISCAL_DOCUMENT_NOTHING_TO_REFRESH,
                'Neither a stamp nor a cancellation is in process',
            )
        marker = document.stamping_since
        credited = None
        if request is not None:
            issuer, recipient, uuid = document.issuer, document.recipient, document.stamp_uuid
            if document.type == FiscalDocumentType.CREDIT_NOTE:
                credited = await _credited_ids(db, document_id)
        await db.commit()

    try:
        if request is None:
            found = await provider.find_stamp(xml)
        else:
            sat = await provider.query_status(
                issuer, recipient, uuid, fds.xml_total(xml) or Decimal(0)
            )
    except PacRejected as error:
        raise DomainError(
            ErrorCode.PAC_REJECTED,
            'The PAC rejected the query',
            {
                'pac_code': error.code[:PAC_CODE_LENGTH],
                'pac_message': error.message[:PAC_MESSAGE_LENGTH],
            },
        ) from None
    except PacUnavailable:
        raise DomainError(ErrorCode.PAC_UNAVAILABLE, 'The PAC could not be reached') from None
    except PacTimeout:
        raise _pac_timeout(state) from None

    if request is None:
        if found is not None:
            return await _store_stamp(
                db, document_id, xml, found, employee_id, reason=f'CFDI stamp found {found.uuid}'
            )
        return await _not_stamped(db, document_id, marker, employee_id)
    return await _apply_status(db, document_id, request, sat, credited, employee_id)


async def _not_stamped(
    db: AsyncSession, document_id: int, marker: datetime, employee_id: int
) -> FiscalDocument:
    """Sealed again past the grace, if the marker is still the one read before `find_stamp`."""
    async with busy_as_conflict(db, BUSY, resource=RESOURCE):
        document = await _begin_locked(db, document_id)
        if document.stamping_since == marker and _now() - marker > STAMP_LOOKUP_GRACE:
            document.stamping_since = None
            _record(db, document_id, employee_id, 'CFDI stamp not found, sealed again')
        await db.commit()
    return document


async def _apply_status(
    db: AsyncSession,
    document_id: int,
    request: Sequence,
    sat: SatStatus,
    credited: tuple[int | None, list[int]] | None,
    employee_id: int,
) -> FiscalDocument:
    """Transaction 2 for an open request: the document follows SAT (FR-021), applied only to the
    request transaction 1 kept and only while its status is unchanged (rule 6)."""
    request_id, kept, requested_at, reason, substitution = request
    async with busy_as_conflict(db, BUSY, resource=RESOURCE):
        document = await _begin_locked(db, document_id)
        orders: list[SalesOrder] = []
        if credited is not None and sat.state == 'cancelled':
            orders = await _lock_credited(db, *credited)
        # Rule 11: the status as the row holds it now, by the id transaction 1 kept.
        status = (
            await db.execute(
                select(FiscalDocumentCancellation.status).where(
                    FiscalDocumentCancellation.fiscal_document_cancellation_id == request_id
                )
            )
        ).scalar_one()
        now = _now()
        values: dict[str, object] = {'last_checked_at': now}
        if status == kept:
            closed = None
            if sat.state == 'cancelled':
                closed = 'expired' if sat.cancellation == 'expired' else 'accepted'
                await _apply_cancelled(db, document, reason, substitution, orders, employee_id)
            elif sat.state == 'valid' and sat.cancellation == 'rejected':
                closed = 'rejected'
                _record(db, document_id, employee_id, 'Cancellation rejected')
            elif sat.state == 'valid' and sat.cancellation == 'none':
                # Rule 7: a cancel within the grace may still be waiting on the PAC.
                if kept != 'requested' or now - requested_at >= STAMP_LOOKUP_GRACE:
                    closed = 'failed'
                    _record(db, document_id, employee_id, 'Cancellation not received by SAT')
            elif sat.state == 'valid' and sat.cancellation == 'pending' and kept == 'requested':
                values.update(status='pending', answered_at=now)
                _record(db, document_id, employee_id, 'Cancellation pending the receiver')
            if closed is not None:
                values.update(status=closed, resolved_at=now)
                document.cancelling_since = None
        await db.execute(
            update(FiscalDocumentCancellation)
            .where(FiscalDocumentCancellation.fiscal_document_cancellation_id == request_id)
            .values(**values)
        )
        await db.commit()
    return document
