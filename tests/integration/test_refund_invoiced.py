"""FR-011 — a refund may not return units that sit on a live invoice (spec 028, US3).

A live invoice is type 0, not cancelled, and either completed (stamped) or sealed (an XML row).
Drafts reserve nothing and a cancelled invoice gives its units back.
"""

from datetime import datetime
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.enums import CurrencyCode
from app.models.core import CashSession
from app.models.fiscal import FiscalDocument, FiscalDocumentDetail, FiscalDocumentXml
from app.models.sales import CustomerRefund, SalesOrderDetail
from app.schemas.customer_refund import CustomerRefundLineUpdate, RefundPayout
from app.services import customer_refund_service as svc
from tests.integration.seed import ISSUER_RFC, RFC, seed_sales_order

BLOCKED = 'Units of line P1 are on invoice A42; cancel the invoice to refund them'


async def _order_line(db: AsyncSession) -> SalesOrderDetail:
    """A paid order with one line of 10 units of P1."""
    order_id = await seed_sales_order(db, completed=True, paid=True)
    return (
        await db.execute(select(SalesOrderDetail).where(SalesOrderDetail.sales_order == order_id))
    ).scalar_one()


async def _invoice(
    db: AsyncSession,
    line: SalesOrderDetail,
    quantity: str,
    *,
    completed: bool = True,
    sealed: bool = False,
    cancelled: bool = False,
    serial: int = 42,
) -> int:
    when = datetime(2026, 8, 2, 10, 0)
    document = FiscalDocument(
        creation_time=when,
        modification_time=when,
        creator=1,
        updater=1,
        issuer=ISSUER_RFC,
        customer=1,
        recipient=RFC,
        type=0,
        facility=1,
        batch='A',
        serial=serial,
        issued=when,
        issued_location='06000',
        completed=completed,
        cancelled=cancelled,
        payment_method=99,
        exchange_rate=Decimal('1'),
        currency=CurrencyCode.MXN,
        payment_terms=0,
        version=Decimal('4.0'),
        provider=0,
        retention_rate=Decimal('0'),
        local_retention_rate=Decimal('0'),
        stamp_uuid='A1B2C3D4-0000-4000-8000-000000000001' if completed else None,
    )
    db.add(document)
    await db.flush()
    db.add(
        FiscalDocumentDetail(
            document=document.fiscal_document_id,
            product=line.product,
            order_detail=line.sales_order_detail_id,
            product_code=line.product_code,
            product_name=line.product_name,
            quantity=Decimal(quantity),
            price=line.price,
            discount=Decimal('0'),
            tax_rate=line.tax_rate,
            exchange_rate=Decimal('1'),
            currency=CurrencyCode.MXN,
            tax_included=False,
        )
    )
    if sealed:
        db.add(FiscalDocumentXml(fiscal_document_xml_id=document.fiscal_document_id, data='<x/>'))
    await db.commit()
    return document.fiscal_document_id


async def _open(db: AsyncSession, line: SalesOrderDetail, current: CurrentUser) -> CustomerRefund:
    return await svc.open_refund(db, line.sales_order, current=current)


async def _set(
    db: AsyncSession, refund: CustomerRefund, quantity: str, current: CurrentUser
) -> CustomerRefund:
    return await svc.update_line(
        db,
        refund,
        refund.lines[0],
        CustomerRefundLineUpdate(quantity=Decimal(quantity)),
        current=current,
    )


async def _open_till(db: AsyncSession) -> None:
    db.add(CashSession(start=datetime(2026, 8, 3, 9, 0), cashier=1, cash_drawer=1))
    await db.commit()


async def test_a_stamped_invoice_excludes_its_units(
    db: AsyncSession, current_user: CurrentUser
) -> None:
    line = await _order_line(db)
    await _invoice(db, line, '6')

    assert await svc.line_refundable(db, line) == Decimal('4')
    refund = await _open(db, line, current_user)
    assert refund.lines[0].refundable_quantity == Decimal('4')


