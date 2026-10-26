"""Stock queries against real tables (spec 030, US3 scenarios 1-3, T013).

Facility A (1) owns warehouse 1 and its transit 2; facility B is built by `setup_inventory`.
"""

from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.models.inventory import (
    InventoryTransfer,
    InventoryTransferDetail,
    LotSerialRqmt,
)
from app.services import stock_query_service as svc
from tests.integration import inventory_seed as seed

D = Decimal


async def _transfer(
    db,
    *,
    origin: int,
    destination: int,
    lines: list[tuple[int, str, str | None]],
    in_transit: bool = True,
    completed: bool = True,
    cancelled: bool = False,
) -> int:
    header = InventoryTransfer(
        facility=1,
        creator=1,
        updater=1,
        creation_time=seed.WHEN,
        modification_time=seed.WHEN,
        warehouse=origin,
        warehouse_to=destination,
        completed=completed,
        cancelled=cancelled,
        in_transit=in_transit,
    )
    db.add(header)
    await db.flush()
    for product, quantity, received in lines:
        db.add(
            InventoryTransferDetail(
                transfer=header.inventory_transfer_id,
                product=product,
                quantity=D(quantity),
                quantity_received=None if received is None else D(received),
            )
        )
    await db.commit()
    return header.inventory_transfer_id


def _by_code(items) -> dict[str, object]:
    return {row.product_code: row for row in items}


async def test_figures_on_hand_reserved_available_and_incoming(db) -> None:
    inv = await seed.setup_inventory(db)
    product = inv.stockable
    await seed.stock(db, product, inv.a.warehouse, '10')
    await seed.reserve(db, product, inv.a.warehouse, '4')
    # A stale reservation under another source is not a claim.
    db.add(
        LotSerialRqmt(
            source=2, reference=999, warehouse=inv.a.warehouse, product=product, quantity=D('50')
        )
    )
    await db.commit()
    # 6 outstanding in transit to A (3 of 8 already received on one line, 1 untouched).
    await _transfer(
        db,
        origin=inv.b.warehouse,
        destination=inv.a.warehouse,
        lines=[(product, '8', '5'), (product, '1', None)],
    )
    # None of these is owed: draft, cancelled, received, and another destination.
    await _transfer(
        db,
        origin=inv.b.warehouse,
        destination=inv.a.warehouse,
        lines=[(product, '7', None)],
        in_transit=False,
        completed=False,
    )
    await _transfer(
        db,
        origin=inv.b.warehouse,
        destination=inv.a.warehouse,
        lines=[(product, '7', None)],
        cancelled=True,
    )
    await _transfer(
        db,
        origin=inv.b.warehouse,
        destination=inv.a.warehouse,
        lines=[(product, '7', '7')],
        in_transit=False,
    )
    await _transfer(
        db,
        origin=inv.a.warehouse,
        destination=inv.b.warehouse,
        lines=[(product, '7', None)],
    )

    items, total = await svc.list_stock(
        db, warehouse=inv.a.warehouse, q=None, include_zero=False, skip=0, limit=50
    )

    assert total == 1
    row = items[0]
    assert (row.on_hand, row.reserved, row.available, row.incoming) == (
        D('10'),
        D('4'),
        D('6'),
        D('4'),
    )
    assert (row.product, row.product_code, row.warehouse) == (product, 'INV', inv.a.warehouse)


async def test_q_filters_code_and_name_and_zero_rows_need_include_zero(db) -> None:
    inv = await seed.setup_inventory(db)
    other = await seed.product(db, code='ZED')
    await seed.stock(db, inv.stockable, inv.a.warehouse, '3')
    await seed.stock(db, other, inv.a.warehouse, '2')
    await seed.stock(db, other, inv.a.warehouse, '-2')

    async def run(**kw):
        args = dict(warehouse=inv.a.warehouse, q=None, include_zero=False, skip=0, limit=50)
        return await svc.list_stock(db, **{**args, **kw})

    assert [r.product_code for r in (await run())[0]] == ['INV']
    assert [r.product_code for r in (await run(include_zero=True))[0]] == ['INV', 'ZED']
    assert [r.product_code for r in (await run(q='inv', include_zero=True))[0]] == ['INV']
    assert [r.product_code for r in (await run(q='PRODUCTO ze', include_zero=True))[0]] == ['ZED']


async def test_paging_is_sorted_by_code(db) -> None:
    inv = await seed.setup_inventory(db)
    for code in ('C3', 'A1', 'B2'):
        await seed.stock(db, await seed.product(db, code=code), 1, '1')

    page, total = await svc.list_stock(db, warehouse=1, q=None, include_zero=False, skip=1, limit=2)

    assert total == 3
    assert [r.product_code for r in page] == ['B2', 'C3']
    assert inv.a.warehouse == 1


async def test_a_product_with_several_labels_counts_once(db) -> None:
    await seed.setup_inventory(db)
    product = await seed.labelled_product(db, code='LBL', labels=3)
    await seed.stock(db, product, 1, '5')
    await seed.reserve(db, product, 1, '2')

    items, total = await svc.list_stock(
        db, warehouse=1, q='LBL', include_zero=False, skip=0, limit=50
    )

    assert total == 1
    assert (items[0].on_hand, items[0].reserved) == (D('5'), D('2'))
    view = await svc.product_stock(db, product)
    assert [(r.warehouse, r.on_hand, r.reserved) for r in view.warehouses] == [(1, D('5'), D('2'))]


async def test_product_view_lists_non_zero_warehouses_and_transit_per_facility(db) -> None:
    inv = await seed.setup_inventory(db)
    product = inv.stockable
    idle = await seed.warehouse(db, facility=1, code='IDLE')
    await seed.stock(db, product, inv.a.warehouse, '10')
    await seed.stock(db, product, idle, '4')
    await seed.stock(db, product, idle, '-4')
    await seed.stock(db, product, inv.b.transit, '3')
    # Only incoming: B has none on hand but 2 coming.
    await _transfer(
        db,
        origin=inv.a.warehouse,
        destination=inv.b.warehouse,
        lines=[(product, '2', None)],
    )

    view = await svc.product_stock(db, product)

    assert view.product == product
    figures = {r.warehouse: (r.on_hand, r.incoming) for r in view.warehouses}
    assert figures == {inv.a.warehouse: (D('10'), D('0')), inv.b.warehouse: (D('0'), D('2'))}
    assert [(t.facility, t.warehouse, t.on_hand) for t in view.in_transit] == [
        (inv.b.facility, inv.b.transit, D('3'))
    ]


async def test_errors(db) -> None:
    inv = await seed.setup_inventory(db)

    with pytest.raises(HTTPException) as in_transit:
        await svc.list_stock(
            db, warehouse=inv.a.transit, q=None, include_zero=False, skip=0, limit=10
        )
    with pytest.raises(HTTPException) as no_warehouse:
        await svc.list_stock(db, warehouse=9999, q=None, include_zero=False, skip=0, limit=10)
    with pytest.raises(HTTPException) as no_product:
        await svc.product_stock(db, 9999)

    assert in_transit.value.status_code == 422
    assert no_warehouse.value.status_code == 404
    assert no_product.value.status_code == 404
