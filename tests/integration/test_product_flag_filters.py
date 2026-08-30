"""The six product flags filter, against a real schema (#188).

Three of the six were writable and unsearchable — a catalog manager could mark a product perishable
and then not ask which products expire. The mocked tests pin that the parameters reach the service
and the statement tests pin the SQL; neither can say that the rows coming back are the right rows.

Two things worth a real database rather than a compiled statement. Boolean columns here are MariaDB
`bit(1)` in production and SQLite integers in this schema, so "does `perishable=false` actually
match the stored `False`" is a round-trip question, not a syntax one. And the tri-state is only
observable against data: a filter that reads `False` as "not given" compiles identically and returns
the whole catalogue.
"""

from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import CurrencyCode, EntityStatus
from app.models.product import Product

#: The three flags #188 added, and the three that already worked — asserted together, because the
#: bug was an asymmetry between them rather than anything wrong with either group.
FLAGS = ('stockable', 'salable', 'purchasable', 'perishable', 'seriable', 'invoiceable')


async def _catalog(db: AsyncSession) -> None:
    """Two products differing in every flag, so each filter has exactly one match either way.

    Product 1 is the seeded baseline (every flag true except `perishable`/`seriable`); products 2
    and 3 are built here so that for every flag there is one product with it and one without.
    """
    for n, value in ((2, True), (3, False)):
        db.add(
            Product(
                product_id=n,
                code=f'P{n}',
                name=f'Producto {n}',
                photo=f'p{n}.png',
                unit_of_measurement='H87',
                tax_rate=Decimal('0.16'),
                tax_included=False,
                price_type=0,
                currency=CurrencyCode.MXN,
                min_order_qty=1,
                status=EntityStatus.ACTIVE,
                stock_verification=False,
                **dict.fromkeys(FLAGS, value),
            )
        )
    await db.commit()


async def _ids(client: AsyncClient, query: str) -> set[int]:
    r = await client.get(f'/api/v1/products?{query}&limit=100')
    assert r.status_code == 200, r.text
    return {row['product_id'] for row in r.json()['items']}


async def test_every_flag_filters_in_both_directions(client: AsyncClient, db: AsyncSession) -> None:
    """The heart of #188: all six, `true` and `false`, against rows that actually differ."""
    await _catalog(db)

    for flag in FLAGS:
        assert 2 in await _ids(client, f'{flag}=true'), flag
        assert 3 not in await _ids(client, f'{flag}=true'), flag
        assert 3 in await _ids(client, f'{flag}=false'), flag
        assert 2 not in await _ids(client, f'{flag}=false'), flag


async def test_omitting_a_flag_returns_both(client: AsyncClient, db: AsyncSession) -> None:
    """The tri-state, observable only against data: absent means unfiltered, not `false`."""
    await _catalog(db)

    everything = await _ids(client, 'status=0')

    assert {2, 3} <= everything


async def test_the_late_flags_compose_with_the_others(
    client: AsyncClient, db: AsyncSession
) -> None:
    """ "Perishable *and* salable" is the query the filter drawer sends with two chips on."""
    await _catalog(db)

    assert await _ids(client, 'perishable=true&salable=true') == {2}
    # Product 2 has every flag; asking for one it has and one it does not matches nothing.
    assert await _ids(client, 'perishable=true&salable=false') == set()


async def test_the_facets_narrow_with_the_late_flags(client: AsyncClient, db: AsyncSession) -> None:
    """The contract #188 asked to preserve: the chips count what the list shows.

    Verified by agreement rather than by a fixed number — the missing-price facet for a list is
    compared against the `total` of the same filter applied to the products list, so the two
    cannot drift apart without failing.
    """
    await _catalog(db)

    facets = await client.get('/api/v1/products/prices/missing-facets?perishable=true')
    assert facets.status_code == 200, facets.text

    for row in facets.json():
        listed = await client.get(
            f'/api/v1/products?perishable=true&missing_price_list={row["price_list"]}'
        )
        assert listed.json()['total'] == row['missing_count'], row['price_list']
