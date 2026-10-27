"""The inventory seed, and the shared inventory mechanics run once against real tables (spec 029).

The story flows (receipts, issues, transfers) are tested through their routes; this only checks
that the fixtures they build on hold what they say, and that `inventory_documents` runs its SQL.
"""

from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.enums import TransactionType
from app.models.inventory import LotSerialTracking
from app.models.product import Product, product_label
from app.models.purchases import PurchaseOrderDetail
from app.models.sales import SalesOrder
from app.services import inventory_documents as inv
from app.services import stock_ledger
from tests.integration import inventory_seed as seed


async def test_seed_builds_two_sites_products_stock_and_orders(db, seeded):
    setup = await seed.setup_inventory(db)
    assert setup.a == seed.Site(facility=1, warehouse=1, transit=2)
    assert setup.b.facility != 1
    assert len({setup.b.warehouse, setup.b.transit, 1, 2}) == 4

    await seed.stock(db, setup.stockable, setup.a.warehouse, '10')
    await seed.reserve(db, setup.stockable, setup.a.warehouse, '3')
    available = await stock_ledger.available(
        db, product=setup.stockable, warehouse=setup.a.warehouse
    )
    assert available == Decimal(7)

    order, details = await seed.purchase_order(
        db, lines=[(setup.stockable, '5', setup.a.warehouse), (1, '2', setup.b.warehouse)]
    )
    rows = (
        await db.execute(
            select(PurchaseOrderDetail.warehouse).where(PurchaseOrderDetail.purchase_order == order)
        )
    ).scalars()
    assert len(details) == 2
    assert sorted(rows) == sorted([setup.a.warehouse, setup.b.warehouse])

    draft = await seed.sales_order(db, lines=[(1, '1', 1)], completed=False)
    assert (await db.get(SalesOrder, draft)).completed is False


async def test_issue_confirm_and_cancel_round_trip(db, seeded, current_user):
    setup = await seed.setup_inventory(db)
    await seed.stock(db, setup.stockable, 1, '10')

    issue = await inv.create_draft(db, inv.ISSUE, warehouse=1, comment=None, current=current_user)
    issue_id = issue.inventory_issue_id
    for _ in range(2):
        await inv.add_line(
            db,
            inv.ISSUE,
            issue_id,
            product=setup.stockable,
            quantity=Decimal(2),
            current=current_user,
        )
    with pytest.raises(HTTPException) as exc:
        await inv.add_line(
            db,
            inv.ISSUE,
            issue_id,
            product=setup.non_stockable,
            quantity=Decimal(1),
            current=current_user,
        )
    assert exc.value.status_code == 422

    confirmed = await inv.confirm_simple(db, inv.ISSUE, issue_id, current=current_user)
    assert (confirmed.serial, confirmed.completed) == (1, True)
    assert await stock_ledger.available(db, product=setup.stockable, warehouse=1) == Decimal(6)

    cancelled = await inv.cancel(db, inv.ISSUE, issue_id, current=current_user)
    assert cancelled.cancelled is True
    assert await stock_ledger.available(db, product=setup.stockable, warehouse=1) == Decimal(10)
    entries = (
        await db.execute(
            select(LotSerialTracking.quantity).where(
                LotSerialTracking.source == int(TransactionType.INVENTORY_ISSUE),
                LotSerialTracking.reference == issue_id,
            )
        )
    ).scalars()
    assert sorted(entries) == [Decimal(-4), Decimal(4)]


async def test_transfer_draft_rules_and_draft_cancel(db, seeded, current_user):
    setup = await seed.setup_inventory(db)

    with pytest.raises(HTTPException) as exc:
        await inv.create_draft(
            db, inv.TRANSFER, warehouse=1, warehouse_to=1, comment=None, current=current_user
        )
    assert exc.value.status_code == 422

    transfer = await inv.create_draft(
        db,
        inv.TRANSFER,
        warehouse=1,
        warehouse_to=setup.b.warehouse,
        comment='x',
        current=current_user,
    )
    assert (transfer.facility, transfer.in_transit) == (1, False)

    cancelled = await inv.cancel(
        db, inv.TRANSFER, transfer.inventory_transfer_id, current=current_user
    )
    assert cancelled.cancelled is True
    posted = (
        await db.execute(
            select(LotSerialTracking).where(
                LotSerialTracking.source == int(TransactionType.INVENTORY_TRANSFER)
            )
        )
    ).first()
    assert posted is None


