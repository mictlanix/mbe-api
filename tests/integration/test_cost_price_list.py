"""The cost price list is not an ordinary price list, against a real schema (#194).

`COST_PRICE_LIST_ID` holds average cost. Only the cost snapshot on a sales-order line reads it,
and until #194 nothing else knew that: it was listed, faceted, deletable, editable and assignable
to a customer, exactly like Mostrador.

Integration rather than mocked because the rule is a seam across four services, and two writes
reach it by routes that bypass the obvious guard site — a retirement's `replacement`, which sets
`customer.price_list` without touching `update_customer`, and `PUT /product-prices/{id}`, whose
body never names a price list.

Three tiers, each pinned in both directions, because refusing too much passes every refusal test:

1. **Absent** from `/price-lists` on every verb, and from the listings a human picks a list from.
2. **Read-only** on `/product-prices`. Reads must keep working — the grid's "copy from the cost
   list" action consumes them and writes the sale column.
3. **Untouched** elsewhere: `GET /customers?price_list=` is a filter, and `delete_for_product` is
   the product-deletion cascade.
"""

from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import COST_PRICE_LIST_ID
from app.enums import EntityStatus
from app.models.customer import Customer
from app.models.product import PriceList, ProductPrice

#: The seed carries only list 1, so the cost list has to be created. Named rather than written as
#: `0` for the same reason the services name it: the literal says nothing about which row this is.
COST = COST_PRICE_LIST_ID


@pytest.fixture
async def cost_list(db: AsyncSession) -> int:
    """The cost list, with one product priced on it, as mbe_dev has."""
    db.add(
        PriceList(
            price_list_id=COST,
            name='Costo',
            high_profit_margin=Decimal('0'),
            low_profit_margin=Decimal('0'),
        )
    )
    await db.flush()
    db.add(
        ProductPrice(
            product=1,
            price_list=COST,
            price=Decimal('42'),
            low_profit=Decimal('0'),
            high_profit=Decimal('0'),
        )
    )
    await db.commit()
    return COST


def _customer_body(code: str, price_list: int) -> dict:
    return {
        'code': code,
        'name': f'Cliente {code}',
        'credit_limit': '0',
        'credit_days': 0,
        'price_list': price_list,
        'status': int(EntityStatus.ACTIVE),
    }


# ── Kept out of the listings a human picks from ───────────────────────────────


async def test_the_cost_list_is_not_in_the_price_list_listing(
    client: AsyncClient, seeded: None, cost_list: int
) -> None:
    """The listing behind mbe-ui's customer price-list dropdown. This is where the bad assignment
    came from: the list was offered, so it was picked."""
    listed = await client.get('/api/v1/price-lists')

    assert listed.status_code == 200, listed.text
    assert cost_list not in {row['price_list_id'] for row in listed.json()['items']}


async def test_the_listing_total_agrees_with_the_items_it_returns(
    client: AsyncClient, seeded: None, cost_list: int
) -> None:
    """`total` drives the pager, so excluding the row from `base` and not from `count_q` would
    promise a page that does not exist."""
    listed = await client.get('/api/v1/price-lists')

    body = listed.json()
    assert body['total'] == len(body['items']) == 1


async def test_a_search_does_not_reach_the_cost_list(
    client: AsyncClient, seeded: None, cost_list: int
) -> None:
    """Searching its name by hand is still picking from a listing."""
    found = await client.get('/api/v1/price-lists?search=Costo')

    assert found.status_code == 200, found.text
    assert found.json() == {'items': [], 'total': 0}


async def test_the_missing_price_facets_have_no_chip_for_the_cost_list(
    client: AsyncClient, seeded: None, cost_list: int
) -> None:
    """`Missing Costo (n)` is not work anyone does from a sale-price grid."""
    facets = await client.get('/api/v1/products/prices/missing-facets')

    assert facets.status_code == 200, facets.text
    assert cost_list not in {row['price_list'] for row in facets.json()}


# ── Not assignable to a customer ──────────────────────────────────────────────


async def test_creating_a_customer_on_the_cost_list_is_refused(
    client: AsyncClient, seeded: None, cost_list: int
) -> None:
    refused = await client.post('/api/v1/customers', json=_customer_body('CC1', cost_list))

    assert refused.status_code == 400, refused.text
    assert 'cost price list' in refused.json()['detail']


