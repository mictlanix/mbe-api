"""Credit notes and refunds carry their customer's own name (#283, #284), as payments do (#270)."""

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.test_refunded_balance import _order_with_one_line, _refund


async def _confirmed_refund(client: AsyncClient, db: AsyncSession) -> int:
    """A refund of customer 1 ("Cliente Uno") confirmed as a credit note; returns its id."""
    order_id, line_id = await _order_with_one_line(db, quantity='2', price='100')
    await _refund(
        db, order_id=order_id, line_id=line_id, quantity='2', price='100', completed=False
    )
    listed = await client.get('/api/v1/customer-refunds', params={'sales_order': order_id})
    (row,) = listed.json()['items']
    opened = await client.post(
        '/api/v1/cash-sessions', json={'cash_drawer': 1, 'opening_amount': '0'}
    )
    assert opened.status_code == 201, opened.text
    confirmed = await client.post(
        f'/api/v1/customer-refunds/{row["customer_refund_id"]}/confirm',
        json={'payout': 'credit_note'},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()['customer_display_name'] == 'Cliente Uno'
    return row['customer_refund_id']


async def test_a_refund_carries_its_customers_name(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    refund_id = await _confirmed_refund(client, db)

    listed = await client.get('/api/v1/customer-refunds')
    read = await client.get(f'/api/v1/customer-refunds/{refund_id}')

    row = next(r for r in listed.json()['items'] if r['customer_refund_id'] == refund_id)
    assert row['customer_display_name'] == 'Cliente Uno'
    assert read.json()['customer_display_name'] == 'Cliente Uno'


async def test_a_credit_note_carries_its_customers_name(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    refund_id = await _confirmed_refund(client, db)

    listed = await client.get('/api/v1/credit-notes')
    (row,) = [n for n in listed.json()['items'] if n['customer_refund'] == refund_id]
    read = await client.get(f'/api/v1/credit-notes/{row["credit_note_id"]}')

    assert row['customer_display_name'] == 'Cliente Uno'
    assert read.json()['customer_display_name'] == 'Cliente Uno'
