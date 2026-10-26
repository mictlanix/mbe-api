"""The kardex against real tables (spec 030, US4 scenarios 1–4 and SC-004, T017).

Documents post through their own services where they exist (receipt, issue, transfer, counter
pickup); the count, the refund, the itinerary and the legacy rows are seeded straight into the
ledger, with the type and reference those documents use.
"""

from datetime import date, datetime
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.core.clock import local_now
from app.enums import (
    CurrencyCode,
    DeliveryOrderStatus,
    FulfillmentType,
    ItineraryStatus,
    TransactionType,
)
from app.models.inventory import InventoryCount, InventoryTransferDetail, LotSerialTracking
from app.models.logistics import DeliveryOrder
from app.models.sales import CustomerRefund, CustomerRefundDetail, SalesOrderDetail
from app.services import delivery_order_service, kardex_service, stock_ledger
from app.services import inventory_documents as inv
from app.services import inventory_transfer_service as transfers
from tests.integration import delivery_seed
from tests.integration import inventory_seed as seed

D = Decimal
T = TransactionType
BEFORE = datetime(2026, 1, 10, 9, 0)


async def _ledger(
    db, source: int, reference: int, product: int, warehouse: int, qty: str, when=None
) -> None:
    """One entry as the current system or another service would have written it."""
    db.add(
        LotSerialTracking(
            source=int(source),
            reference=reference,
            date=when or local_now(),
            warehouse=warehouse,
            product=product,
            quantity=D(qty),
        )
    )
    await db.commit()


async def _simple(db, kind, current, warehouse: int, product: int, qty: str):
    document = await inv.create_draft(db, kind, warehouse=warehouse, comment=None, current=current)
    document_id = getattr(document, kind.id_attr)
    await inv.add_line(db, kind, document_id, product=product, quantity=D(qty), current=current)
    return await inv.confirm_simple(db, kind, document_id, current=current)


async def _transfer(db, current, origin: int, destination: int, product: int, qty: str):
    transfer = await inv.create_draft(
        db, inv.TRANSFER, warehouse=origin, warehouse_to=destination, comment=None, current=current
    )
    transfer_id = transfer.inventory_transfer_id
    await inv.add_line(
        db, inv.TRANSFER, transfer_id, product=product, quantity=D(qty), current=current
    )
    return await transfers.dispatch(db, transfer_id, current=current)


async def _refund(db, product: int, serial: int) -> int:
    """A completed refund of facility 1 with one line of `product`."""
    order = await seed.sales_order(db, lines=[(product, '1', 1)])
    detail = (
        await db.execute(
            select(SalesOrderDetail.sales_order_detail_id).where(
                SalesOrderDetail.sales_order == order
            )
        )
    ).scalar_one()
    refund = CustomerRefund(
        sales_order=order,
        customer=1,
        creator=1,
        updater=1,
        sales_person=1,
        creation_time=seed.WHEN,
        modification_time=seed.WHEN,
        completed=True,
        cancelled=False,
        facility=1,
        serial=serial,
        date=seed.WHEN,
        currency=CurrencyCode.MXN,
        exchange_rate=D('1'),
    )
    db.add(refund)
    await db.flush()
    db.add(
        CustomerRefundDetail(
            customer_refund=refund.customer_refund_id,
            sales_order_detail=detail,
            quantity=D('1'),
            product=product,
            price=D('100'),
            product_code='INV',
            product_name='Producto INV',
            tax_rate=D('0.16'),
            discount=D('0'),
            exchange_rate=D('1'),
            currency=CurrencyCode.MXN,
            tax_included=False,
            warehouse=1,
        )
    )
    await db.commit()
    return refund.customer_refund_id


async def _kardex(db, product: int, warehouse: int, start: date | None = None, end=None):
    today = local_now().date()
    return await kardex_service.kardex(
        db, product=product, warehouse=warehouse, from_=start or today, to=end or today
    )


def _documents(kardex) -> list[tuple]:
    return [
        (row.source, row.reference, row.document and tuple(row.document.model_dump().values()))
        for row in kardex.rows
    ]


@pytest.fixture
async def setup(db, seeded):
    return await seed.setup_inventory(db)


