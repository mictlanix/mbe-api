"""Retiring a price list, against a real schema with its foreign keys enforced (#181).

The mocked tests read the statements a retirement issues; they cannot say whether those statements
leave the database in the state the contract promises. That is what these do — the prices are
actually gone, the customers are actually on the replacement, and a refused retirement has actually
changed nothing. Foreign keys are on here (`PRAGMA foreign_keys=ON`), so a cascade that misses a row
fails at the final delete rather than passing quietly.
"""

from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import EntityStatus
from app.models.customer import Customer
from app.models.product import PriceList, ProductPrice


async def _price_list(client: AsyncClient, name: str) -> int:
    created = await client.post(
        '/api/v1/price-lists',
        json={'name': name, 'high_profit_margin': '0.5', 'low_profit_margin': '0.1'},
    )
    assert created.status_code == 201, created.text
    return int(created.json()['price_list_id'])


async def _price(client: AsyncClient, product: int, price_list: int) -> None:
    created = await client.post(
        '/api/v1/product-prices',
        json={
            'product': product,
            'price_list': price_list,
            'price': '100',
            'low_profit': '0.1',
            'high_profit': '0.5',
        },
    )
    assert created.status_code == 201, created.text


async def _customer(db: AsyncSession, code: str, price_list: int) -> int:
    customer = Customer(
        code=code,
        name=f'Cliente {code}',
        credit_limit=Decimal('1000'),
        credit_days=30,
        price_list=price_list,
        shipping=False,
        shipping_required_document=False,
        status=EntityStatus.ACTIVE,
    )
    db.add(customer)
    await db.commit()
    return customer.customer_id


async def _count(db: AsyncSession, model: type, column: object, value: int) -> int:
    total = await db.scalar(select(func.count()).select_from(model).where(column == value))
    return int(total or 0)


# ── The prices go with the list (US1) ─────────────────────────────────────────


