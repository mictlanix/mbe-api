"""A confirmed refund reports how it paid the customer back (#286), through its credit note alone:
a cash payout writes a note already paid back in cash, backed by the cash that left the drawer."""

from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sales import CreditNote, CustomerPayment
from app.services import cash_session_service
from tests.integration.inventory_seed import setup_inventory
from tests.integration.seed import seed_sales_order

URL = '/api/v1/customer-refunds'


@pytest.fixture
async def session(client: AsyncClient, db: AsyncSession) -> int:
    await setup_inventory(db)
    opened = await client.post(
        '/api/v1/cash-sessions', json={'cash_drawer': 1, 'opening_amount': '0'}
    )
    assert opened.status_code == 201, opened.text
    return opened.json()['cash_session_id']


async def _draft(client: AsyncClient, db: AsyncSession) -> int:
    sale = await seed_sales_order(db, completed=True, paid=True)
    raised = await client.post(URL, json={'sales_order': sale})
    assert raised.status_code == 201, raised.text
    refund_id = raised.json()['customer_refund_id']
    line_id = raised.json()['lines'][0]['customer_refund_detail_id']
    entered = await client.put(f'{URL}/{refund_id}/lines/{line_id}', json={'quantity': '1'})
    assert entered.status_code == 200, entered.text
    return refund_id


async def _confirm(client: AsyncClient, db: AsyncSession, payout: str) -> dict:
    refund_id = await _draft(client, db)
    confirmed = await client.post(f'{URL}/{refund_id}/confirm', json={'payout': payout})
    assert confirmed.status_code == 200, confirmed.text
    return confirmed.json()


async def test_a_cash_payout_is_a_note_already_paid_back(
    client: AsyncClient, db: AsyncSession, session: int
) -> None:
    refund = await _confirm(client, db, 'cash')
    total = Decimal(refund['total'])

    assert refund['payout'] == 'cash'
    payment = await db.get(CustomerPayment, refund['customer_payment'])
    assert (payment.amount, payment.cash_session) == (-total, session)
    note = (await client.get(f'/api/v1/credit-notes/{refund["credit_note"]}')).json()
    assert note['customer_refund'] == refund['customer_refund_id']
    assert note['customer_payment'] == refund['customer_payment']
    assert (Decimal(note['amount']), Decimal(note['refunded'])) == (total, total)
    assert (note['refunded_to_customer'], Decimal(note['remaining'])) == (True, 0)
    spendable = await client.get('/api/v1/credit-notes', params={'open_only': 'true'})
    assert refund['credit_note'] not in [n['credit_note_id'] for n in spendable.json()['items']]


async def test_a_cash_payout_still_leaves_the_drawer_once(
    client: AsyncClient, db: AsyncSession, session: int
) -> None:
    """The note adds no payment: the cut counts the one negative cash payment, as before."""
    refund = await _confirm(client, db, 'cash')

    figures = await cash_session_service.cut_figures(db, session)

    assert figures.cash_refunds == Decimal(refund['total'])
    payments = (
        (await db.execute(select(CustomerPayment).where(CustomerPayment.cash_session == session)))
        .scalars()
        .all()
    )
    assert [p.customer_payment_id for p in payments] == [refund['customer_payment']]


async def test_a_credit_note_payout_reports_its_note_and_ticket_number(
    client: AsyncClient, db: AsyncSession, session: int
) -> None:
    refund = await _confirm(client, db, 'credit_note')

    note = await db.get(CreditNote, refund['credit_note'])
    assert refund['payout'] == 'credit_note'
    assert note.customer_payment == refund['customer_payment']
    assert (note.cash_session, note.refunded) == (None, 0)
    listed = await client.get(URL)
    row = next(
        r for r in listed.json()['items'] if r['customer_refund_id'] == refund['customer_refund_id']
    )
    assert (row['payout'], row['credit_note'], row['customer_payment']) == (
        'credit_note',
        refund['credit_note'],
        refund['customer_payment'],
    )


async def test_a_draft_refund_has_no_payout(
    client: AsyncClient, db: AsyncSession, session: int
) -> None:
    refund_id = await _draft(client, db)

    read = (await client.get(f'{URL}/{refund_id}')).json()

    assert (read['payout'], read['customer_payment'], read['credit_note']) == (None, None, None)
