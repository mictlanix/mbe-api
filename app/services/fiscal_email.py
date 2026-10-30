"""Emailing a stamped CFDI's PDF and XML (spec 028, US4, FR-013, research R6).

Spec 024's quote email, applied to a fiscal document: the PDF is spec 020's, the XML is the stored
one, and the send is recorded only after the mail server accepted it.
"""

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app import rendering
from app.core.clock import local_now
from app.core.deps import CurrentUser
from app.core.error_codes import ErrorCode
from app.core.errors import DomainError
from app.enums import SourceType
from app.models.fiscal import FiscalDocument
from app.rendering import formatting
from app.services import cfdi, fiscal_document_service, incidences, mail, print_contexts
from app.services.sales_quote_service import _recipients_context

_CFDI_40 = Decimal('4.0')


def fiscal_email_message(
    *,
    issuer: str,
    recipient: str,
    batch: str | None,
    serial: int | None,
    total: Decimal,
    uuid: str,
    qr_url: str,
    to: Sequence[str],
    pdf: bytes,
    xml: str,
) -> mail.Message:
    """The current system's subject and body (`FiscalDocumentEmail*FormatString`), with the PDF
    and the XML named `{RFC}-{Batch}{Serial:D6}` as the downloads are."""
    folio = f'{batch or ""}{serial or 0:06d}'
    name = f'{issuer}-{folio}'
    return mail.Message(
        to=list(to),
        subject=f'Comprobante Fiscal - {issuer} - {folio}',
        body=(
            'Has recibido el siguiente comprobante fiscal digital por internet (CFDI).\n'
            '\n'
            f'Folio Fiscal: {uuid}\n'
            f'RFC Emisor: {issuer}\n'
            f'RFC Receptor: {recipient}\n'
            f'Total: {formatting.money(total)}\n'
            '\n'
            'Puedes hacer la validación del mismo en el sitio oficial del SAT.\n'
            f'{qr_url}\n'
            '\n'
            'Adjunto, podrás encontrar tu comprobante en los formatos PDF y XML.\n'
            '\n'
            'Gracias por utilizar nuestros servicios.'
        ),
        attachments=[
            mail.Attachment(f'{name}.pdf', 'application/pdf', pdf),
            mail.Attachment(f'{name}.xml', 'application/xml', xml.encode('utf-8')),
        ],
    )


async def email_document(
    db: AsyncSession,
    document: FiscalDocument,
    to: Sequence[str],
    *,
    mailer: mail.Mailer,
    current: CurrentUser,
) -> datetime:
    """Send a stamped CFDI 4.0's PDF and XML and record the send; returns when it was sent.

    `document` comes from `fiscal_document_service.get_document`. As in spec 024, the record is
    written only after the server accepted the message, so every recorded send happened.
    """
    stamped = (
        document.completed
        and not document.cancelled
        and document.version == _CFDI_40
        and document.stamp_uuid
    )
    xml = None
    if stamped:
        xml = await fiscal_document_service.get_xml(db, document.fiscal_document_id)
    if xml is None:
        raise DomainError(
            ErrorCode.FISCAL_DOCUMENT_NOT_EMAILABLE, 'Only a stamped CFDI 4.0 can be emailed'
        )

    try:
        doc = cfdi.parse(xml)
        template, context = await print_contexts.fiscal_document_context(db, document, xml)
    except cfdi.CfdiError:
        raise DomainError(
            ErrorCode.FISCAL_DOCUMENT_XML_UNAVAILABLE, 'Stamped XML is missing or unreadable'
        ) from None
    pdf = await rendering.render_pdf(template, context)
    message = fiscal_email_message(
        issuer=document.issuer,
        recipient=doc.receptor.get('Rfc', document.recipient),
        batch=document.batch,
        serial=document.serial,
        total=Decimal(doc.comprobante['Total']),
        uuid=doc.timbre['UUID'],
        qr_url=cfdi.sat_qr_payload(doc),
        to=to,
        pdf=pdf,
        xml=xml,
    )

    try:
        await mailer.send(message)
    except mail.MailError as exc:
        raise DomainError(
            ErrorCode.EMAIL_SEND_FAILED, f'The email could not be sent: {exc}'
        ) from exc

    sent_at = local_now()
    incidences.record(
        db,
        source=SourceType.FISCAL_DOCUMENT,
        instance_id=document.fiscal_document_id,
        updater=current.employee_id,
        reason='CFDI emailed',
        context=_recipients_context(to),
    )
    await db.commit()
    return sent_at