async def test_a_priced_list_is_retired_in_one_request(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    """GH #181's reproduction, verbatim: create a list, price a product in it, delete it. That
    third step used to be a 409 no client could clear without one request per priced product."""
    retired = await _price_list(client, 'Scratch')
    await _price(client, product=1, price_list=retired)
    assert await _count(db, ProductPrice, ProductPrice.price_list, retired) == 1

    deleted = await client.delete(f'/api/v1/price-lists/{retired}')

    assert deleted.status_code == 204, deleted.text
    assert await db.get(PriceList, retired) is None
    assert await _count(db, ProductPrice, ProductPrice.price_list, retired) == 0


async def test_retiring_a_list_leaves_every_other_lists_prices_alone(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    """The same product is priced in both lists. A cascade keyed on the product rather than the
    list — the mistake `delete_product` is one letter away from — would empty both."""
    retired = await _price_list(client, 'Scratch')
    kept = await _price_list(client, 'Kept')
    await _price(client, product=1, price_list=retired)
    await _price(client, product=1, price_list=kept)

    deleted = await client.delete(f'/api/v1/price-lists/{retired}')

    assert deleted.status_code == 204, deleted.text
    assert await _count(db, ProductPrice, ProductPrice.price_list, kept) == 1
    # The baseline list's own price for product 1 is untouched too.
    assert await _count(db, ProductPrice, ProductPrice.price_list, 1) == 1


# ── The customers move (US2) ──────────────────────────────────────────────────


async def test_the_lists_customers_move_to_the_named_replacement(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    retired = await _price_list(client, 'Scratch')
    replacement = await _price_list(client, 'Replacement')
    moved = [await _customer(db, f'C-{n}', retired) for n in range(3)]
    bystander = await _customer(db, 'C-OTHER', 1)

    deleted = await client.delete(f'/api/v1/price-lists/{retired}?replacement={replacement}')

    assert deleted.status_code == 204, deleted.text
    assert await db.get(PriceList, retired) is None
    for customer_id in moved:
        customer = await db.get(Customer, customer_id)
        await db.refresh(customer)
        assert customer.price_list == replacement
    await db.refresh(await db.get(Customer, bystander))
    assert (await db.get(Customer, bystander)).price_list == 1


async def test_customers_with_no_replacement_still_refuse_the_retirement(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    """FR-004: omitting the replacement preserves today's behaviour. The prices no longer appear
    in the refusal, which is the half #181 called unactionable; the customers still do."""
    retired = await _price_list(client, 'Scratch')
    await _price(client, product=1, price_list=retired)
    customer_id = await _customer(db, 'C-STAY', retired)

    refused = await client.delete(f'/api/v1/price-lists/{retired}')

    assert refused.status_code == 409, refused.text
    assert 'customer.price_list (1)' in refused.json()['detail']
    assert 'product_price' not in refused.json()['detail']
    # And nothing moved: the list, its price and the assignment are all still there.
    assert await db.get(PriceList, retired) is not None
    assert await _count(db, ProductPrice, ProductPrice.price_list, retired) == 1
    await db.refresh(await db.get(Customer, customer_id))
    assert (await db.get(Customer, customer_id)).price_list == retired


async def test_a_replacement_for_a_list_nobody_sits_on_moves_nobody(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    """Research R8: a client that always names a replacement is not punished for it."""
    retired = await _price_list(client, 'Scratch')
    replacement = await _price_list(client, 'Replacement')

    deleted = await client.delete(f'/api/v1/price-lists/{retired}?replacement={replacement}')

    assert deleted.status_code == 204, deleted.text
    assert await _count(db, Customer, Customer.price_list, replacement) == 0


async def test_a_refused_retirement_leaves_no_customer_moved(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    """FR-006, the part no mocked test can reach. The replacement is validated before the move,
    so the 404 arrives with nothing written — and the assignment is provably still where it was."""
    retired = await _price_list(client, 'Scratch')
    customer_id = await _customer(db, 'C-STAY', retired)

    missing = await client.delete(f'/api/v1/price-lists/{retired}?replacement=999999')
    assert (missing.status_code, missing.json()['detail']) == (
        404,
        'Replacement price list not found',
    )

    itself = await client.delete(f'/api/v1/price-lists/{retired}?replacement={retired}')
    assert (itself.status_code, itself.json()['detail']) == (
        400,
        'Cannot replace a price list with itself',
    )

    assert await db.get(PriceList, retired) is not None
    await db.refresh(await db.get(Customer, customer_id))
    assert (await db.get(Customer, customer_id)).price_list == retired


# ── The report (US3) ──────────────────────────────────────────────────────────


async def test_the_report_counts_what_the_retirement_then_acts_on(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    retired = await _price_list(client, 'Scratch')
    replacement = await _price_list(client, 'Replacement')
    await _price(client, product=1, price_list=retired)
    await _customer(db, 'C-MOVE', retired)

    report = await client.get(f'/api/v1/price-lists/{retired}/delete/preview')

    assert report.status_code == 200, report.text
    assert report.json() == {
        'items': [
            {'category': 'customer.price_list', 'count': 1},
            {'category': 'product_price.list', 'count': 1},
        ],
        'total': 2,
    }
    # Asking changed nothing, and the retirement acts on exactly those two kinds.
    assert await db.get(PriceList, retired) is not None
    deleted = await client.delete(f'/api/v1/price-lists/{retired}?replacement={replacement}')
    assert deleted.status_code == 204, deleted.text
    assert await _count(db, ProductPrice, ProductPrice.price_list, retired) == 0
    assert await _count(db, Customer, Customer.price_list, replacement) == 1


async def test_the_report_is_empty_for_a_list_nothing_references(
    client: AsyncClient, seeded: None
) -> None:
    retired = await _price_list(client, 'Scratch')

    report = await client.get(f'/api/v1/price-lists/{retired}/delete/preview')

    assert report.status_code == 200, report.text
    assert report.json() == {'items': [], 'total': 0}


async def test_the_report_is_refused_for_a_list_that_does_not_exist(
    client: AsyncClient, seeded: None
) -> None:
    report = await client.get('/api/v1/price-lists/999999/delete/preview')

    assert (report.status_code, report.json()['detail']) == (404, 'Price list not found')
