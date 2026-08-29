"""The three pricing-grid gaps, against a real schema (#182, #183, #184).

The mocked tests pin the endpoint contracts and the statements each service issues; none of them
can say whether a page of the grid actually reads back, whether a column action that half-fails
leaves anything behind, or whether the worklist counts match the rows the filter returns. That is
what these do.

The grid is the shape under test throughout: several products, several price lists, some cells
priced and some not — which is also the arrangement in which an off-by-one between "missing" and
"priced" is visible rather than hidden by every cell being filled.
"""

from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import CurrencyCode, EntityStatus
from app.models.product import PriceList, Product, ProductPrice


async def _grid(db: AsyncSession) -> tuple[list[int], list[int]]:
    """Three products and three lists, with only product 1 priced on list 1 (from the baseline).

    Returns `(product_ids, price_list_ids)`, both including the seeded row at id 1, so a test can
    talk about "the second product" without knowing how the baseline is built.
    """
    products = [1]
    for n in (2, 3):
        db.add(
            Product(
                product_id=n,
                code=f'P{n}',
                name=f'Producto {n}',
                photo=f'p{n}.png',
                unit_of_measurement='H87',
                stockable=True,
                perishable=False,
                seriable=False,
                purchasable=True,
                salable=n == 2,
                invoiceable=True,
                tax_rate=Decimal('0.16'),
                tax_included=False,
                price_type=0,
                currency=CurrencyCode.MXN,
                min_order_qty=1,
                status=EntityStatus.ACTIVE,
                stock_verification=False,
            )
        )
        products.append(n)

    lists = [1]
    for n in (2, 3):
        db.add(
            PriceList(
                price_list_id=n,
                name=f'Lista {n}',
                high_profit_margin=Decimal('0.5'),
                low_profit_margin=Decimal('0.1'),
            )
        )
        lists.append(n)

    await db.commit()
    return products, lists


# ── #182: reading a page of the grid ──────────────────────────────────────────


