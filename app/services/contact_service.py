"""Contacts — the named person a document is addressed to.

`contact` has been a real table with real rows all along, and `sales_order.contact` and
`delivery_order.contact` have been accepting ids from it, but nothing produced one: a client was
asked for an id it had no way to obtain or create (#133). This is the missing half.

There is no `status` column on `contact`, so unlike the other catalogs there is nothing to filter
on but the search term.
"""

from collections.abc import Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.core import Contact
from app.schemas.core import ContactCreate, ContactUpdate
from app.services.references import assert_not_referenced


async def list_contacts(
    db: AsyncSession,
    *,
    search: str | None = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[Sequence[Contact], int]:
    base = select(Contact)
    count_q = select(func.count()).select_from(Contact)

    if search:
        term = f'%{search}%'
        condition = or_(
            Contact.name.ilike(term),
            Contact.job_title.ilike(term),
            Contact.phone.ilike(term),
            Contact.mobile.ilike(term),
            Contact.email.ilike(term),
        )
        base = base.where(condition)
        count_q = count_q.where(condition)

    total: int = (await db.execute(count_q)).scalar_one()
    items = (await db.execute(base.offset(skip).limit(limit))).scalars().all()
    return items, total


async def get_contact(db: AsyncSession, contact_id: int) -> Contact | None:
    return await db.get(Contact, contact_id)


async def create_contact(db: AsyncSession, data: ContactCreate) -> Contact:
    contact = Contact(
        name=data.name,
        job_title=data.job_title,
        phone=data.phone,
        phone_ext=data.phone_ext,
        mobile=data.mobile,
        fax=data.fax,
        website=data.website,
        email=data.email,
        im=data.im,
        sip=data.sip,
        birthday=data.birthday,
        comment=data.comment,
    )
    db.add(contact)
    await db.commit()
    await db.refresh(contact)
    return contact


async def update_contact(db: AsyncSession, contact: Contact, data: ContactUpdate) -> Contact:
    if data.name is not None:
        contact.name = data.name
    if data.job_title is not None:
        contact.job_title = data.job_title
    if data.phone is not None:
        contact.phone = data.phone
    if data.phone_ext is not None:
        contact.phone_ext = data.phone_ext
    if data.mobile is not None:
        contact.mobile = data.mobile
    if data.fax is not None:
        contact.fax = data.fax
    if data.website is not None:
        contact.website = data.website
    if data.email is not None:
        contact.email = data.email
    if data.im is not None:
        contact.im = data.im
    if data.sip is not None:
        contact.sip = data.sip
    if data.birthday is not None:
        contact.birthday = data.birthday
    if data.comment is not None:
        contact.comment = data.comment
    await db.commit()
    await db.refresh(contact)
    return contact


async def delete_contact(db: AsyncSession, contact: Contact) -> None:
    await assert_not_referenced(db, contact)
    await db.delete(contact)
    await db.commit()