# ── Counts (spec 030, T005) ───────────────────────────────────────────────────


async def test_count_seed_writes_a_draft_the_count_kind_reads(db, seeded, current_user):
    setup = await seed.setup_inventory(db)

    count_id = await seed.count(
        db, setup.b.warehouse, [(setup.stockable, '7'), (1, '0')], creator=1
    )

    count = await inv.get_document(db, inv.COUNT, count_id)
    assert (count.facility, count.warehouse, count.serial) == (
        setup.b.facility,
        setup.b.warehouse,
        None,
    )
    assert (count.completed, count.cancelled) == (False, False)
    lines = (await inv.attach(db, inv.COUNT, count)).lines
    assert [(ln.product, ln.product_code, ln.quantity) for ln in lines] == [
        (setup.stockable, 'INV', Decimal(7)),
        (1, (await db.get(Product, 1)).code, Decimal(0)),
    ]
    assert all(ln.quantity_on_hand is None and ln.difference is None for ln in lines)

    cancelled = await inv.cancel(db, inv.COUNT, count_id, current=current_user)
    assert cancelled.cancelled is True
    posted = (
        await db.execute(
            select(LotSerialTracking).where(
                LotSerialTracking.source == int(TransactionType.PHYSICAL_COUNT)
            )
        )
    ).first()
    assert posted is None


async def test_a_confirmed_count_cannot_be_cancelled(db, seeded, current_user):
    setup = await seed.setup_inventory(db)
    count_id = await seed.count(db, 1, [(setup.stockable, '1')])
    count = await inv.get_document(db, inv.COUNT, count_id)
    count.completed, count.serial = True, 1
    await db.commit()

    with pytest.raises(HTTPException) as exc:
        await inv.cancel(db, inv.COUNT, count_id, current=current_user)

    assert exc.value.status_code == 409
    assert (await inv.get_document(db, inv.COUNT, count_id)).cancelled is False


async def test_a_product_with_several_labels(db, seeded):
    product_id = await seed.labelled_product(db, code='LBL', labels=3)

    labels = (
        await db.execute(select(product_label.c.label).where(product_label.c.product == product_id))
    ).scalars()
    assert len(set(labels)) == 3
    assert (await db.get(Product, product_id)).stockable is True


async def test_purchasing_seeds_requests_suppliers_costs_and_linked_orders(db, seeded):
    """Spec 031: the request, supplier and cost helpers, and a PO line linked to a request line."""
    from app.core.constants import COST_PRICE_LIST_ID
    from app.models.product import ProductPrice
    from app.models.purchases import PurchaseOrder, PurchaseRequest, PurchaseRequestDetail

    setup = await seed.setup_inventory(db)
    vendor = await seed.supplier(db)
    item = await seed.product(db, code='BUY', supplier=vendor, min_order_qty=6)

    await seed.cost(db, item, '12.5')
    await seed.cost(db, item, '13')
    costs = (
        (
            await db.execute(
                select(ProductPrice.price).where(
                    ProductPrice.product == item, ProductPrice.price_list == COST_PRICE_LIST_ID
                )
            )
        )
        .scalars()
        .all()
    )
    assert costs == [Decimal('13')]

    request, request_lines = await seed.purchase_request(
        db,
        warehouse=setup.a.warehouse,
        lines=[(item, '6', setup.a.warehouse), (item, '2', setup.b.warehouse)],
        state='approved',
        serial=7,
    )
    header = await db.get(PurchaseRequest, request)
    assert (header.completed, header.approved, header.cancelled, header.serial) == (
        True,
        True,
        False,
        7,
    )
    line = await db.get(PurchaseRequestDetail, request_lines[1])
    assert (line.warehouse, line.to_purchase, line.accepted, line.customer) == (
        setup.b.warehouse,
        True,
        False,
        1,
    )

    order, details = await seed.purchase_order(
        db,
        lines=[(item, '6', setup.a.warehouse)],
        completed=False,
        supplier=vendor,
        price='12',
        request_lines=[request_lines[0]],
    )
    assert (await db.get(PurchaseOrder, order)).supplier == vendor
    detail = await db.get(PurchaseOrderDetail, details[0])
    assert (detail.price, detail.purchase_request_detail) == (Decimal('12'), request_lines[0])
