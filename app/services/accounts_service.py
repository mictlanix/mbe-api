"""Accounts receivable and accounts payable: one design seen from two sides (spec 033).

A customer's credit orders and a supplier's purchase orders are the same thing to these views: open
documents with a balance and a due date. The aging, the party's figures and the statement are
written once here, and each side supplies its own documents, credit and party data.
"""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.clock import local_now
from app.enums import CurrencyCode, FiscalDocumentType, SourceType
from app.models.core import Employee, Facility
from app.models.customer import Customer
from app.models.fiscal import FiscalDocument, FiscalDocumentDetail
from app.models.sales import SalesOrderDetail
from app.models.supplier import Supplier
from app.services import (
    customer_payment_service,
    fiscal_document_service,
    sales_order_service,
    supplier_payment_service,
    totals,
)

BUCKETS = ('current', '1-30', '31-60', '61-90', '90+')
_RATIO = Decimal('0.0001')


@dataclass(frozen=True)
class Document:
    """An open document on either side, in the shape the drill-down shows (data-model.md)."""

    id: int
    party: int
    serial: int | None
    date: datetime
    due_date: datetime | None
    facility: int | None
    facility_code: str | None
    salesperson: int | None
    #: `{'id': …, 'label': …}` for each invoice or receipt.
    linked: tuple[dict[str, Any], ...]
    currency: CurrencyCode
    exchange_rate: Decimal
    total: Decimal
    deductions: Decimal
    applied: Decimal
    balance: Decimal


# ── Aging and figures (pure) ──────────────────────────────────────────────────


def days_past_due(due: datetime | None, today: date) -> int:
    """Local days since the due date; 0 with no due date or one of today or later."""
    if due is None:
        return 0
    return max((today - due.date()).days, 0)


def bucket(days: int) -> str:
    if days <= 0:
        return 'current'
    if days <= 30:
        return '1-30'
    if days <= 60:
        return '31-60'
    if days <= 90:
        return '61-90'
    return '90+'


def po_due_date(first_receipt: datetime | None, credit_days: int | None) -> datetime | None:
    """A PO is due its first confirmed receipt plus the supplier's credit days; with no receipt
    it isn't due yet (FR-006, research R6)."""
    if first_receipt is None:
        return None
    return first_receipt + timedelta(days=credit_days or 0)


def summarize(
    documents: Sequence[Document],
    *,
    today: date,
    credit: Decimal,
    credit_limit: Decimal | None,
    owed_company: Decimal,
) -> dict[str, Any]:
    """A party's figures from its open documents in scope (FR-014, FR-015).

    Owed is in base currency at each document's rate, as the credit check sums it. Each document
    lands in exactly one bucket, so the buckets add up to owed. Utilization and available credit
    read `owed_company`, what the party owes across every facility, so a user scoped to one
    facility sees the same limit picture as everyone else; with no limit they stay blank.
    """
    buckets = dict.fromkeys(BUCKETS, Decimal(0))
    for document in documents:
        base = document.balance * document.exchange_rate
        buckets[bucket(days_past_due(document.due_date, today))] += base
    buckets = {name: value.quantize(totals.CENTS) for name, value in buckets.items()}
    owed = sum(buckets.values(), Decimal(0))
    limited = credit_limit is not None and credit_limit > 0
    return {
        'owed': owed,
        'overdue': owed - buckets['current'],
        'buckets': buckets,
        'oldest': min((document.date for document in documents), default=None),
        'credit': credit,
        'net': owed - credit,
        'owed_company': owed_company,
        'utilization': (owed_company / credit_limit).quantize(_RATIO) if limited else None,
        'available_credit': credit_limit - owed_company if limited else None,
    }


# ── Sides ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Party:
    id: int
    name: str
    credit_limit: Decimal | None
    credit_days: int | None
    sales_agent: int | None = None
    sales_agent_name: str | None = None