async def test_moving_an_existing_customer_onto_the_cost_list_is_refused(
    client: AsyncClient, db: AsyncSession, seeded: None, cost_list: int
) -> None:
    """The exact request that produced customer 11478 in mbe_dev."""
    refused = await client.put('/api/v1/customers/1', json={'price_list': cost_list})

    assert refused.status_code == 400, refused.text
    on_cost = await db.execute(
        select(func.count()).select_from(Customer).where(Customer.price_list == cost_list)
    )
    assert on_cost.scalar_one() == 0


async def test_a_customer_update_that_does_not_touch_the_price_list_still_works(
    client: AsyncClient, seeded: None, cost_list: int
) -> None:
    """The guard fires on the value sent, not on the request. A `PUT` editing the name of a
    customer must not be refused because the cost list exists."""
    updated = await client.put('/api/v1/customers/1', json={'name': 'Cliente Uno Editado'})

    assert updated.status_code == 200, updated.text
    assert updated.json()['name'] == 'Cliente Uno Editado'


# ── The row itself ────────────────────────────────────────────────────────────


async def test_deleting_the_cost_list_is_refused_and_its_prices_survive(
    client: AsyncClient, db: AsyncSession, seeded: None, cost_list: int
) -> None:
    """Deleting it takes every cost row with it, after which `add_line` snapshots `cost = 0` in
    silence — no error, just margin reporting that is wrong from then on.

    404, not the service's own 400: the route resolves the list before deleting, and the cost list
    does not resolve here. `delete_price_list` keeps its refusal for other callers.
    """
    refused = await client.delete(f'/api/v1/price-lists/{cost_list}')

    assert refused.status_code == 404, refused.text
    prices = await db.execute(
        select(func.count()).select_from(ProductPrice).where(ProductPrice.price_list == cost_list)
    )
    assert prices.scalar_one() == 1


async def test_retiring_a_list_onto_the_cost_list_is_refused(
    client: AsyncClient, db: AsyncSession, seeded: None, cost_list: int
) -> None:
    """`replacement` is `UPDATE customer SET price_list = <cost>` over every customer of the
    retired list — the same assignment `update_customer` refuses, at up to five figures of rows,
    and it never passes through `update_customer` to be caught there."""
    refused = await client.delete(f'/api/v1/price-lists/1?replacement={cost_list}')

    assert refused.status_code == 400, refused.text
    assert 'cannot be assigned to a customer' in refused.json()['detail']
    survivors = await db.execute(select(func.count()).select_from(PriceList))
    assert survivors.scalar_one() == 2
    moved = await db.execute(
        select(func.count()).select_from(Customer).where(Customer.price_list == cost_list)
    )
    assert moved.scalar_one() == 0


# ── Absent from the whole /price-lists resource ───────────────────────────────
#
# Not "listed but guarded": the row is not part of this resource at all, on every verb. One
# lookup enforces that, so these four tests are really one assertion about `get_price_list` --
# written out per route because the routes are what a client meets, and a fifth route added later
# should either inherit the rule or fail here.


@pytest.mark.parametrize(
    ('method', 'path', 'body'),
    [
        ('get', '', None),
        ('put', '', {'name': 'Renombrada'}),
        ('delete', '', None),
        ('get', '/delete/preview', None),
    ],
    ids=['read', 'rename', 'delete', 'delete-preview'],
)
async def test_no_verb_on_the_price_list_resource_reaches_the_cost_list(
    client: AsyncClient, seeded: None, cost_list: int, method: str, path: str, body: dict | None
) -> None:
    """The preview included: it would otherwise report 21,591 rows about to be touched by a
    delete that is refused. The rename too — harmless, but a blanket rule is easier to keep."""
    request = getattr(client, method)
    refused = await (
        request(f'/api/v1/price-lists/{cost_list}{path}', json=body)
        if body is not None
        else request(f'/api/v1/price-lists/{cost_list}{path}')
    )

    assert refused.status_code == 404, refused.text


async def test_an_ordinary_list_still_answers_every_one_of_those_verbs(
    client: AsyncClient, seeded: None, cost_list: int
) -> None:
    """The rejection is the cost list, not the routes. Without this, returning `None` from the
    lookup unconditionally would pass every test above."""
    assert (await client.get('/api/v1/price-lists/1')).status_code == 200
    assert (await client.get('/api/v1/price-lists/1/delete/preview')).status_code == 200
    assert (await client.put('/api/v1/price-lists/1', json={'name': 'General'})).status_code == 200


