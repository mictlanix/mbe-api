"""Emailing an account statement to a customer or a supplier (spec 033, FR-024).

Quotes' flow (spec 024): render, send, then record, so every recorded send happened. Recipients are
the party's own contacts, chosen by id, and addresses the user types, at most ten in all.
"""

from collections.abc import Sequence
from datetime import datetime

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import rendering
from app.core.clock import local_now
from app.core.deps import CurrentUser
from app.core.errors import not_found
from app.models.core import Contact
from app.models.customer import customer_contact
from app.models.supplier import supplier_contact
from app.services import accounts_service, incidences, mail, print_contexts
from app.services.sales_quote_service import _recipients_context

MAX_RECIPIENTS = 10


def _unprocessable(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail)


def merge_recipients(contact_addresses: Sequence[str], typed: Sequence[str]) -> list[str]:
    """Contacts first, then typed addresses, deduplicated case-insensitively keeping the first
    spelling; one to ten of them."""
    seen: set[str] = set()
    merged = []
    for address in [*contact_addresses, *typed]:
        if address.lower() not in seen:
            seen.add(address.lower())
            merged.append(address)
    if not merged:
        raise _unprocessable('Choose at least one contact or type an address')
    if len(merged) > MAX_RECIPIENTS:
        raise _unprocessable(f'At most {MAX_RECIPIENTS} recipients')
    return merged


async def recipients(
    db: AsyncSession,
    side: accounts_service.Side,
    party_id: int,
    contact_ids: Sequence[int],
    typed: Sequence[str],
) -> list[str]:
    """The chosen contacts' addresses and the typed ones. A contact must be the party's own and
    have an address; typed addresses arrive checked by the request schema."""
    addresses: list[str] = []
    if contact_ids:
        link = customer_contact if side is accounts_service.CUSTOMERS else supplier_contact
        party_column = link.c.customer if side is accounts_service.CUSTOMERS else link.c.supplier
        rows = dict(
            (
                await db.execute(
                    select(Contact.contact_id, Contact.email)
                    .join(link, link.c.contact == Contact.contact_id)
                    .where(party_column == party_id, Contact.contact_id.in_(list(contact_ids)))
                )
            ).all()
        )
        for contact_id in contact_ids:
            if contact_id not in rows:
                raise _unprocessable(f'Contact {contact_id} is not one of this party')
            email = (rows[contact_id] or '').strip()
            if not email:
                raise _unprocessable(f'Contact {contact_id} has no email address')
            addresses.append(email)
    return merge_recipients(addresses, typed)


def statement_message(
    *,
    company: str,
    party: str,
    party_id: int,
    owed: str,
    overdue: str,
    to: Sequence[str],
    pdf: bytes,
) -> mail.Message:
    return mail.Message(
        to=list(to),
        subject=f'{company} Estado de cuenta {party}',
        body=(
            f'Le enviamos su estado de cuenta.\n'
            f'Adeudo: {owed}. Vencido: {overdue}.\n'
            'El detalle va en el PDF adjunto.'
        ),
        attachments=[mail.Attachment(f'estado-de-cuenta-{party_id}.pdf', 'application/pdf', pdf)],
    )


async def email_statement(
    db: AsyncSession,
    side: accounts_service.Side,
    party_id: int,
    *,
    contact_ids: Sequence[int],
    to: Sequence[str],
    mailer: mail.Mailer,
    current: CurrentUser,
) -> tuple[datetime, list[str]]:
    """Send the party's statement and record one incidence on the party; returns when and to whom.

    The record is written only after the server accepted the message, as `email_quote` does: a
    failed send records nothing (502).
    """
    if party_id not in await side.parties(db, [party_id]):
        raise not_found(side.party_label.lower(), party_id)
    addresses = await recipients(db, side, party_id, contact_ids, to)
    template, context = await print_contexts.account_statement_context(
        db, side, party_id, facility=current.facility_id
    )
    pdf = await rendering.render_pdf(template, context)
    message = statement_message(
        company=context['header']['name'],
        party=context['party'],
        party_id=party_id,
        owed=context['owed'],
        overdue=context['overdue'],
        to=addresses,
        pdf=pdf,
    )

    try:
        await mailer.send(message)
    except mail.MailError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f'The email could not be sent: {exc}',
        ) from exc

    sent_at = local_now()
    incidences.record(
        db,
        source=side.source,
        instance_id=party_id,
        updater=current.employee_id,
        reason='Account statement emailed',
        context=_recipients_context(addresses),
    )
    await db.commit()
    return sent_at, addresses