@dataclass(frozen=True)
class Side:
    """What one side supplies to the shared views: its open documents in a facility scope (None for
    all), its parties' credit, and its parties."""

    party_label: str
    source: SourceType
    documents: Callable[..., Awaitable[list[Document]]]
    credit: Callable[[AsyncSession, Sequence[int]], Awaitable[dict[int, Decimal]]]
    parties: Callable[[AsyncSession, Sequence[int]], Awaitable[dict[int, Party]]]


async def _facility_codes(db: AsyncSession, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    rows = await db.execute(
        select(Facility.facility_id, Facility.code).where(Facility.facility_id.in_(ids))
    )
    return dict(rows.all())


async def _invoices_by_order(db: AsyncSession, order_ids: Sequence[int]) -> dict[int, list[dict]]:
    """The live invoices carrying each order's lines, as batch and serial (FR-017)."""
    if not order_ids:
        return {}
    invoice = aliased(FiscalDocument)
    rows = await db.execute(
        select(
            SalesOrderDetail.sales_order, invoice.fiscal_document_id, invoice.batch, invoice.serial
        )
        .join(
            FiscalDocumentDetail,
            FiscalDocumentDetail.order_detail == SalesOrderDetail.sales_order_detail_id,
        )
        .join(invoice, invoice.fiscal_document_id == FiscalDocumentDetail.document)
        .where(
            SalesOrderDetail.sales_order.in_(order_ids),
            invoice.type == FiscalDocumentType.INVOICE,
            fiscal_document_service.live(invoice),
        )
        .distinct()
        .order_by(invoice.fiscal_document_id)
    )
    linked: dict[int, list[dict]] = {}
    for order_id, invoice_id, batch, serial in rows.all():
        label = f'{batch or ""}{serial if serial is not None else ""}'
        linked.setdefault(order_id, []).append({'id': invoice_id, 'label': label})
    return linked


async def _customer_documents(
    db: AsyncSession, *, facility: int | None, party: int | None = None
) -> list[Document]:
    """Open credit orders (FR-007), from the one selector the credit check also reads."""
    orders = await sales_order_service.open_credit_orders(db, customer=party, facility=facility)
    linked = await _invoices_by_order(db, [order.sales_order_id for order, _ in orders])
    codes = await _facility_codes(db, {order.facility for order, _ in orders})
    return [
        Document(
            id=order.sales_order_id,
            party=order.customer,
            serial=order.serial,
            date=order.date,
            due_date=order.due_date,
            facility=order.facility,
            facility_code=codes.get(order.facility),
            salesperson=order.salesperson,
            linked=tuple(linked.get(order.sales_order_id, [])),
            currency=CurrencyCode(order.currency),
            exchange_rate=order.exchange_rate,
            total=balance.total,
            deductions=balance.refunded + balance.credited,
            applied=balance.applied,
            balance=balance.balance,
        )
        for order, balance in orders
    ]


async def _customer_credit(db: AsyncSession, customer_ids: Sequence[int]) -> dict[int, Decimal]:
    """Each customer's payments' amounts left to apply, change counted as used (FR-005a)."""
    credit: dict[int, Decimal] = {}
    for payment in await customer_payment_service.payments_with_credit(db, customers=customer_ids):
        credit[payment.customer] = credit.get(payment.customer, Decimal(0)) + payment.unapplied
    return {customer: amount.quantize(totals.CENTS) for customer, amount in credit.items()}


async def _customer_parties(db: AsyncSession, customer_ids: Sequence[int]) -> dict[int, Party]:
    if not customer_ids:
        return {}
    rows = await db.execute(
        select(Customer, Employee.first_name, Employee.last_name)
        .outerjoin(Employee, Employee.employee_id == Customer.salesperson)
        .where(Customer.customer_id.in_(list(customer_ids)))
    )
    return {
        customer.customer_id: Party(
            id=customer.customer_id,
            name=customer.name,
            credit_limit=customer.credit_limit,
            credit_days=customer.credit_days,
            sales_agent=customer.salesperson,
            sales_agent_name=f'{first} {last}'.strip() if first is not None else None,
        )
        for customer, first, last in rows.all()
    }


CUSTOMERS = Side(
    party_label='Customer',
    source=SourceType.CUSTOMER,
    documents=_customer_documents,
    credit=_customer_credit,
    parties=_customer_parties,
)


async def _supplier_documents(
    db: AsyncSession, *, facility: int | None, party: int | None = None
) -> list[Document]:
    """Open POs (FR-008). POs have no facility, so the supplier side is company-wide (FR-019)."""
    rows = await supplier_payment_service.open_purchase_orders(db, supplier=party)
    suppliers = {row['order'].supplier for row in rows}
    credit_days: dict[int, int] = {}
    if suppliers:
        query = select(Supplier.supplier_id, Supplier.credit_days).where(
            Supplier.supplier_id.in_(suppliers)
        )
        credit_days = dict((await db.execute(query)).all())
    return [
        Document(
            id=row['order'].purchase_order_id,
            party=row['order'].supplier,
            serial=None,
            date=row['order'].modification_time,
            due_date=po_due_date(row['first_receipt'], credit_days.get(row['order'].supplier)),
            facility=None,
            facility_code=None,
            salesperson=None,
            linked=tuple({'id': r['id'], 'label': r['label']} for r in row['receipts']),
            currency=CurrencyCode.MXN,
            exchange_rate=Decimal(1),
            total=row['total'],
            # Supplier returns aren't modelled; they'd come off here, as refunds do on orders.
            deductions=Decimal('0.00'),
            applied=row['applied'],
            balance=row['balance'],
        )
        for row in rows
    ]


async def _supplier_credit(db: AsyncSession, supplier_ids: Sequence[int]) -> dict[int, Decimal]:
    credit = await supplier_payment_service.credit_by_supplier(db, supplier_ids)
    return {supplier: amount.quantize(totals.CENTS) for supplier, amount in credit.items()}


async def _supplier_parties(db: AsyncSession, supplier_ids: Sequence[int]) -> dict[int, Party]:
    if not supplier_ids:
        return {}
    rows = await db.execute(select(Supplier).where(Supplier.supplier_id.in_(list(supplier_ids))))
    return {
        supplier.supplier_id: Party(
            id=supplier.supplier_id,
            name=supplier.name,
            credit_limit=supplier.credit_limit,
            credit_days=supplier.credit_days,
        )
        for supplier in rows.scalars()
    }


SUPPLIERS = Side(
    party_label='Supplier',
    source=SourceType.SUPPLIER,
    documents=_supplier_documents,
    credit=_supplier_credit,
    parties=_supplier_parties,
)


# ── Views ─────────────────────────────────────────────────────────────────────


def _owed_by_party(documents: Sequence[Document]) -> dict[int, Decimal]:
    owed: dict[int, Decimal] = {}
    for document in documents:
        owed[document.party] = owed.get(document.party, Decimal(0)) + (
            document.balance * document.exchange_rate
        )
    return {party: amount.quantize(totals.CENTS) for party, amount in owed.items()}


def _party_row(party: Party, figures: dict[str, Any]) -> dict[str, Any]:
    return {
        'party': party.id,
        'name': party.name,
        'sales_agent': party.sales_agent,
        'sales_agent_name': party.sales_agent_name,
        'credit_limit': party.credit_limit,
        'credit_days': party.credit_days,
        **figures,
    }


async def _rows(
    db: AsyncSession,
    side: Side,
    documents: Sequence[Document],
    *,
    scoped: bool,
    parties: Sequence[int] = (),
) -> dict[int, dict[str, Any]]:
    """Each party's summary row from its documents in scope; `parties` get a row even with none.

    Utilization reads what the party owes across every facility (FR-015), so a scoped view loads the
    parties' documents once more without the facility filter.
    """
    by_party: dict[int, list[Document]] = {party: [] for party in parties}
    for document in documents:
        by_party.setdefault(document.party, []).append(document)
    ids = list(by_party)
    owed_company = _owed_by_party(documents)
    if scoped and ids:
        everywhere = await side.documents(
            db, facility=None, party=ids[0] if len(ids) == 1 else None
        )
        owed_company = _owed_by_party(everywhere)
    found = await side.parties(db, ids)
    credit = await side.credit(db, ids)
    today = local_now().date()
    return {
        party_id: _party_row(
            found[party_id],
            summarize(
                party_documents,
                today=today,
                credit=credit.get(party_id, Decimal(0)),
                credit_limit=found[party_id].credit_limit,
                owed_company=owed_company.get(party_id, Decimal(0)),
            ),
        )
        for party_id, party_documents in by_party.items()
        if party_id in found
    }


_SORTS: dict[str, tuple[Callable[[dict], Any], bool]] = {
    'owed': (lambda row: row['owed'], True),
    'overdue': (lambda row: row['overdue'], True),
    'oldest': (lambda row: row['oldest'] or datetime.max, False),
}


async def summary(
    db: AsyncSession,
    side: Side,
    *,
    facility: int | None,
    party: int | None = None,
    overdue: bool = False,
    sales_agent: int | None = None,
    sort: str = 'owed',
    skip: int = 0,
    limit: int = 50,
) -> dict[str, Any]:
    """One row per party with an open document in scope (FR-014, FR-016), and the column totals."""
    documents = await side.documents(db, facility=facility, party=party)
    rows = list((await _rows(db, side, documents, scoped=facility is not None)).values())
    if overdue:
        rows = [row for row in rows if row['overdue'] > 0]
    if sales_agent is not None:
        rows = [row for row in rows if row['sales_agent'] == sales_agent]
    key, descending = _SORTS[sort]
    rows.sort(key=lambda row: row['party'])
    rows.sort(key=key, reverse=descending)
    buckets = dict.fromkeys(BUCKETS, Decimal(0))
    for row in rows:
        for name, value in row['buckets'].items():
            buckets[name] += value
    return {
        'items': rows[skip : skip + limit],
        'total': len(rows),
        'totals': {
            'owed': sum((row['owed'] for row in rows), Decimal(0)),
            'overdue': sum((row['overdue'] for row in rows), Decimal(0)),
            'buckets': buckets,
        },
    }


def _document_row(document: Document, today: date) -> dict[str, Any]:
    days = days_past_due(document.due_date, today)
    return {
        'id': document.id,
        'serial': document.serial,
        'date': document.date,
        'due_date': document.due_date,
        'days_past_due': days,
        'bucket': bucket(days),
        'facility': document.facility,
        'facility_code': document.facility_code,
        'salesperson': document.salesperson,
        'linked': list(document.linked),
        'currency': document.currency,
        'exchange_rate': document.exchange_rate,
        'total': document.total,
        'deductions': document.deductions,
        'applied': document.applied,
        'balance': document.balance,
    }


async def detail(
    db: AsyncSession, side: Side, party_id: int, *, facility: int | None
) -> dict[str, Any]:
    """A party's summary and open documents in scope, oldest due first, those without a due date
    last (FR-017). 404 when the party doesn't exist; a zero summary when nothing is open."""
    documents = await side.documents(db, facility=facility, party=party_id)
    rows = await _rows(db, side, documents, scoped=facility is not None, parties=[party_id])
    if party_id not in rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f'{side.party_label} not found'
        )
    today = local_now().date()
    documents = sorted(
        documents, key=lambda d: (d.due_date is None, d.due_date or datetime.min, d.id)
    )
    return {
        'summary': rows[party_id],
        'documents': [_document_row(document, today) for document in documents],
    }
