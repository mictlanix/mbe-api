"""Stamping a sealed CFDI through the PAC provider (spec 039, research R3 and R15).

Each step is its own transaction, and no lock is held while the PAC answers:

- transaction A locks the document, checks it is sealed and commits `stamping_since`, the durable
  record that a stamp is with the PAC (FR-011);
- the PAC call runs with no transaction open;
- transaction B locks the document again and stores the stamp (`_store_stamp`).

A second request finds the marker on the locked row and never reaches the PAC (FR-012). Neither
the XML nor the PAC's raw errors are logged or returned; `pac.rejected` carries only the PAC's code
and message.
"""

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import local_now
from app.core.error_codes import ErrorCode
from app.core.errors import DomainError
from app.enums import SourceType
from app.models.fiscal import FiscalDocument, FiscalDocumentXml
from app.services import fiscal_document_service as fds
from app.services import incidences
from app.services.inventory_documents import busy_as_conflict
from app.services.invoice_service import issue_date_ok
from app.services.pac.provider import PacProvider, PacRejected, PacTimeout, PacUnavailable, Stamp

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