async def test_one_call_reads_every_price_for_a_page_of_products(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The gap #182 was filed for: a page of products used to cost one request per row."""
    products, lists = await _grid(db)
    body = [
        {'product': p, 'price_list': pl, 'price': f'{p * 10 + pl}'}
        for p in products
        for pl in lists
    ]
    assert (await client.put('/api/v1/product-prices', json=body)).status_code == 200

    query = '&'.join(f'product={p}' for p in products)
    read = await client.get(f'/api/v1/product-prices?{query}&limit=100')

    assert read.status_code == 200, read.text
    payload = read.json()
    assert payload['total'] == len(products) * len(lists)
    assert {(row['product'], row['price_list']['price_list_id']) for row in payload['items']} == {
        (p, pl) for p in products for pl in lists
    }


async def test_repeating_product_composes_with_price_list(
    client: AsyncClient, db: AsyncSession
) -> None:
    """One column of the grid: several products, one list."""
    products, lists = await _grid(db)
    body = [{'product': p, 'price_list': pl, 'price': '5'} for p in products for pl in lists]
    await client.put('/api/v1/product-prices', json=body)

    query = '&'.join(f'product={p}' for p in products)
    read = await client.get(f'/api/v1/product-prices?{query}&price_list=2')

    assert read.json()['total'] == len(products)
    assert {row['price_list']['price_list_id'] for row in read.json()['items']} == {2}


async def test_a_single_product_still_reads_the_way_it_did(
    client: AsyncClient, db: AsyncSession
) -> None:
    """`?product=1` is the shape every existing client sends; repeating it must not break that."""
    await _grid(db)
    read = await client.get('/api/v1/product-prices?product=1')

    assert read.status_code == 200
    assert read.json()['total'] == 1
    assert read.json()['items'][0]['product'] == 1


# ── #183: writing a column in one request ─────────────────────────────────────


async def test_bulk_upsert_creates_and_updates_in_one_call(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The mixed case is the point: the client no longer picks POST against PUT per cell."""
    await _grid(db)

    written = await client.put(
        '/api/v1/product-prices',
        json=[
            {'product': 1, 'price_list': 1, 'price': '999'},  # exists (seeded at 100)
            {'product': 2, 'price_list': 1, 'price': '250'},  # does not
        ],
    )

    assert written.status_code == 200, written.text
    # Compared as numbers, not strings: the response echoes the values the request set, so they
    # carry the scale the client sent rather than the column's `Numeric(18, 4)`. Only the single-row
    # `POST` re-reads its row, and re-reading a page of 500 to normalise the formatting would cost
    # a query each — the number is the same either way.
    assert [Decimal(row['price']) for row in written.json()] == [Decimal('999'), Decimal('250')]
    stored = (
        await db.execute(
            select(ProductPrice.product, ProductPrice.price)
            .where(ProductPrice.price_list == 1)
            .order_by(ProductPrice.product)
        )
    ).all()
    assert [(p, str(price)) for p, price in stored] == [(1, '999.0000'), (2, '250.0000')]


async def test_a_created_row_takes_the_price_lists_margins(
    client: AsyncClient, db: AsyncSession
) -> None:
    """#183's second blocker: the grid edits a price and must not invent two more numbers."""
    await _grid(db)

    written = await client.put(
        '/api/v1/product-prices', json=[{'product': 2, 'price_list': 2, 'price': '10'}]
    )

    assert written.status_code == 200, written.text
    created = (
        await db.execute(
            select(ProductPrice).where(ProductPrice.product == 2, ProductPrice.price_list == 2)
        )
    ).scalar_one()
    assert created.low_profit == Decimal('0.1')
    assert created.high_profit == Decimal('0.5')


async def test_margins_given_on_an_update_are_honoured_and_omitted_ones_left_alone(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Deprecated does not mean ignored — a client still sending them is still obeyed."""
    await _grid(db)

    await client.put(
        '/api/v1/product-prices',
        json=[{'product': 1, 'price_list': 1, 'price': '7', 'low_profit': '0.25'}],
    )

    row = (
        await db.execute(
            select(ProductPrice).where(ProductPrice.product == 1, ProductPrice.price_list == 1)
        )
    ).scalar_one()
    assert row.low_profit == Decimal('0.25')
    assert row.high_profit == Decimal('0.5')  # untouched, not reset to the list's


async def test_a_bad_id_anywhere_in_the_body_writes_nothing(
    client: AsyncClient, db: AsyncSession
) -> None:
    """All-or-nothing is the whole reason the endpoint exists — the half-failure #183 named."""
    await _grid(db)

    refused = await client.put(
        '/api/v1/product-prices',
        json=[
            {'product': 2, 'price_list': 1, 'price': '250'},
            {'product': 9999, 'price_list': 1, 'price': '250'},
        ],
    )

    assert refused.status_code == 404
    assert '9999' in refused.json()['detail']
    assert (
        await db.execute(
            select(func.count()).select_from(ProductPrice).where(ProductPrice.product == 2)
        )
    ).scalar_one() == 0


async def test_a_repeated_cell_is_refused_rather_than_letting_the_last_one_win(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _grid(db)

    refused = await client.put(
        '/api/v1/product-prices',
        json=[
            {'product': 1, 'price_list': 1, 'price': '1'},
            {'product': 1, 'price_list': 1, 'price': '2'},
        ],
    )

    assert refused.status_code == 400
    row = (
        await db.execute(
            select(ProductPrice).where(ProductPrice.product == 1, ProductPrice.price_list == 1)
        )
    ).scalar_one()
    assert row.price == Decimal('100')


async def test_an_empty_body_is_refused(client: AsyncClient, db: AsyncSession) -> None:
    await _grid(db)
    assert (await client.put('/api/v1/product-prices', json=[])).status_code == 422


# ── #184: the worklist ────────────────────────────────────────────────────────


async def test_missing_price_list_returns_exactly_the_unpriced_products(
    client: AsyncClient, db: AsyncSession
) -> None:
    products, _ = await _grid(db)

    unpriced = await client.get('/api/v1/products?missing_price_list=1')

    assert unpriced.status_code == 200, unpriced.text
    assert unpriced.json()['total'] == len(products) - 1  # product 1 is priced on list 1
    assert {row['product_id'] for row in unpriced.json()['items']} == {2, 3}


async def test_missing_price_list_composes_with_the_other_filters(
    client: AsyncClient, db: AsyncSession
) -> None:
    """ "Unpriced *and* salable" is the query #184 asked to be expressible."""
    await _grid(db)

    both = await client.get('/api/v1/products?missing_price_list=1&salable=true')

    # Product 3 is the one seeded unsalable, so it drops out and only product 2 remains.
    assert {row['product_id'] for row in both.json()['items']} == {2}
    assert both.json()['total'] == 1


async def test_a_list_nobody_has_priced_reports_every_product_missing(
    client: AsyncClient, db: AsyncSession
) -> None:
    products, _ = await _grid(db)

    unpriced = await client.get('/api/v1/products?missing_price_list=3')

    assert unpriced.json()['total'] == len(products)


async def test_the_facets_agree_with_the_filter_they_summarise(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The chip count and the grid behind it must be the same number, or the chip lies."""
    _, lists = await _grid(db)
    await client.put(
        '/api/v1/product-prices', json=[{'product': 2, 'price_list': 2, 'price': '10'}]
    )

    facets = await client.get('/api/v1/products/prices/missing-facets')

    assert facets.status_code == 200, facets.text
    counts = {row['price_list']: row['missing_count'] for row in facets.json()}
    assert set(counts) == set(lists)  # every list, including ones with nothing missing
    for price_list, missing in counts.items():
        filtered = await client.get(f'/api/v1/products?missing_price_list={price_list}')
        assert filtered.json()['total'] == missing, price_list


async def test_the_facets_narrow_with_the_product_filters(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _grid(db)

    facets = await client.get('/api/v1/products/prices/missing-facets?salable=true')

    counts = {row['price_list']: row['missing_count'] for row in facets.json()}
    # Products 1 and 2 are salable; 1 is priced on list 1, so one is missing there and both on
    # the lists nobody has priced.
    assert counts == {1: 1, 2: 2, 3: 2}