@pytest.fixture
async def history(db, setup, current_user):
    """100 on hand before the range, then one movement of every resolvable type today."""
    p, b = setup.stockable, setup.b.warehouse
    await _ledger(db, T.INVENTORY_ADJUSTMENT, 0, p, 1, '100', BEFORE)

    receipt = await _simple(db, inv.RECEIPT, current_user, 1, p, '10')
    issue = await _simple(db, inv.ISSUE, current_user, 1, p, '2')

    closed = await _transfer(db, current_user, 1, b, p, '5')
    line = (
        await db.execute(
            select(InventoryTransferDetail.transfer_detail_id).where(
                InventoryTransferDetail.transfer == closed.inventory_transfer_id
            )
        )
    ).scalar_one()
    await transfers.receive(
        db, closed.inventory_transfer_id, [(line, D('2'))], current=current_user
    )
    await transfers.close_remainder(db, closed.inventory_transfer_id, current=current_user)
    cancelled = await _transfer(db, current_user, 1, b, p, '1')
    await transfers.cancel(db, cancelled.inventory_transfer_id, current=current_user)

    count_id = await seed.count(db, 1, [(p, '0')])
    count = await db.get(InventoryCount, count_id)
    count.serial, count.completed = 1, True
    await db.commit()
    await _ledger(db, T.PHYSICAL_COUNT, count_id, p, 1, '3')

    refund = await _refund(db, p, serial=4)
    await _ledger(db, T.CUSTOMER_REFUND, refund, p, 1, '1')

    pickup_id = await delivery_seed.delivery_order(
        db,
        fulfillment_type=FulfillmentType.PICKUP,
        status=DeliveryOrderStatus.READY_FOR_PICKUP,
        lines=[(p, '2', 1, None)],
        serial=11,
    )
    await delivery_order_service.confirm_pickup(
        db,
        await db.get(DeliveryOrder, pickup_id),
        receiver_name='Ana Cliente',
        receiver_id_shown='INE',
        image_file='pickup.png',
        current=current_user,
    )

    itinerary = await delivery_seed.itinerary(db, status=ItineraryStatus.DEPARTED)
    await _ledger(db, T.DELIVERY_ORDER, itinerary, p, 1, '-1')
    # The current system's count adjustment, referencing its receipt.
    await _ledger(db, T.INVENTORY_ADJUSTMENT, receipt.inventory_receipt_id, p, 1, '1')

    return dict(
        receipt=receipt,
        issue=issue,
        closed=closed,
        cancelled=cancelled,
        count=count_id,
        refund=refund,
        pickup=pickup_id,
        itinerary=itinerary,
    )


# ── Scenario 2: each movement resolves to its document ────────────────────────


async def test_every_movement_resolves_with_facility_and_serial(db, setup, history):
    h = history
    receipt, issue = h['receipt'], h['issue']
    closed, cancelled = h['closed'], h['cancelled']

    kardex = await _kardex(db, setup.stockable, 1)

    def transfer(document) -> tuple:
        return (5, document.inventory_transfer_id, 1, document.serial)

    t_closed, t_cancelled = closed.inventory_transfer_id, cancelled.inventory_transfer_id
    assert _documents(kardex) == [
        (4, receipt.inventory_receipt_id, (4, receipt.inventory_receipt_id, 1, receipt.serial)),
        (3, issue.inventory_issue_id, (3, issue.inventory_issue_id, 1, issue.serial)),
        (5, t_closed, transfer(closed)),  # dispatch
        (5, t_closed, transfer(closed)),  # the remainder closed back to the origin
        (5, t_cancelled, transfer(cancelled)),  # dispatch
        (5, t_cancelled, transfer(cancelled)),  # cancel
        (12, h['count'], (12, h['count'], 1, 1)),
        (2, h['refund'], (2, h['refund'], 1, 4)),
        (13, h['pickup'], (13, h['pickup'], 1, 11)),
        (10, h['itinerary'], (10, h['itinerary'], None, None)),
        (8, receipt.inventory_receipt_id, (8, receipt.inventory_receipt_id, 1, receipt.serial)),
    ]
    assert [row.quantity_in - row.quantity_out for row in kardex.rows] == [
        D(q) for q in ('10', '-2', '-5', '3', '-1', '1', '3', '1', '-2', '-1', '1')
    ]
    assert kardex.rows == sorted(kardex.rows, key=lambda r: (r.date, r.lot_serial_tracking_id))
    assert kardex.opening == D('100')


