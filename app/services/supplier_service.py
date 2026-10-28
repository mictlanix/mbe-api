from collections.abc import Sequence

from sqlalchemy import delete, func, insert, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.enums import AccessRight, SystemObject
from app.models.core import Address, BankAccount, Contact
from app.models.supplier import (
    Supplier,
    SupplierAgreement,
    supplier_address,
    supplier_bank_account,
    supplier_contact,
)
from app.models.user import AccessPrivilege
from app.schemas.supplier import SupplierCreate, SupplierUpdate
from app.services.references import assert_not_referenced, assert_unique

# Each sub-panel answers to its own read privilege (spec 035 FR-001): the object that owns the
# rows, and for the agreements, Suppliers itself.
_PANELS = frozenset(
    {
        SystemObject.BANK_ACCOUNTS,
        SystemObject.CONTACTS,
        SystemObject.ADDRESSES,
        SystemObject.SUPPLIERS,
    }
)


async def readable_panels(db: AsyncSession, user: CurrentUser) -> frozenset[SystemObject]:
    """The sub-panels `user` may read, from one query. Administrators read all four, no query."""
    if user.administrator:
        return _PANELS
    rows = await db.execute(
        select(AccessPrivilege.system_object, AccessPrivilege.privileges).where(
            AccessPrivilege.user_id == user.user_id,
            AccessPrivilege.system_object.in_([int(p) for p in _PANELS]),
        )
    )
    return frozenset(SystemObject(obj) for obj, mask in rows.all() if mask & AccessRight.READ)


async def _get_links(
    db: AsyncSession, supplier_id: int, panels: frozenset[SystemObject] = _PANELS
) -> tuple[list[Address], list[Contact], list[BankAccount], list[SupplierAgreement]]:
    """A supplier's linked addresses, contacts and bank accounts, and its agreements (#13).

    `supplier_address`, `supplier_contact` and `supplier_bank_account` are real junction tables
    with real rows that nothing exposed, and `supplier_agreement` had no route at all. The
    agreements are read only here; they are written through their own routes.

    Attached to the detail response only, never to the list — a page of suppliers must not cost
    four queries per row. Only the panels in `panels` are queried; the rest come back empty.
    """
    addresses: Sequence[Address] = []
    contacts: Sequence[Contact] = []
    bank_accounts: Sequence[BankAccount] = []
    agreements: Sequence[SupplierAgreement] = []
    if SystemObject.ADDRESSES in panels:
        addresses = (
            (
                await db.execute(
                    select(Address)
                    .join(supplier_address, supplier_address.c['address'] == Address.address_id)
                    .where(supplier_address.c['supplier'] == supplier_id)
                    .order_by(Address.address_id)
                )
            )
            .scalars()
            .all()
        )
    if SystemObject.CONTACTS in panels:
        contacts = (
            (
                await db.execute(
                    select(Contact)
                    .join(supplier_contact, supplier_contact.c['contact'] == Contact.contact_id)
                    .where(supplier_contact.c['supplier'] == supplier_id)
                    .order_by(Contact.contact_id)
                )
            )
            .scalars()
            .all()
        )
    if SystemObject.BANK_ACCOUNTS in panels:
        bank_accounts = (
            (
                await db.execute(
                    select(BankAccount)
                    .join(
                        supplier_bank_account,
                        supplier_bank_account.c['bank_account'] == BankAccount.bank_account_id,
                    )
                    .where(supplier_bank_account.c['supplier'] == supplier_id)
                    .order_by(BankAccount.bank_account_id)
                )
            )
            .scalars()
            .all()
        )
    if SystemObject.SUPPLIERS in panels:
        agreements = (
            (
                await db.execute(
                    select(SupplierAgreement)
                    .where(SupplierAgreement.supplier == supplier_id)
                    .order_by(SupplierAgreement.start, SupplierAgreement.supplier_agreement_id)
                )
            )
            .scalars()
            .all()
        )
    return list(addresses), list(contacts), list(bank_accounts), list(agreements)


async def _attach_links(
    db: AsyncSession, supplier: Supplier, panels: frozenset[SystemObject]
) -> None:
    addresses, contacts, bank_accounts, agreements = await _get_links(
        db, supplier.supplier_id, panels
    )
    # Written under separate keys for consistency with the customer detail, though these four
    # shadow no mapped column.
    supplier.__dict__['addresses'] = addresses
    supplier.__dict__['contacts'] = contacts
    supplier.__dict__['bank_accounts'] = bank_accounts
    supplier.__dict__['agreements'] = agreements


