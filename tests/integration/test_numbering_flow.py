"""Spec 035, T017, T025 and T030: per-facility numbering and the stock check, through the API.

SQLite cannot show a race, so these pin the sequential behaviour the locks must preserve: each
confirmation takes its facility's next serial for its document type, a refused confirmation takes
none, two facilities number independently, and a sales order confirmed twice reserves its stock
once. The races themselves are for MariaDB (spec 035, SC-001 and SC-005).

Delivery orders and refunds take their facility from the sales order they are raised from, so a
second-facility document is one whose source order is moved there.

Customer payments (T030) are numbered the same way: the API's payments and the one payment a refund
writes take their facility's next serial, and the list's numeric search finds them by it.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import local_now
from app.enums import CurrencyCode, PaymentMethod, PaymentType, TransactionType
from app.models.customer import Customer
from app.models.inventory import LotSerialRqmt
from app.models.logistics import DeliveryOrder
from app.models.sales import CustomerPayment, CustomerRefund, SalesOrder, SalesQuote
from app.services import print_contexts, stock_ledger
from tests.integration.inventory_seed import Inventory, setup_inventory, stock
from tests.integration.seed import seed_sales_order

pytestmark = pytest.mark.usefixtures('seeded')

MODELS = {
    'sales order': SalesOrder,
    'sales quote': SalesQuote,
    'delivery order': DeliveryOrder,
    'customer refund': CustomerRefund,
}


@pytest.fixture
async def inventory(db: AsyncSession) -> Inventory:
    """Facility 1 (the baseline) and facility 2, each with a warehouse and a stockable product."""
    return await setup_inventory(db)


@pytest.fixture
async def cash_session(client: AsyncClient) -> None:
    """A refund needs the cashier's open session."""
    opened = await client.post(
        '/api/v1/cash-sessions', json={'cash_drawer': 1, 'opening_amount': '0'}
    )
    assert opened.status_code == 201, opened.text


async def _max_serial(db: AsyncSession, model: type, facility: int) -> int:
    query = select(func.max(model.serial)).where(model.facility == facility)
    return (await db.execute(query)).scalar_one_or_none() or 0


async def _draft_sales_order(
    client: AsyncClient,
    db: AsyncSession,
    *,
    facility: int = 1,
    product: int = 1,
    quantity: str = '1',
    price: str = '100',
) -> int:
    created = await client.post('/api/v1/sales-orders', json={'customer': 1, 'payment_terms': 0})
    assert created.status_code == 201, created.text
    order_id = created.json()['sales_order_id']
    lined = await client.post(
        f'/api/v1/sales-orders/{order_id}/lines',
        json={'product': product, 'quantity': quantity, 'warehouse': 1, 'price': price},
    )
    assert lined.status_code == 200, lined.text
    if facility != 1:
        await db.execute(
            update(SalesOrder)
            .where(SalesOrder.sales_order_id == order_id)
            .values(facility=facility)
        )
        await db.commit()
    return order_id


async def _draft_sales_quote(client: AsyncClient, db: AsyncSession, facility: int) -> int:
    created = await client.post('/api/v1/sales-quotes', json={'customer': 1, 'payment_terms': 0})
    assert created.status_code == 201, created.text
    quote_id = created.json()['sales_quote_id']
    lined = await client.post(
        f'/api/v1/sales-quotes/{quote_id}/lines', json={'product': 1, 'quantity': '1'}
    )
    assert lined.status_code == 200, lined.text
    await db.execute(
        update(SalesQuote).where(SalesQuote.sales_quote_id == quote_id).values(facility=facility)
    )
    await db.commit()
    return quote_id


async def _completed_sale(db: AsyncSession, facility: int) -> int:
    """A paid, completed sale of `facility`, the source of a delivery order or a refund."""
    order_id = await seed_sales_order(db, completed=True, paid=True)
    await db.execute(
        update(SalesOrder).where(SalesOrder.sales_order_id == order_id).values(facility=facility)
    )
    await db.commit()
    return order_id