# ── Readable through /product-prices, and only readable ───────────────────────


async def test_cost_prices_are_still_readable(
    client: AsyncClient, seeded: None, cost_list: int
) -> None:
    """What "copy from the cost list" reads."""
    prices = await client.get(f'/api/v1/product-prices?price_list={cost_list}')

    assert prices.status_code == 200, prices.text
    assert [Decimal(row['price']) for row in prices.json()['items']] == [Decimal('42')]


@pytest.mark.parametrize(
    ('method', 'path', 'body'),
    [
        ('post', '', {'product': 1, 'price_list': None, 'price': '55'}),
        ('put', '', [{'product': 1, 'price_list': None, 'price': '55'}]),
        ('put', '/{id}', {'price': '55'}),
        ('delete', '/{id}', None),
    ],
    ids=['create', 'bulk-upsert', 'update', 'delete'],
)
async def test_no_write_reaches_a_cost_price(
    client: AsyncClient,
    db: AsyncSession,
    seeded: None,
    cost_list: int,
    method: str,
    path: str,
    body: object,
) -> None:
    """The two row-addressed verbs are the ones worth having here: `ProductPriceUpdate` carries
    no `price_list`, so only the row a request names makes it a cost write."""
    row = (
        await db.execute(select(ProductPrice).where(ProductPrice.price_list == cost_list))
    ).scalar_one()
    url = f'/api/v1/product-prices{path}'.replace('{id}', str(row.product_price_id))
    if isinstance(body, dict):
        body = {**body, 'price_list': cost_list} if 'price_list' in body else body
    elif isinstance(body, list):
        body = [{**item, 'price_list': cost_list} for item in body]

    request = getattr(client, method)
    refused = await (request(url, json=body) if body is not None else request(url))

    assert refused.status_code == 400, refused.text
    assert refused.json()['detail'] == 'The cost price list is read-only'


async def test_a_refused_bulk_body_writes_none_of_its_other_cells(
    client: AsyncClient, db: AsyncSession, seeded: None, cost_list: int
) -> None:
    """Checked in the same up-front pass as the duplicate check, so one cost cell refuses the
    whole body rather than letting the sale cells beside it land first."""
    refused = await client.put(
        '/api/v1/product-prices',
        json=[
            {'product': 1, 'price_list': 1, 'price': '999'},
            {'product': 1, 'price_list': cost_list, 'price': '55'},
        ],
    )

    assert refused.status_code == 400, refused.text
    sale = (await db.execute(select(ProductPrice).where(ProductPrice.price_list == 1))).scalar_one()
    assert sale.price == Decimal('100'), 'the sale cell was written despite the refusal'


async def test_an_ordinary_price_list_is_still_writable(
    client: AsyncClient, seeded: None, cost_list: int
) -> None:
    """The refusal is the cost list, not the endpoint. Without this, refusing every write would
    pass every test above."""
    upserted = await client.put(
        '/api/v1/product-prices',
        json=[{'product': 1, 'price_list': 1, 'price': '55'}],
    )

    assert upserted.status_code == 200, upserted.text
    # Compared as a number: the trailing scale on a decimal differs between SQLite here and
    # MariaDB in the deployment, and it is not what this test is about.
    assert Decimal(upserted.json()[0]['price']) == Decimal('55')


async def test_deleting_a_product_still_takes_its_cost_row_with_it(
    client: AsyncClient, db: AsyncSession, seeded: None, cost_list: int
) -> None:
    """`delete_for_product` is exempt: guarding it would make any product with a cost row
    undeletable, and skipping the row would orphan it behind an FK."""
    deleted = await client.delete('/api/v1/products/1')

    assert deleted.status_code == 204, deleted.text
    left = await db.execute(
        select(func.count()).select_from(ProductPrice).where(ProductPrice.price_list == cost_list)
    )
    assert left.scalar_one() == 0


async def test_customers_can_still_be_filtered_by_the_cost_list(
    client: AsyncClient, seeded: None, cost_list: int
) -> None:
    """A filter, not an assignment — and the query that finds a row like 11478."""
    found = await client.get(f'/api/v1/customers?price_list={cost_list}')

    assert found.status_code == 200, found.text
    assert found.json()['total'] == 0
