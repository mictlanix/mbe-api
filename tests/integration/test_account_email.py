"""Emailing an account statement: what is sent, refused and recorded (spec 033, US5)."""

import pytest
from fastapi import HTTPException
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.enums import SourceType
from app.models.core import Contact
from app.models.customer import customer_contact
from app.models.incidence import Incidence
from app.services import accounts_service
from app.services.account_email import email_statement
from app.services.mail import MailError
from tests.integration.test_accounts_receivable_flow import PAST, _customer, _order
from tests.integration.test_fiscal_email import RecordingMailer


async def _contact(db: AsyncSession, customer: int, email: str | None) -> int:
    contact = Contact(name='Cobranza', mobile='5555555555', email=email)
    db.add(contact)
    await db.flush()
    await db.execute(insert(customer_contact).values(customer=customer, contact=contact.contact_id))
    await db.commit()
    return contact.contact_id


async def _incidences(db: AsyncSession, customer: int) -> list[Incidence]:
    query = select(Incidence).where(
        Incidence.source == int(SourceType.CUSTOMER), Incidence.instance_id == customer
    )
    return list((await db.execute(query)).scalars())


@pytest.fixture
async def owing(db: AsyncSession, seeded: None) -> int:
    await _customer(db, 21, limit='50000')
    await _order(db, 21, '30000', due=PAST)
    return 21


async def _send(db: AsyncSession, current: CurrentUser, mailer: RecordingMailer, **body):  # noqa: ANN003, ANN202
    return await email_statement(
        db,
        accounts_service.CUSTOMERS,
        21,
        contact_ids=body.get('contacts', []),
        to=body.get('to', []),
        mailer=mailer,
        current=current,
    )


async def test_two_contacts_and_a_typed_address(
    db: AsyncSession, current_user: CurrentUser, owing: int
) -> None:
    """US5 scenario 2: one send, one incidence naming the three recipients."""
    first = await _contact(db, owing, 'ana@cliente.mx')
    second = await _contact(db, owing, 'luis@cliente.mx')
    mailer = RecordingMailer()

    sent_at, to = await _send(
        db, current_user, mailer, contacts=[first, second], to=['cobranza@cliente.mx']
    )

    assert to == ['ana@cliente.mx', 'luis@cliente.mx', 'cobranza@cliente.mx']
    [message] = mailer.sent
    assert list(message.to) == to
    assert message.subject == 'Matriz Estado de cuenta Cliente 21'
    assert '$30,000.00' in message.body
    [attachment] = message.attachments
    assert attachment.filename == 'estado-de-cuenta-21.pdf'
    assert attachment.data.startswith(b'%PDF')
    [incidence] = await _incidences(db, owing)
    assert incidence.comment == 'Account statement emailed'
    assert incidence.content == f'To: {", ".join(to)}'
    assert incidence.updater == current_user.employee_id
    assert sent_at is not None


async def test_a_contact_of_another_customer_is_refused(
    db: AsyncSession, current_user: CurrentUser, owing: int
) -> None:
    await _customer(db, 22, limit='0')
    stranger = await _contact(db, 22, 'otro@cliente.mx')
    mailer = RecordingMailer()

    with pytest.raises(HTTPException) as refusal:
        await _send(db, current_user, mailer, contacts=[stranger])

    assert refusal.value.status_code == 422
    assert mailer.sent == [] and await _incidences(db, owing) == []


async def test_a_contact_without_an_address_is_refused(
    db: AsyncSession, current_user: CurrentUser, owing: int
) -> None:
    silent = await _contact(db, owing, None)

    with pytest.raises(HTTPException) as refusal:
        await _send(db, current_user, RecordingMailer(), contacts=[silent])

    assert refusal.value.status_code == 422


async def test_a_refused_send_records_nothing(
    db: AsyncSession, current_user: CurrentUser, owing: int
) -> None:
    mailer = RecordingMailer(error=MailError('550 mailbox unavailable'))

    with pytest.raises(HTTPException) as refusal:
        await _send(db, current_user, mailer, to=['a@cliente.mx'])

    assert refusal.value.status_code == 502
    assert await _incidences(db, owing) == []


async def test_an_unknown_customer_is_404(
    db: AsyncSession, current_user: CurrentUser, seeded: None
) -> None:
    with pytest.raises(HTTPException) as refusal:
        await email_statement(
            db,
            accounts_service.CUSTOMERS,
            999,
            contact_ids=[],
            to=['a@x.mx'],
            mailer=RecordingMailer(),
            current=current_user,
        )

    assert refusal.value.status_code == 404