async def _confirm(
    kind: str, client: AsyncClient, db: AsyncSession, facility: int, *, payout: str = 'cash'
) -> int:
    """Draft a document of `kind` in `facility`, confirm it and return the serial it got.

    `payout` is how a refund is paid back, `cash` or `credit_note`; other documents ignore it.
    """
    if kind == 'sales order':
        order_id = await _draft_sales_order(client, db, facility=facility)
        response = await client.post(f'/api/v1/sales-orders/{order_id}/confirm')
    elif kind == 'sales quote':
        quote_id = await _draft_sales_quote(client, db, facility)
        response = await client.post(f'/api/v1/sales-quotes/{quote_id}/confirm')
    elif kind == 'delivery order':
        sale = await _completed_sale(db, facility)
        raised = await client.post('/api/v1/delivery-orders', json={'sales_order': sale})
        assert raised.status_code == 201, raised.text
        delivery_id = raised.json()['delivery_order_id']
        response = await client.post(f'/api/v1/delivery-orders/{delivery_id}/confirm')
    else:
        sale = await _completed_sale(db, facility)
        raised = await client.post('/api/v1/customer-refunds', json={'sales_order': sale})
        assert raised.status_code == 201, raised.text
        refund_id = raised.json()['customer_refund_id']
        line_id = raised.json()['lines'][0]['customer_refund_detail_id']
        entered = await client.put(
            f'/api/v1/customer-refunds/{refund_id}/lines/{line_id}', json={'quantity': '1'}
        )
        assert entered.status_code == 200, entered.text
        response = await client.post(
            f'/api/v1/customer-refunds/{refund_id}/confirm', json={'payout': payout}
        )
    assert response.status_code == 200, response.text
    return response.json()['serial']


@pytest.mark.parametrize('kind', MODELS)
async def test_three_confirmations_take_the_next_three_serials(
    kind: str, client: AsyncClient, db: AsyncSession, inventory: Inventory, cash_session: None
) -> None:
    before = await _max_serial(db, MODELS[kind], 1)

    serials = [await _confirm(kind, client, db, 1) for _ in range(3)]

    assert serials == [before + 1, before + 2, before + 3]


@pytest.mark.parametrize('kind', MODELS)
async def test_a_second_facility_numbers_independently(
    kind: str, client: AsyncClient, db: AsyncSession, inventory: Inventory, cash_session: None
) -> None:
    model = MODELS[kind]
    a, b = inventory.a.facility, inventory.b.facility
    a_before, b_before = await _max_serial(db, model, a), await _max_serial(db, model, b)

    first_a = await _confirm(kind, client, db, a)
    first_b = await _confirm(kind, client, db, b)
    second_a = await _confirm(kind, client, db, a)

    assert (first_a, second_a) == (a_before + 1, a_before + 2)
    assert first_b == b_before + 1
    assert await _max_serial(db, model, b) == b_before + 1


async def test_a_refused_confirmation_takes_no_serial(
    client: AsyncClient, db: AsyncSession, inventory: Inventory
) -> None:
    before = await _max_serial(db, SalesOrder, 1)
    first = await _confirm('sales order', client, db, 1)

    zero = await _draft_sales_order(client, db, price='0')
    unpriced = await client.post(f'/api/v1/sales-orders/{zero}/confirm')
    short = await _draft_sales_order(client, db, product=inventory.stockable, quantity='3')
    unstocked = await client.post(f'/api/v1/sales-orders/{short}/confirm')

    assert unpriced.status_code == 409, unpriced.text
    assert unpriced.json()['detail']['message'] == 'Order has lines priced at zero'
    assert unstocked.status_code == 409, unstocked.text
    assert unstocked.json()['detail']['message'] == 'Insufficient stock'
    assert await _max_serial(db, SalesOrder, 1) == first == before + 1
    assert await _confirm('sales order', client, db, 1) == before + 2


# ── Stock under confirmation (T025) ───────────────────────────────────────────


async def _reservations(db: AsyncSession, order_id: int) -> int:
    query = select(func.count()).where(
        LotSerialRqmt.source == int(TransactionType.SALES_ORDER_RESERVATION),
        LotSerialRqmt.reference == order_id,
    )
    return (await db.execute(query)).scalar_one()


