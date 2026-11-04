"""Physical counts from draft to confirmed, over their routes (spec 030, US1 and US2).

US1 (T007): numbering, posting, the blind draft, batches, uncounted products, repeats, the facility
rule, the warnings, the list scope. US2 (T011): a 15,000-line count built in batches and confirmed
in one go, and a failure in the middle of posting rolling everything back.
"""

import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import func, insert, select, update

from app.core.deps import CurrentUser
from app.enums import AccessRight, CurrencyCode, EntityStatus, TransactionType
from app.models.core import Employee
from app.models.inventory import InventoryCount, InventoryCountDetail, LotSerialTracking
from app.models.product import Product
from app.models.user import AccessPrivilege
from app.services import inventory_count_service as svc
from app.services import stock_ledger
from tests.integration import inventory_seed as seed

URL = '/api/v1/inventory-counts'
SOURCE = int(TransactionType.PHYSICAL_COUNT)
WHEN = seed.WHEN


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _draft(client, warehouse: int = 1) -> int:
    response = await client.post(URL, json={'warehouse': warehouse})
    assert response.status_code == 201, response.text
    return response.json()['inventory_count_id']


async def _set(client, count: int, lines: list[dict]):
    return await client.put(f'{URL}/{count}/lines', json={'lines': lines})


async def _confirm(client, count: int, include_uncounted: bool = False):
    return await client.post(
        f'{URL}/{count}/confirm', json={'include_uncounted': include_uncounted}
    )


async def _entries(db, count: int) -> list[tuple[int, Decimal]]:
    rows = await db.execute(
        select(LotSerialTracking.product, LotSerialTracking.quantity)
        .where(LotSerialTracking.source == SOURCE, LotSerialTracking.reference == count)
        .order_by(LotSerialTracking.product)
    )
    return [tuple(row) for row in rows.all()]


async def _on_hand(db, product: int, warehouse: int = 1) -> Decimal:
    return await stock_ledger.on_hand(db, product=product, warehouse=warehouse)