async def test_the_transfer_leg_through_transit_resolves_too(db, setup, history):
    """Receive shows on the transit warehouse, as do dispatch, close and cancel."""
    closed, cancelled = history['closed'], history['cancelled']

    kardex = await _kardex(db, setup.stockable, setup.a.transit)

    documents = {row.document.id: row.document.serial for row in kardex.rows}
    assert documents == {
        closed.inventory_transfer_id: closed.serial,
        cancelled.inventory_transfer_id: cancelled.serial,
    }
    assert [row.quantity_in - row.quantity_out for row in kardex.rows] == [
        D(q) for q in ('5', '-2', '-3', '1', '-1')
    ]
    assert kardex.closing == D('0')


async def test_a_counter_pickup_posts_type_13(db, setup, history):
    """FR-034: the pickup references its delivery order under 13, never the itinerary's 10."""
    sources = (
        await db.execute(
            select(LotSerialTracking.source).where(
                LotSerialTracking.reference == history['pickup'],
                LotSerialTracking.source.in_([T.DELIVERY_ORDER, T.DELIVERY_PICKUP]),
                LotSerialTracking.quantity == D('-2'),
            )
        )
    ).scalars()
    assert list(sources) == [int(T.DELIVERY_PICKUP)]


# ── Scenario 3: unresolved entries show raw ───────────────────────────────────


async def test_unresolved_entries_show_their_type_and_raw_reference(db, setup):
    p = setup.stockable
    other = await seed.product(db, code='OTRO')
    stranger = await _refund(db, other, serial=5)
    await _ledger(db, T.CUSTOMER_REFUND, stranger, p, 1, '0')  # product not on its lines
    await _ledger(db, T.CUSTOMER_REFUND, 9999, p, 1, '1')  # no such refund
    await _ledger(db, T.INVENTORY_ADJUSTMENT, 0, p, 1, '1')  # no receipt
    await _ledger(db, T.SUPPLIER_RETURN, 3, p, 1, '-1')  # never resolved

    kardex = await _kardex(db, p, 1)

    assert _documents(kardex) == [(2, stranger, None), (2, 9999, None), (8, 0, None), (7, 3, None)]
    assert [row.source_name for row in kardex.rows] == [
        'customer_refund',
        'customer_refund',
        'inventory_adjustment',
        'supplier_return',
    ]


# ── Scenarios 1, 4 and SC-004: balances ───────────────────────────────────────


async def test_an_empty_range_opens_and_closes_at_the_same_balance(db, setup, history):
    kardex = await _kardex(db, setup.stockable, 1, date(2026, 2, 1), date(2026, 2, 28))

    assert kardex.rows == []
    assert kardex.opening == kardex.closing == D('100')
    assert kardex.total_in == kardex.total_out == D('0')


async def test_the_closing_balance_is_the_ledger_sum_up_to_the_range_end(db, setup, history):
    p = setup.stockable

    kardex = await _kardex(db, p, 1, date(2026, 1, 10))

    assert kardex.opening == D('0')
    assert kardex.closing == kardex.opening + kardex.total_in - kardex.total_out
    assert kardex.closing == await stock_ledger.on_hand(db, product=p, warehouse=1)
    assert kardex.rows[-1].balance == kardex.closing


async def test_the_range_end_is_inclusive_of_its_whole_day(db, setup):
    p = setup.stockable
    await _ledger(db, T.INVENTORY_ADJUSTMENT, 0, p, 1, '4', datetime(2026, 3, 5, 23, 59, 59))
    await _ledger(db, T.INVENTORY_ADJUSTMENT, 0, p, 1, '6', datetime(2026, 3, 6, 0, 0))

    kardex = await _kardex(db, p, 1, date(2026, 3, 5), date(2026, 3, 5))

    assert [row.quantity_in for row in kardex.rows] == [D('4')]
    assert (kardex.opening, kardex.closing) == (D('0'), D('4'))


# ── Errors ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize('unknown', ['product', 'warehouse'])
async def test_an_unknown_product_or_warehouse_is_404(db, setup, unknown):
    target = {'product': setup.stockable, 'warehouse': 1} | {unknown: 9999}
    with pytest.raises(HTTPException) as exc:
        await _kardex(db, target['product'], target['warehouse'])
    assert exc.value.status_code == 404
