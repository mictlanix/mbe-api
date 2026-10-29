"""Emailing a stamped CFDI: what is sent, what is refused, what is recorded (spec 028, US4)."""

from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.enums import SourceType
from app.models.incidence import Incidence
from app.services import cfdi, fiscal_document_service
from app.services.fiscal_email import email_document
from app.services.mail import MailError
from tests.integration.seed import CFDI_FIXTURES, seed_fiscal_document

_TO = ['ana@cliente.mx', 'b@cliente.mx']


class RecordingMailer:
    """Keeps messages instead of sending them, or raises the error it was given."""

    def __init__(self, error: Exception | None = None) -> None:
        self.sent: list = []
        self.error = error

    async def send(self, message) -> None:  # noqa: ANN001
        if self.error is not None:
            raise self.error
        self.sent.append(message)


async def _email(db: AsyncSession, document_id: int, mailer: RecordingMailer, current: CurrentUser):  # noqa: ANN202
    document = await fiscal_document_service.get_document(db, document_id)
    return await email_document(db, document, _TO, mailer=mailer, current=current)


async def _incidences(db: AsyncSession, document_id: int) -> list[Incidence]:
    query = select(Incidence).where(
        Incidence.source == int(SourceType.FISCAL_DOCUMENT), Incidence.instance_id == document_id
    )
    return list((await db.execute(query)).scalars())


@pytest.mark.usefixtures('seeded')
async def test_a_stamped_invoice_is_sent_and_recorded(
    db: AsyncSession, current_user: CurrentUser
) -> None:
    document_id = await seed_fiscal_document(db, 'invoice')
    xml = (CFDI_FIXTURES / 'invoice.xml').read_text(encoding='utf-8')
    doc = cfdi.parse(xml)
    mailer = RecordingMailer()

    sent_at = await _email(db, document_id, mailer, current_user)

    [message] = mailer.sent
    assert message.to == _TO
    assert message.subject == f'Comprobante Fiscal - {doc.emisor["Rfc"]} - RH011405'
    assert doc.timbre['UUID'] in message.body
    assert 'Total: $3,160.00' in message.body
    assert cfdi.sat_qr_payload(doc) in message.body
    pdf, attached_xml = message.attachments
    assert pdf.filename == f'{doc.emisor["Rfc"]}-RH011405.pdf'
    assert pdf.data.startswith(b'%PDF')
    assert attached_xml.filename == f'{doc.emisor["Rfc"]}-RH011405.xml'
    assert attached_xml.data == xml.encode('utf-8')

    [incidence] = await _incidences(db, document_id)
    assert incidence.source == 13
    assert incidence.updater == current_user.employee_id
    assert incidence.comment == 'CFDI emailed'
    assert incidence.content == 'To: ana@cliente.mx, b@cliente.mx'
    assert sent_at is not None


@pytest.mark.usefixtures('seeded')
@pytest.mark.parametrize(
    'state',
    [
        dict(completed=False),
        dict(cancelled=True),
        dict(stamp=False),
        dict(version=Decimal('3.3')),
        dict(xml=None),
    ],
    ids=['draft', 'cancelled', 'sealed-not-stamped', 'cfdi-3.3', 'no-xml'],
)
async def test_only_a_stamped_cfdi_40_can_be_emailed(
    db: AsyncSession, current_user: CurrentUser, state: dict
) -> None:
    document_id = await seed_fiscal_document(db, 'invoice', **state)
    mailer = RecordingMailer()

    with pytest.raises(HTTPException) as raised:
        await _email(db, document_id, mailer, current_user)

    assert raised.value.status_code == 409
    assert raised.value.code == 'fiscal_document.not_emailable'
    assert raised.value.params == {}
    assert raised.value.detail == 'Only a stamped CFDI 4.0 can be emailed'
    assert mailer.sent == []
    assert await _incidences(db, document_id) == []


@pytest.mark.usefixtures('seeded')
async def test_a_refused_send_is_reported_and_not_recorded(
    db: AsyncSession, current_user: CurrentUser
) -> None:
    document_id = await seed_fiscal_document(db, 'invoice')
    mailer = RecordingMailer(error=MailError('550 mailbox unavailable'))

    with pytest.raises(HTTPException) as raised:
        await _email(db, document_id, mailer, current_user)

    assert raised.value.status_code == 502
    assert raised.value.code == 'email.send_failed'
    assert raised.value.params == {}
    assert raised.value.detail == 'The email could not be sent: 550 mailbox unavailable'
    assert await _incidences(db, document_id) == []


@pytest.mark.usefixtures('seeded')
async def test_a_stamped_document_with_unreadable_xml_cannot_be_emailed(
    db: AsyncSession, current_user: CurrentUser
) -> None:
    document_id = await seed_fiscal_document(db, 'invoice', xml='<not-a-cfdi')
    mailer = RecordingMailer()

    with pytest.raises(HTTPException) as raised:
        await _email(db, document_id, mailer, current_user)

    assert raised.value.status_code == 409
    assert raised.value.code == 'fiscal_document.xml_unavailable'
    assert raised.value.params == {}
    assert mailer.sent == []