async def test_a_second_order_for_the_same_five_units_is_refused(
    client: AsyncClient, db: AsyncSession, inventory: Inventory
) -> None:
    product = inventory.stockable
    await stock(db, product, 1, '5')
    first = await _draft_sales_order(client, db, product=product, quantity='5')
    second = await _draft_sales_order(client, db, product=product, quantity='5')

    confirmed = await client.post(f'/api/v1/sales-orders/{first}/confirm')
    refused = await client.post(f'/api/v1/sales-orders/{second}/confirm')

    assert confirmed.status_code == 200, confirmed.text
    assert refused.status_code == 409, refused.text
    detail = refused.json()['detail']
    assert detail['message'] == 'Insufficient stock'
    assert detail['lines']
    assert await _reservations(db, second) == 0
    assert await _reservations(db, first) == 1
    assert await stock_ledger.available(db, product=product, warehouse=1) == 0
    drafted = await client.get(f'/api/v1/sales-orders/{second}')
    assert drafted.json()['serial'] is None


async def test_confirming_a_confirmed_order_changes_nothing(
    client: AsyncClient, db: AsyncSession, inventory: Inventory
) -> None:
    product = inventory.stockable
    await stock(db, product, 1, '5')
    order_id = await _draft_sales_order(client, db, product=product, quantity='5')
    first = await client.post(f'/api/v1/sales-orders/{order_id}/confirm')
    assert first.status_code == 200, first.text

    again = await client.post(f'/api/v1/sales-orders/{order_id}/confirm')

    assert again.status_code == 409, again.text
    assert again.json()['detail'] == 'Document is completed and can no longer be edited'
    read = await client.get(f'/api/v1/sales-orders/{order_id}')
    assert read.json()['serial'] == first.json()['serial']
    assert await _max_serial(db, SalesOrder, 1) == first.json()['serial']
    assert await _reservations(db, order_id) == 1


async def test_a_line_cannot_be_added_to_a_confirmed_order(
    client: AsyncClient, db: AsyncSession, inventory: Inventory
) -> None:
    order_id = await _draft_sales_order(client, db)
    confirmed = await client.post(f'/api/v1/sales-orders/{order_id}/confirm')
    assert confirmed.status_code == 200, confirmed.text

    added = await client.post(
        f'/api/v1/sales-orders/{order_id}/lines', json={'product': 1, 'quantity': '1'}
    )

    assert added.status_code == 409, added.text
    assert added.json()['detail'] == 'Document is completed and can no longer be edited'
    read = await client.get(f'/api/v1/sales-orders/{order_id}')
    assert len(read.json()['lines']) == 1


# ── Customer payments (T030) ──────────────────────────────────────────────────


async def _plain_payment(db: AsyncSession, facility: int, serial: int, customer: int = 1) -> int:
    """A payment already in `facility`'s sequence at `serial`, written straight to the table."""
    now = local_now()
    payment = CustomerPayment(
        customer=customer,
        amount='10',
        method=int(PaymentMethod.CASH),
        commission=None,
        payment_charge=None,
        date=now,
        cash_session=None,
        reference=None,
        facility=facility,
        serial=serial,
        creator=1,
        updater=1,
        verifier=None,
        creation_time=now,
        modification_time=now,
        currency=CurrencyCode.MXN,
        payment_type=int(PaymentType.IMMEDIATE),
    )
    db.add(payment)
    await db.commit()
    return payment.customer_payment_id