async def _set_links(
    db: AsyncSession,
    supplier_id: int,
    *,
    addresses: list[int] | None,
    contacts: list[int] | None,
    bank_accounts: list[int] | None,
) -> None:
    """Replace-all, and only for a collection the caller actually sent.

    `None` means "leave the links alone", which is what makes an ordinary `PUT` that does not
    mention addresses safe. An empty list is a real instruction: unlink everything.
    """
    if addresses is not None:
        await db.execute(
            delete(supplier_address).where(supplier_address.c['supplier'] == supplier_id)
        )
        if addresses:
            await db.execute(
                insert(supplier_address),
                [{'supplier': supplier_id, 'address': a} for a in addresses],
            )
    if contacts is not None:
        await db.execute(
            delete(supplier_contact).where(supplier_contact.c['supplier'] == supplier_id)
        )
        if contacts:
            await db.execute(
                insert(supplier_contact),
                [{'supplier': supplier_id, 'contact': c} for c in contacts],
            )
    if bank_accounts is not None:
        await db.execute(
            delete(supplier_bank_account).where(supplier_bank_account.c['supplier'] == supplier_id)
        )
        if bank_accounts:
            await db.execute(
                insert(supplier_bank_account),
                [{'supplier': supplier_id, 'bank_account': b} for b in bank_accounts],
            )


def _sent_links(
    data: SupplierCreate | SupplierUpdate, panels: frozenset[SystemObject]
) -> dict[str, list[int] | None]:
    """The link lists the caller sent, with one for a panel they cannot read treated as not sent.

    Otherwise a caller could change links they are not allowed to see (spec 035 FR-003).
    """
    return {
        'addresses': data.addresses if SystemObject.ADDRESSES in panels else None,
        'contacts': data.contacts if SystemObject.CONTACTS in panels else None,
        'bank_accounts': data.bank_accounts if SystemObject.BANK_ACCOUNTS in panels else None,
    }


async def list_suppliers(
    db: AsyncSession,
    *,
    search: str | None = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[Sequence[Supplier], int]:
    base = select(Supplier)
    count_q = select(func.count()).select_from(Supplier)

    if search:
        term = f'%{search}%'
        condition = or_(
            Supplier.code.ilike(term),
            Supplier.name.ilike(term),
            Supplier.zone.ilike(term),
        )
        base = base.where(condition)
        count_q = count_q.where(condition)

    total: int = (await db.execute(count_q)).scalar_one()
    items = (await db.execute(base.offset(skip).limit(limit))).scalars().all()
    return items, total


async def get_supplier(db: AsyncSession, supplier_id: int) -> Supplier | None:
    return await db.get(Supplier, supplier_id)


async def get_supplier_detail(
    db: AsyncSession, supplier_id: int, user: CurrentUser
) -> Supplier | None:
    supplier = await db.get(Supplier, supplier_id)
    if supplier is not None:
        await _attach_links(db, supplier, await readable_panels(db, user))
    return supplier


async def create_supplier(db: AsyncSession, data: SupplierCreate, user: CurrentUser) -> Supplier:
    await assert_unique(db, Supplier, Supplier.code, data.code, label='Supplier code')
    supplier = Supplier(
        code=data.code,
        name=data.name,
        zone=data.zone,
        credit_limit=data.credit_limit,
        credit_days=data.credit_days,
        comment=data.comment,
    )
    db.add(supplier)
    await db.flush()  # get supplier_id before writing the junction rows
    panels = await readable_panels(db, user)
    await _set_links(db, supplier.supplier_id, **_sent_links(data, panels))
    await db.commit()
    await db.refresh(supplier)
    await _attach_links(db, supplier, panels)
    return supplier


async def update_supplier(
    db: AsyncSession, supplier: Supplier, data: SupplierUpdate, user: CurrentUser
) -> Supplier:
    if data.code is not None:
        await assert_unique(
            db,
            Supplier,
            Supplier.code,
            data.code,
            exclude_pk=supplier.supplier_id,
            label='Supplier code',
        )
        supplier.code = data.code
    if data.name is not None:
        supplier.name = data.name
    if data.zone is not None:
        supplier.zone = data.zone
    if data.credit_limit is not None:
        supplier.credit_limit = data.credit_limit
    if data.credit_days is not None:
        supplier.credit_days = data.credit_days
    if data.comment is not None:
        supplier.comment = data.comment
    panels = await readable_panels(db, user)
    await _set_links(db, supplier.supplier_id, **_sent_links(data, panels))
    await db.commit()
    await db.refresh(supplier)
    await _attach_links(db, supplier, panels)
    return supplier


async def delete_supplier(db: AsyncSession, supplier: Supplier) -> None:
    await assert_not_referenced(db, supplier)
    await db.delete(supplier)
    await db.commit()