async def test_a_sealed_invoice_also_blocks(db: AsyncSession, current_user: CurrentUser) -> None:
    line = await _order_line(db)
    await _invoice(db, line, '6', completed=False, sealed=True)

    assert await svc.line_refundable(db, line) == Decimal('4')


@pytest.mark.parametrize(
    'state', [{'completed': False}, {'cancelled': True}], ids=['draft', 'cancelled']
)
async def test_draft_and_cancelled_invoices_do_not_block(
    db: AsyncSession, current_user: CurrentUser, state: dict
) -> None:
    line = await _order_line(db)
    await _invoice(db, line, '6', **state)

    assert await svc.line_refundable(db, line) == Decimal('10')
    refund = await _set(db, await _open(db, line, current_user), '10', current_user)
    assert refund.lines[0].quantity == Decimal('10')


async def test_editing_above_the_uninvoiced_remainder_is_409_naming_the_invoice(
    db: AsyncSession, current_user: CurrentUser
) -> None:
    line = await _order_line(db)
    refund = await _open(db, line, current_user)
    await _invoice(db, line, '6')
    # A later, larger invoice does not change which one is named.
    await _invoice(db, line, '1', serial=43)

    with pytest.raises(HTTPException) as exc:
        await _set(db, refund, '4', current_user)

    assert exc.value.status_code == 409
    assert exc.value.detail == BLOCKED


async def test_more_than_was_sold_stays_422(db: AsyncSession, current_user: CurrentUser) -> None:
    line = await _order_line(db)
    refund = await _open(db, line, current_user)
    await _invoice(db, line, '6')

    with pytest.raises(HTTPException) as exc:
        await _set(db, refund, '11', current_user)

    assert exc.value.status_code == 422


async def test_the_uninvoiced_remainder_is_refunded_and_confirmed(
    db: AsyncSession, current_user: CurrentUser
) -> None:
    line = await _order_line(db)
    await _invoice(db, line, '6')
    refund = await _set(db, await _open(db, line, current_user), '4', current_user)
    await _open_till(db)

    refund = await svc.confirm_refund(db, refund, payout=RefundPayout.CASH, current=current_user)

    assert refund.completed is True
    assert refund.lines[0].quantity == Decimal('4')
    assert await svc.line_refundable(db, line) == Decimal('0')


async def test_an_invoice_sealed_after_the_line_was_set_refuses_confirm(
    db: AsyncSession, current_user: CurrentUser
) -> None:
    """Not a silent trim to 4: the clerk must learn the invoice holds the units."""
    line = await _order_line(db)
    refund = await _set(db, await _open(db, line, current_user), '6', current_user)
    await _invoice(db, line, '6', completed=False, sealed=True)
    await _open_till(db)

    with pytest.raises(HTTPException) as exc:
        await svc.confirm_refund(db, refund, payout=RefundPayout.CASH, current=current_user)

    assert exc.value.status_code == 409
    assert exc.value.detail == BLOCKED


async def test_opening_a_fully_invoiced_order_is_409_naming_the_invoice(
    db: AsyncSession, current_user: CurrentUser
) -> None:
    line = await _order_line(db)
    await _invoice(db, line, '10')

    with pytest.raises(HTTPException) as exc:
        await _open(db, line, current_user)

    assert exc.value.status_code == 409
    assert exc.value.detail == BLOCKED


async def test_opening_a_fully_refunded_order_keeps_the_old_message(
    db: AsyncSession, current_user: CurrentUser
) -> None:
    line = await _order_line(db)
    refund = await _set(db, await _open(db, line, current_user), '10', current_user)
    await _open_till(db)
    await svc.confirm_refund(db, refund, payout=RefundPayout.CASH, current=current_user)

    with pytest.raises(HTTPException) as exc:
        await _open(db, line, current_user)

    assert exc.value.status_code == 409
    assert exc.value.detail == 'No refundable items remain on this order'
    assert exc.value.code == 'refund.nothing_refundable'