async def _post_payment(
    client: AsyncClient, *, payment_type: PaymentType = PaymentType.IMMEDIATE
) -> dict:
    response = await client.post(
        '/api/v1/customer-payments',
        json={
            'customer': 1,
            'amount': '50',
            'method': PaymentMethod.CASH,
            'payment_type': payment_type,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _refund_payment_serial(db: AsyncSession, facility: int, refund_serial: int) -> int:
    """The serial of the one payment the refund `refund_serial` of `facility` wrote."""
    refund_id = (
        await db.execute(
            select(CustomerRefund.customer_refund_id).where(
                CustomerRefund.facility == facility, CustomerRefund.serial == refund_serial
            )
        )
    ).scalar_one()
    query = select(CustomerPayment.serial).where(CustomerPayment.reference == f'Refund {refund_id}')
    return (await db.execute(query)).scalar_one()


async def test_two_payments_take_the_facilitys_next_two_serials(
    client: AsyncClient, db: AsyncSession, inventory: Inventory
) -> None:
    await _plain_payment(db, 1, 41)
    await _plain_payment(db, inventory.b.facility, 900)
    before = await _max_serial(db, CustomerPayment, 1)

    first = await _post_payment(client)
    second = await _post_payment(client)

    assert before >= 41
    assert (first['serial'], second['serial']) == (before + 1, before + 2)
    assert await _max_serial(db, CustomerPayment, 1) == before + 2
    assert await _max_serial(db, CustomerPayment, inventory.b.facility) == 900


async def test_the_payment_ticket_prints_the_serial_as_eight_digits(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _plain_payment(db, 1, 41)
    before = await _max_serial(db, CustomerPayment, 1)

    created = await _post_payment(client, payment_type=PaymentType.PAYMENT_IN_ADVANCE)
    payment = await db.get(CustomerPayment, created['customer_payment_id'])
    _, context = await print_contexts.payment_ticket_context(db, payment)

    assert created['serial'] == before + 1
    assert context['folio'] == f'{before + 1:08d}'
    assert len(context['folio']) == 8


async def test_a_refund_payout_is_numbered_after_a_plain_payment(
    client: AsyncClient, db: AsyncSession, inventory: Inventory, cash_session: None
) -> None:
    before = await _max_serial(db, CustomerPayment, 1)
    plain = await _post_payment(client)

    cash = await _confirm('customer refund', client, db, 1, payout='cash')
    credit = await _confirm('customer refund', client, db, 1, payout='credit_note')

    assert plain['serial'] == before + 1
    assert await _refund_payment_serial(db, 1, cash) == before + 2
    assert await _refund_payment_serial(db, 1, credit) == before + 3


@pytest.mark.parametrize('payout', ['cash', 'credit_note'])
async def test_a_refund_payout_takes_the_refund_facilitys_sequence(
    payout: str, client: AsyncClient, db: AsyncSession, inventory: Inventory, cash_session: None
) -> None:
    b = inventory.b.facility
    await _plain_payment(db, b, 7)
    facility_one = await _max_serial(db, CustomerPayment, 1)

    refund = await _confirm('customer refund', client, db, b, payout=payout)

    assert await _refund_payment_serial(db, b, refund) == 8
    assert await _max_serial(db, CustomerPayment, b) == 8
    assert await _max_serial(db, CustomerPayment, 1) == facility_one


# ── The numeric search finds a payment by its serial ──────────────────────────


async def _search(client: AsyncClient, search: str) -> list[int]:
    response = await client.get('/api/v1/customer-payments', params={'search': search})
    assert response.status_code == 200, response.text
    return [item['customer_payment_id'] for item in response.json()['items']]


async def test_a_numeric_search_matches_the_payments_id_or_serial(
    client: AsyncClient, db: AsyncSession, inventory: Inventory
) -> None:
    mine = await _plain_payment(db, 1, 9001)
    other_facility = await _plain_payment(db, inventory.b.facility, 9001)

    by_id = await _search(client, str(mine))
    by_serial = await _search(client, '9001')

    assert mine in by_id
    assert other_facility not in by_id
    assert mine in by_serial
    assert other_facility not in by_serial


async def test_a_numeric_search_does_not_match_the_customers_name(
    client: AsyncClient, db: AsyncSession
) -> None:
    mine = await _plain_payment(db, 1, 9002)

    assert mine not in await _search(client, '9003')


async def test_a_text_search_still_matches_the_customers_name(
    client: AsyncClient, db: AsyncSession
) -> None:
    mine = await _plain_payment(db, 1, 9004)
    name = (await db.execute(select(Customer.name).where(Customer.customer_id == 1))).scalar_one()

    assert mine in await _search(client, name[:4])
    assert await _search(client, 'no-such-customer-name') == []