async def _header(db, count: int) -> InventoryCount:
    return (
        await db.execute(
            select(InventoryCount)
            .where(InventoryCount.inventory_count_id == count)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


def _by_product(body: dict) -> dict[int, dict]:
    return {line['product']: line for line in body['lines']}


async def _completed(db, warehouse: int, product: int, *, serial: int, confirmed: datetime) -> int:
    """A count confirmed at `confirmed`, inserted directly (no ledger: only its header matters)."""
    count = await seed.count(db, warehouse, [(product, '1')])
    await db.execute(
        update(InventoryCount)
        .where(InventoryCount.inventory_count_id == count)
        .values(completed=True, serial=serial, modification_time=confirmed)
    )
    await db.commit()
    return count


# ── Confirm (US1 scenario 1, SC-001) ──────────────────────────────────────────


async def test_confirm_numbers_the_count_and_posts_only_the_differences(client, db, seeded):
    setup = await seed.setup_inventory(db)
    a, b = setup.stockable, await seed.product(db, code='B')
    await seed.stock(db, a, 1, '10')
    await seed.stock(db, b, 1, '5')
    # Serials are per facility: 4 here, and a higher one in facility 2 that must not count.
    await _completed(db, 1, a, serial=4, confirmed=WHEN - timedelta(days=30))
    await _completed(db, setup.b.warehouse, a, serial=9, confirmed=WHEN - timedelta(days=30))

    count = await _draft(client)
    response = await _set(
        client, count, [{'product': a, 'quantity': '7'}, {'product_code': 'B', 'quantity': '5'}]
    )
    assert response.status_code == 200, response.text

    response = await _confirm(client, count)
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body['status'], body['serial']) == ('completed', 5)
    lines = _by_product(body)
    assert Decimal(lines[a]['quantity_on_hand']) == 10
    assert Decimal(lines[a]['difference']) == -3
    assert Decimal(lines[b]['quantity_on_hand']) == 5
    assert Decimal(lines[b]['difference']) == 0

    assert await _entries(db, count) == [(a, Decimal(-3))]
    entry = (
        await db.execute(
            select(LotSerialTracking).where(
                LotSerialTracking.source == SOURCE, LotSerialTracking.reference == count
            )
        )
    ).scalar_one()
    assert (entry.warehouse, entry.lot_number, entry.serial_number, entry.expiration_date) == (
        1,
        None,
        None,
        None,
    )
    # SC-001: on hand equals counted, and the entries sum to the recorded differences.
    assert await _on_hand(db, a) == 7
    assert await _on_hand(db, b) == 5

    # Read-only afterwards, and the GET shows the same figures.
    got = (await client.get(f'{URL}/{count}')).json()
    assert Decimal(_by_product(got)[a]['difference']) == -3


async def test_difference_is_against_stock_at_the_moment_of_confirm(client, db, seeded):
    setup = await seed.setup_inventory(db)
    await seed.stock(db, setup.stockable, 1, '10')
    count = await _draft(client)
    await _set(client, count, [{'product': setup.stockable, 'quantity': '7'}])
    # Stock moves after the physical count but before the confirm (US1 scenario 5).
    await seed.stock(db, setup.stockable, 1, '-4')

    body = (await _confirm(client, count)).json()

    line = _by_product(body)[setup.stockable]
    assert (Decimal(line['quantity_on_hand']), Decimal(line['difference'])) == (6, 1)
    assert await _on_hand(db, setup.stockable) == 7


# ── Blind drafts (FR-003a, R4) ────────────────────────────────────────────────


async def test_a_draft_shows_no_on_hand_or_difference(client, db, seeded):
    setup = await seed.setup_inventory(db)
    await seed.stock(db, setup.stockable, 1, '10')
    count = await _draft(client)

    responses = [
        await _set(client, count, [{'product': setup.stockable, 'quantity': '7'}]),
        await client.get(f'{URL}/{count}'),
        await client.put(f'{URL}/{count}', json={'comment': 'pasillo 3'}),
    ]
    line_id = responses[1].json()['lines'][0]['count_detail_id']
    responses.append(await client.put(f'{URL}/{count}/lines/{line_id}', json={'quantity': '8'}))

    for response in responses:
        assert response.status_code == 200, response.text
        [line] = response.json()['lines']
        assert line['quantity_on_hand'] is None
        assert line['difference'] is None
    row = (
        await db.execute(
            select(InventoryCountDetail.quantity_on_hand, InventoryCountDetail.difference)
        )
    ).one()
    assert tuple(row) == (None, None)


# ── Batches (FR-002, R3) ──────────────────────────────────────────────────────


async def test_a_batch_replaces_quantities_and_a_repeat_changes_nothing(client, db, seeded):
    setup = await seed.setup_inventory(db)
    b = await seed.product(db, code='B')
    count = await _draft(client)
    await _set(client, count, [{'product': setup.stockable, 'quantity': '5'}])

    batch = [
        {'product_code': 'INV', 'quantity': '2'},
        {'product': b, 'quantity': '0'},
    ]
    first = (await _set(client, count, batch)).json()
    again = (await _set(client, count, batch)).json()

    for body in (first, again):
        lines = _by_product(body)
        assert len(lines) == 2
        assert Decimal(lines[setup.stockable]['quantity']) == 2
        assert Decimal(lines[b]['quantity']) == 0
        assert lines[b]['product_code'] == 'B'


async def test_a_bad_batch_is_422_listing_every_bad_entry_and_applies_nothing(client, db, seeded):
    setup = await seed.setup_inventory(db)
    count = await _draft(client)
    await _set(client, count, [{'product': setup.stockable, 'quantity': '5'}])

    response = await _set(
        client,
        count,
        [
            {'product': setup.stockable, 'quantity': '1'},
            {'product_code': 'NOPE', 'quantity': '1'},
            {'product': setup.non_stockable, 'quantity': '1'},
            {'product_code': 'INV', 'quantity': '3'},
            {'product': 9999, 'quantity': '1'},
        ],
    )

    assert response.status_code == 422, response.text
    body = response.json()
    assert response.headers['content-type'].startswith('application/problem+json')
    assert body['code'] == 'inventory_count.batch_invalid'
    assert body['detail']
    assert body['errors'] == [
        {
            'index': 1,
            'product_code': 'NOPE',
            'reason': 'unknown product',
            'code': 'inventory_count_line.product_unknown',
        },
        {
            'index': 2,
            'product': setup.non_stockable,
            'reason': 'not stockable',
            'code': 'inventory_count_line.not_stockable',
        },
        {
            'index': 3,
            'product_code': 'INV',
            'reason': 'repeated in the batch',
            'code': 'inventory_count_line.repeated',
        },
        {
            'index': 4,
            'product': 9999,
            'reason': 'unknown product',
            'code': 'inventory_count_line.product_unknown',
        },
    ]
    [line] = (await client.get(f'{URL}/{count}')).json()['lines']
    assert Decimal(line['quantity']) == 5


async def test_single_line_edit_replaces_and_remove_deletes(client, db, seeded):
    setup = await seed.setup_inventory(db)
    count = await _draft(client)
    body = (await _set(client, count, [{'product': setup.stockable, 'quantity': '5'}])).json()
    line_id = body['lines'][0]['count_detail_id']

    response = await client.put(f'{URL}/{count}/lines/{line_id}', json={'quantity': '0'})
    assert Decimal(response.json()['lines'][0]['quantity']) == 0

    response = await client.delete(f'{URL}/{count}/lines/{line_id}')
    assert response.json()['lines'] == []
    assert (await client.delete(f'{URL}/{count}/lines/{line_id}')).status_code == 404


# ── Uncounted as zero (FR-005) ────────────────────────────────────────────────


async def test_without_include_uncounted_other_products_are_untouched(client, db, seeded):
    setup = await seed.setup_inventory(db)
    c = await seed.product(db, code='C')
    await seed.stock(db, setup.stockable, 1, '2')
    await seed.stock(db, c, 1, '4')
    count = await _draft(client)
    await _set(client, count, [{'product': setup.stockable, 'quantity': '2'}])

    body = (await _confirm(client, count)).json()

    assert list(_by_product(body)) == [setup.stockable]
    assert await _entries(db, count) == []
    assert await _on_hand(db, c) == 4


async def test_include_uncounted_adds_them_at_zero_and_corrects_negatives(client, db, seeded):
    setup = await seed.setup_inventory(db)
    c = await seed.product(db, code='C')
    d = await seed.product(db, code='D')
    gone = await seed.product(db, code='E')
    await seed.stock(db, setup.stockable, 1, '2')
    await seed.stock(db, c, 1, '4')
    await seed.stock(db, d, 1, '-3')
    await seed.stock(db, gone, 1, '0')
    # Not stockable: never added, whatever its balance.
    await seed.stock(db, setup.non_stockable, 1, '6')
    # Another warehouse's stock is not this count's.
    await seed.stock(db, c, setup.b.warehouse, '9')
    count = await _draft(client)
    await _set(client, count, [{'product': setup.stockable, 'quantity': '2'}])

    response = await _confirm(client, count, include_uncounted=True)

    assert response.status_code == 200, response.text
    lines = _by_product(response.json())
    assert sorted(lines) == sorted([setup.stockable, c, d])
    assert (lines[c]['product_code'], Decimal(lines[c]['quantity'])) == ('C', 0)
    assert Decimal(lines[c]['difference']) == -4
    assert Decimal(lines[d]['difference']) == 3
    assert await _entries(db, count) == sorted([(c, Decimal(-4)), (d, Decimal(3))])
    assert await _on_hand(db, c) == 0
    assert await _on_hand(db, d) == 0
    assert await _on_hand(db, setup.non_stockable) == 6
    assert await _on_hand(db, c, setup.b.warehouse) == 9


async def test_an_empty_count_is_refused_and_uses_no_serial(client, db, seeded):
    setup = await seed.setup_inventory(db)
    empty = await seed.warehouse(db, facility=1, code='W3')
    count = await _draft(client, empty)

    # Without lines, with or without uncounted products to add (the warehouse has none).
    for include in (False, True):
        response = await _confirm(client, count, include_uncounted=include)
        assert response.status_code == 409, response.text
    assert (await _header(db, count)).serial is None

    other = await _draft(client)
    await _set(client, other, [{'product': setup.stockable, 'quantity': '1'}])
    assert (await _confirm(client, other)).json()['serial'] == 1


# ── Repeats and cancel (FR-004, FR-006, FR-007) ───────────────────────────────


async def test_a_second_confirm_and_any_edit_are_409_and_post_nothing(client, db, seeded):
    setup = await seed.setup_inventory(db)
    await seed.stock(db, setup.stockable, 1, '10')
    count = await _draft(client)
    body = (await _set(client, count, [{'product': setup.stockable, 'quantity': '7'}])).json()
    line_id = body['lines'][0]['count_detail_id']
    assert (await _confirm(client, count)).status_code == 200

    responses = [
        await _confirm(client, count),
        await _confirm(client, count, include_uncounted=True),
        await _set(client, count, [{'product': setup.stockable, 'quantity': '1'}]),
        await client.put(f'{URL}/{count}/lines/{line_id}', json={'quantity': '1'}),
        await client.delete(f'{URL}/{count}/lines/{line_id}'),
        await client.put(f'{URL}/{count}', json={'comment': 'x'}),
    ]

    assert [r.status_code for r in responses] == [409] * 6
    assert await _entries(db, count) == [(setup.stockable, Decimal(-3))]
    assert (await _header(db, count)).serial == 1


async def test_a_confirmed_count_cannot_be_cancelled(client, db, seeded):
    setup = await seed.setup_inventory(db)
    await seed.stock(db, setup.stockable, 1, '10')
    count = await _draft(client)
    await _set(client, count, [{'product': setup.stockable, 'quantity': '7'}])
    await _confirm(client, count)

    response = await client.post(f'{URL}/{count}/cancel')

    assert response.status_code == 409
    assert response.json()['detail'] == 'A confirmed count cannot be cancelled'
    assert await _entries(db, count) == [(setup.stockable, Decimal(-3))]
    assert await _on_hand(db, setup.stockable) == 7


async def test_cancelling_a_draft_posts_nothing(client, db, seeded):
    setup = await seed.setup_inventory(db)
    await seed.stock(db, setup.stockable, 1, '10')
    count = await _draft(client)
    await _set(client, count, [{'product': setup.stockable, 'quantity': '7'}])

    response = await client.post(f'{URL}/{count}/cancel')

    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'cancelled'
    assert await _entries(db, count) == []
    assert await _on_hand(db, setup.stockable) == 10
    assert (await _confirm(client, count)).status_code == 409


# ── Warnings (FR-010, FR-010a) ────────────────────────────────────────────────


async def test_a_count_below_the_reserved_quantity_warns_and_still_confirms(client, db, seeded):
    setup = await seed.setup_inventory(db)
    b = await seed.product(db, code='B')
    await seed.stock(db, setup.stockable, 1, '10')
    await seed.stock(db, b, 1, '10')
    await seed.reserve(db, setup.stockable, 1, '4')
    await seed.reserve(db, b, 1, '1')
    # A reservation in another warehouse is not this one's.
    await seed.reserve(db, b, setup.b.warehouse, '50')
    count = await _draft(client)
    await _set(
        client,
        count,
        [{'product': setup.stockable, 'quantity': '3'}, {'product': b, 'quantity': '1'}],
    )

    response = await _confirm(client, count)

    assert response.status_code == 200, response.text
    [warning] = response.json()['negative_available']
    assert warning['product'] == setup.stockable
    assert warning['product_code'] == 'INV'
    assert (
        Decimal(warning['counted']),
        Decimal(warning['reserved']),
        Decimal(warning['available']),
    ) == (3, 4, -1)
    assert await _on_hand(db, setup.stockable) == 3


async def test_another_count_confirmed_at_or_after_this_ones_creation_is_reported(
    client, db, seeded
):
    setup = await seed.setup_inventory(db)
    b = await seed.product(db, code='B')
    c = await seed.product(db, code='C')
    # This count was created at WHEN (2026-10-01 10:00).
    count = await seed.count(db, 1, [(setup.stockable, '1'), (b, '1'), (c, '1')])
    same_second = await _completed(db, 1, setup.stockable, serial=4, confirmed=WHEN)
    later = await _completed(db, 1, b, serial=5, confirmed=WHEN + timedelta(days=1))
    await _completed(db, 1, c, serial=3, confirmed=WHEN - timedelta(seconds=1))
    await _completed(db, setup.b.warehouse, c, serial=6, confirmed=WHEN + timedelta(days=1))

    response = await _confirm(client, count)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body['corrected_by_other_counts'] == [
        {
            'product': setup.stockable,
            'product_code': 'INV',
            'inventory_count_id': same_second,
            'serial': 4,
        },
        {'product': b, 'product_code': 'B', 'inventory_count_id': later, 'serial': 5},
    ]
    assert body['negative_available'] == []


async def test_two_drafts_of_one_warehouse_the_later_confirm_warns(client, db, seeded):
    setup = await seed.setup_inventory(db)
    await seed.stock(db, setup.stockable, 1, '10')
    first = await _draft(client)
    second = await _draft(client)
    for count, quantity in ((first, '8'), (second, '6')):
        await _set(client, count, [{'product': setup.stockable, 'quantity': quantity}])

    one = (await _confirm(client, second)).json()
    two = (await _confirm(client, first)).json()

    assert one['corrected_by_other_counts'] == []
    assert [w['inventory_count_id'] for w in two['corrected_by_other_counts']] == [second]
    # The later confirm corrects against the stock the earlier one left.
    assert Decimal(_by_product(two)[setup.stockable]['difference']) == 2
    assert await _on_hand(db, setup.stockable) == 8


# ── Products and warehouses ───────────────────────────────────────────────────


async def test_a_product_discontinued_after_it_went_on_the_draft_still_confirms(client, db, seeded):
    setup = await seed.setup_inventory(db)
    await seed.stock(db, setup.stockable, 1, '10')
    count = await _draft(client)
    await _set(client, count, [{'product': setup.stockable, 'quantity': '7'}])
    await db.execute(
        update(Product)
        .where(Product.product_id == setup.stockable)
        .values(status=EntityStatus.ARCHIVED)
    )
    await db.commit()

    response = await _confirm(client, count)

    assert response.status_code == 200, response.text
    assert await _on_hand(db, setup.stockable) == 7


async def test_an_in_transit_warehouse_and_a_non_stockable_product_are_422(client, db, seeded):
    setup = await seed.setup_inventory(db)
    assert (await client.post(URL, json={'warehouse': setup.a.transit})).status_code == 422
    assert (await client.post(URL, json={'warehouse': 9999})).status_code == 404
    count = await _draft(client)
    response = await client.put(f'{URL}/{count}', json={'warehouse': setup.b.transit})
    assert response.status_code == 422

    response = await _set(client, count, [{'product': setup.non_stockable, 'quantity': '1'}])
    assert response.status_code == 422
    assert response.json()['errors'][0]['reason'] == 'not stockable'


async def test_moving_the_draft_to_another_facilitys_warehouse_moves_its_facility(
    client, db, seeded
):
    setup = await seed.setup_inventory(db)
    count = await _draft(client)

    body = (await client.put(f'{URL}/{count}', json={'warehouse': setup.b.warehouse})).json()

    assert (body['warehouse'], body['facility']) == (setup.b.warehouse, setup.b.facility)


# ── Facility rule and list scope (FR-008, FR-009) ─────────────────────────────


READ = int(AccessRight.READ)
CREATE = int(AccessRight.CREATE)
UPDATE = int(AccessRight.UPDATE)
DELETE = int(AccessRight.DELETE)


@dataclass
class World:
    a: int  # facility 1's warehouse, and the point of sale's
    b: int  # facility 2's warehouse
    c: int  # a second warehouse of facility 1


@pytest.fixture
def current_user() -> CurrentUser:
    """A non-administrator at point of sale 1 (warehouse 1, facility 1), employee 1."""
    return CurrentUser(
        user_id='tester',
        session_version=1,
        administrator=False,
        facility_id=1,
        employee_id=1,
        point_sale_id=1,
        cash_drawer_id=1,
    )


@pytest.fixture(autouse=True)
async def _rights(request, db, seeded) -> None:
    """Every right on counts, unless the test asks for a narrower `grant` itself."""
    if 'world' not in request.fixturenames:
        await _grant(db, READ | CREATE | UPDATE | DELETE)


async def _grant(db, mask: int) -> None:
    db.add(AccessPrivilege(user_id='tester', system_object=74, privileges=mask))
    await db.commit()


@pytest.fixture
async def world(db, seeded) -> World:
    setup = await seed.setup_inventory(db)
    db.add(
        Employee(
            employee_id=2,
            first_name='Bea',
            last_name='Soto',
            nickname='bea',
            gender=2,
            birthday=datetime(1991, 1, 1).date(),
            sales_person=False,
            status=EntityStatus.ACTIVE,
            start_job_date=datetime(2021, 1, 1).date(),
        )
    )
    await db.commit()
    return World(a=1, b=setup.b.warehouse, c=await seed.warehouse(db, facility=1, code='W3'))


async def _make(db, *, creator: int, warehouse: int, state: str = 'draft') -> int:
    count = await seed.count(db, warehouse, [], creator=creator)
    await db.execute(
        update(InventoryCount)
        .where(InventoryCount.inventory_count_id == count)
        .values(completed=state == 'completed', cancelled=state == 'cancelled')
    )
    await db.commit()
    return count


async def _listed(client, **params) -> list[int]:
    response = await client.get(URL, params={'limit': 100, **params})
    assert response.status_code == 200, response.text
    return [item['inventory_count_id'] for item in response.json()['items']]


async def test_list_default_scope_is_the_pos_warehouse_plus_own_newest_first(client, db, world):
    await _grant(db, READ)
    pos = await _make(db, creator=2, warehouse=world.a)
    own = await _make(db, creator=1, warehouse=world.c)
    other = await _make(db, creator=2, warehouse=world.b)

    assert await _listed(client) == [own, pos]
    assert other not in await _listed(client, warehouse=world.b)
    assert await _listed(client, warehouse=world.c) == [own]


async def test_list_with_the_delete_right_sees_everything(client, db, world):
    await _grant(db, READ | DELETE)
    ids = [
        await _make(db, creator=2, warehouse=world.a),
        await _make(db, creator=1, warehouse=world.c),
        await _make(db, creator=2, warehouse=world.b),
    ]

    assert await _listed(client) == sorted(ids, reverse=True)
    assert await _listed(client, warehouse=world.b) == [ids[2]]


async def test_list_filters_by_state(client, db, world):
    await _grant(db, READ)
    by_state = {
        state: await _make(db, creator=1, warehouse=world.a, state=state)
        for state in ('draft', 'completed', 'cancelled')
    }

    for state, count in by_state.items():
        assert await _listed(client, status=state) == [count]
    response = await client.get(URL, params={'status': 'in_transit'})
    assert response.status_code == 422


@pytest.mark.parametrize('step', ['confirm', 'cancel'])
async def test_another_facilitys_non_administrator_gets_403(client, db, world, current_user, step):
    await _grant(db, READ | UPDATE | DELETE)
    count = await _make(db, creator=2, warehouse=world.a)
    current_user.facility_id = 2

    response = await client.post(f'{URL}/{count}/{step}')

    assert response.status_code == 403
    assert response.json()['detail'] == 'Document belongs to another facility'
    assert (await _header(db, count)).completed is False
    assert (await _header(db, count)).cancelled is False


async def test_without_the_right_on_object_74_a_count_action_is_403(client, db, world):
    await _grant(db, READ)
    count = await _make(db, creator=1, warehouse=world.a)

    responses = [
        await client.post(URL, json={'warehouse': world.a}),
        await client.post(f'{URL}/{count}/confirm'),
        await client.post(f'{URL}/{count}/cancel'),
    ]

    assert [r.status_code for r in responses] == [403] * 3


# ── The opening count at scale (US2, T011) ────────────────────────────────────

SIZE = 15_000
BATCH = 2_000


async def _many_products(db, n: int) -> list[int]:
    """`n` stockable products, inserted in bulk; their ids in code order."""
    rows = [
        {
            'code': f'M{i:05d}',
            'name': f'Producto M{i:05d}',
            'unit_of_measurement': 'H87',
            'stockable': True,
            'perishable': False,
            'seriable': False,
            'purchasable': True,
            'salable': True,
            'invoiceable': True,
            'tax_rate': Decimal('0.16'),
            'tax_included': False,
            'price_type': 0,
            'currency': CurrencyCode.MXN,
            'min_order_qty': 1,
            'status': EntityStatus.ACTIVE,
            'stock_verification': True,
        }
        for i in range(n)
    ]
    for start in range(0, n, 1000):
        await db.execute(insert(Product), rows[start : start + 1000])
    await db.commit()
    ids = await db.execute(
        select(Product.product_id).where(Product.code.like('M%')).order_by(Product.code)
    )
    return list(ids.scalars().all())


async def _bulk_stock(db, quantities: dict[int, Decimal], warehouse: int = 1) -> None:
    rows = [
        {
            'source': int(TransactionType.INVENTORY_ADJUSTMENT),
            'reference': 0,
            'date': WHEN,
            'warehouse': warehouse,
            'product': product,
            'quantity': quantity,
        }
        for product, quantity in quantities.items()
        if quantity
    ]
    for start in range(0, len(rows), 1000):
        await db.execute(insert(LotSerialTracking), rows[start : start + 1000])
    await db.commit()


def _counted(i: int) -> Decimal:
    return Decimal(i % 3)


def _stock_of(i: int) -> Decimal:
    return Decimal(i % 5)


async def _build_in_batches(client, count: int, products: list[int]) -> None:
    for start in range(0, len(products), BATCH):
        batch = [
            {'product': product, 'quantity': str(_counted(start + offset))}
            for offset, product in enumerate(products[start : start + BATCH])
        ]
        response = await _set(client, count, batch)
        assert response.status_code == 200, response.text[:500]


async def test_a_15000_line_count_is_built_in_batches_and_confirmed_at_once(client, db, seeded):
    products = await _many_products(db, SIZE)
    await _bulk_stock(db, {p: _stock_of(i) for i, p in enumerate(products)})
    count = await _draft(client)

    await _build_in_batches(client, count, products)
    # A repeated batch changes nothing (US2 scenario 2).
    await _build_in_batches(client, count, products[:BATCH])
    lines = (
        await db.execute(
            select(func.count(), func.sum(InventoryCountDetail.quantity)).where(
                InventoryCountDetail.count_id == count
            )
        )
    ).one()
    assert lines[0] == SIZE
    assert lines[1] == sum(_counted(i) for i in range(SIZE))

    started = time.perf_counter()
    response = await _confirm(client, count)
    elapsed = time.perf_counter() - started
    print(f'\nSQLite confirm of {SIZE} lines: {elapsed:.2f} s')

    assert response.status_code == 200, response.text[:500]
    assert len(response.json()['lines']) == SIZE
    differing = sum(1 for i in range(SIZE) if _counted(i) != _stock_of(i))
    entries = await _entries(db, count)
    assert len(entries) == differing
    on_hand = dict(
        (
            await db.execute(
                select(LotSerialTracking.product, func.sum(LotSerialTracking.quantity))
                .where(LotSerialTracking.warehouse == 1, LotSerialTracking.product.in_(products))
                .group_by(LotSerialTracking.product)
            )
        ).all()
    )
    assert all(
        on_hand.get(product, Decimal(0)) == _counted(i) for i, product in enumerate(products)
    )


async def test_a_failure_in_the_middle_of_posting_rolls_everything_back(client, db, seeded):
    products = await _many_products(db, 3_000)
    # Every product differs, so posting takes three chunks of 1,000.
    await _bulk_stock(db, {p: Decimal(5) for p in products})
    count = await _draft(client)
    await _build_in_batches(client, count, products)

    real = svc._insert_entries
    calls = 0

    async def failing(session, rows):  # noqa: ANN001, ANN202
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError('injected')
        await real(session, rows)

    with patch.object(svc, '_insert_entries', failing), pytest.raises(RuntimeError):
        await _confirm(client, count)

    assert calls == 3
    assert await _entries(db, count) == []
    header = await _header(db, count)
    assert (header.serial, header.completed) == (None, False)
    nulls = (
        await db.execute(
            select(func.count()).where(
                InventoryCountDetail.count_id == count,
                InventoryCountDetail.quantity_on_hand.is_not(None),
            )
        )
    ).scalar_one()
    assert nulls == 0

    # Nothing was left half done: the same count still confirms.
    response = await _confirm(client, count)
    assert response.status_code == 200, response.text[:500]
    assert len(await _entries(db, count)) == 3_000
