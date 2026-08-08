"""Money in and money back, driven through the API against a real database.

The payment services do the arithmetic that mocked tests can only assert *about*: an application
reduces an order's balance, a reversal restores it, and a refund is refused until the sale is fully
paid. Each of those is a read of rows the previous request wrote, which is exactly what a mock
cannot represent.
"""

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import PaymentMethod
from tests.integration.seed import seed_sales_order


async def _completed_order_with_total(client: AsyncClient, db: AsyncSession) -> tuple[int, str]:
    """A confirmed sale owing 1,160.00 — ten at 100 plus 16% — and its id."""
    order_id = await seed_sales_order(db, completed=True)
    read = await client.get(f'/api/v1/sales-orders/{order_id}')
    assert read.status_code == 200, read.text
    return order_id, read.json()['total']


async def test_a_payment_is_taken_and_applied_to_an_order(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    order_id, total = await _completed_order_with_total(client, db)

    payment = await client.post(
        '/api/v1/customer-payments',
        json={'customer': 1, 'amount': total, 'method': PaymentMethod.CASH},
    )
    assert payment.status_code == 201, payment.text
    payment_id = payment.json()['customer_payment_id']

    applied = await client.post(
        f'/api/v1/customer-payments/{payment_id}/applications',
        json={'sales_order': order_id, 'amount': total},
    )
    assert applied.status_code == 201, applied.text

    # The balance is derived from the applications, so this reads what the write left behind.
    order = await client.get(f'/api/v1/sales-orders/{order_id}')
    assert order.json()['balance'] == '0.00'
    assert order.json()['status'] == 'paid'


async def test_the_order_side_lists_what_was_applied(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    """#134 — the reverse direction of the same link, joined in one query."""
    order_id, total = await _completed_order_with_total(client, db)
    payment = await client.post(
        '/api/v1/customer-payments',
        json={'customer': 1, 'amount': total, 'method': PaymentMethod.CASH, 'reference': 'R-1'},
    )
    payment_id = payment.json()['customer_payment_id']
    await client.post(
        f'/api/v1/customer-payments/{payment_id}/applications',
        json={'sales_order': order_id, 'amount': total},
    )

    listed = await client.get(f'/api/v1/sales-orders/{order_id}/payments')

    assert listed.status_code == 200, listed.text
    row = listed.json()[0]
    assert row['customer_payment'] == payment_id
    # Flattened from the payment, which means the join ran.
    assert row['reference'] == 'R-1'
    assert row['cancelled'] is False


async def test_reversing_an_application_restores_the_balance(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    """A reversal stays visible rather than deleting the row, so the audit trail survives."""
    order_id, total = await _completed_order_with_total(client, db)
    payment = await client.post(
        '/api/v1/customer-payments',
        json={'customer': 1, 'amount': total, 'method': PaymentMethod.CASH},
    )
    payment_id = payment.json()['customer_payment_id']
    applied = await client.post(
        f'/api/v1/customer-payments/{payment_id}/applications',
        json={'sales_order': order_id, 'amount': total},
    )
    application_id = applied.json()['sales_order_payment_id']

    reversed_ = await client.post(
        f'/api/v1/customer-payments/{payment_id}/applications/{application_id}/reverse',
        json={'reason': 'Cobro duplicado'},
    )

    assert reversed_.status_code == 200, reversed_.text
    order = await client.get(f'/api/v1/sales-orders/{order_id}')
    assert order.json()['balance'] == total
    # Still listed, marked cancelled — not removed.
    listed = await client.get(f'/api/v1/sales-orders/{order_id}/payments')
    assert [row['cancelled'] for row in listed.json()] == [True]


async def test_applying_more_than_the_payment_holds_is_refused(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    """`assert_within_unapplied` — the guard `apply_payment` actually has."""
    order_id, _ = await _completed_order_with_total(client, db)
    payment = await client.post(
        '/api/v1/customer-payments',
        json={'customer': 1, 'amount': '100', 'method': PaymentMethod.CASH},
    )
    payment_id = payment.json()['customer_payment_id']

    response = await client.post(
        f'/api/v1/customer-payments/{payment_id}/applications',
        json={'sales_order': order_id, 'amount': '500'},
    )

    assert response.status_code == 422, response.text
    assert 'unapplied' in response.json()['detail']


async def test_an_application_is_not_capped_at_what_the_order_owes(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    """Recording current behaviour, not endorsing it.

    `apply_payment` checks that an application fits inside the *payment's* unapplied balance;
    nothing checks it against what the *order* still owes. So a payment larger than the sale can be
    applied whole, and the order's balance is whatever `totals.remaining` makes of the excess.
    Pinned because it is easy to change by accident, and because a reader of
    `assert_within_unapplied` would reasonably assume the other bound exists too. Whether
    overpayment *should* be refused is a product question — `amount_change` suggests cash change was
    the intent — and this test is not the place to decide it.
    """
    order_id, total = await _completed_order_with_total(client, db)
    payment = await client.post(
        '/api/v1/customer-payments',
        json={'customer': 1, 'amount': '99999', 'method': PaymentMethod.CASH},
    )
    payment_id = payment.json()['customer_payment_id']

    applied = await client.post(
        f'/api/v1/customer-payments/{payment_id}/applications',
        json={'sales_order': order_id, 'amount': '99999'},
    )

    assert applied.status_code == 201, applied.text
    order = await client.get(f'/api/v1/sales-orders/{order_id}')
    assert order.json()['status'] == 'paid'
    # Not negative: `remaining` floors at zero, so the excess is simply not reported here.
    assert order.json()['balance'] == '0.00'
    assert float(total) < 99999


async def test_an_unpaid_order_is_listed_as_outstanding(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    """The search a cashier uses to find what a customer owes, over real rows."""
    order_id, _ = await _completed_order_with_total(client, db)

    response = await client.get(
        '/api/v1/customer-payments/outstanding-orders', params={'customer': 1}
    )

    assert response.status_code == 200, response.text
    assert order_id in [row['sales_order_id'] for row in response.json()['items']]


async def test_a_cash_session_opens_reports_itself_and_closes(
    client: AsyncClient, seeded: None
) -> None:
    """Opening is refused twice over for the same till, which needs the first row to exist."""
    opened = await client.post(
        '/api/v1/cash-sessions', json={'cash_drawer': 1, 'opening_amount': '500'}
    )
    assert opened.status_code == 201, opened.text
    session_id = opened.json()['cash_session_id']

    current = await client.get('/api/v1/cash-sessions/current')
    assert current.status_code == 200, current.text
    # A state plus an optional session: no open till is an answer, not a 404.
    assert current.json()['session']['cash_session_id'] == session_id

    again = await client.post(
        '/api/v1/cash-sessions', json={'cash_drawer': 1, 'opening_amount': '500'}
    )
    assert again.status_code == 409, again.text

    closed = await client.post(
        f'/api/v1/cash-sessions/{session_id}/close',
        json={'counts': [{'denomination': '100', 'quantity': 5}]},
    )
    assert closed.status_code == 200, closed.text
    # `end` is what marks it closed; the counted total lives on the cash_count rows.
    assert closed.json()['end'] is not None

    # And the till is free again, which only holds if the close was written.
    reopened = await client.post(
        '/api/v1/cash-sessions', json={'cash_drawer': 1, 'opening_amount': '500'}
    )
    assert reopened.status_code == 201, reopened.text


async def test_a_refund_is_refused_until_the_sale_is_paid(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    """FR-060 — refundable means paid; a completed unpaid sale is unwound by cancelling instead."""
    order_id = await seed_sales_order(db, completed=True, paid=False)

    response = await client.post('/api/v1/customer-refunds', json={'sales_order': order_id})

    assert response.status_code == 409, response.text
    assert 'paid' in response.json()['detail']


async def test_a_paid_sale_is_refunded_as_a_credit_note(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    """Sale, payment, application, refund, credit note — each step reading what the last wrote."""
    order_id, total = await _completed_order_with_total(client, db)
    payment = await client.post(
        '/api/v1/customer-payments',
        json={'customer': 1, 'amount': total, 'method': PaymentMethod.CASH},
    )
    payment_id = payment.json()['customer_payment_id']
    await client.post(
        f'/api/v1/customer-payments/{payment_id}/applications',
        json={'sales_order': order_id, 'amount': total},
    )

    refund = await client.post('/api/v1/customer-refunds', json={'sales_order': order_id})
    assert refund.status_code == 201, refund.text
    refund_id = refund.json()['customer_refund_id']
    # A draft opens at zero on every line, carrying what each one *could* be refunded.
    line = refund.json()['lines'][0]
    assert (line['quantity'], line['refundable_quantity']) == ('0.0000', '10.0000')

    returned = await client.put(
        f'/api/v1/customer-refunds/{refund_id}/lines/{line["customer_refund_detail_id"]}',
        json={'quantity': '10'},
    )
    assert returned.status_code == 200, returned.text
    assert returned.json()['total'] == total

    # Confirming pays money out, so it needs an open till — a cross-resource precondition that
    # only shows up once the rows are real.
    refused = await client.post(
        f'/api/v1/customer-refunds/{refund_id}/confirm', json={'payout': 'credit_note'}
    )
    assert refused.status_code == 409, refused.text
    assert 'cash session' in refused.json()['detail']

    await client.post('/api/v1/cash-sessions', json={'cash_drawer': 1, 'opening_amount': '0'})
    confirmed = await client.post(
        f'/api/v1/customer-refunds/{refund_id}/confirm', json={'payout': 'credit_note'}
    )
    assert confirmed.status_code == 200, confirmed.text

    notes = await client.get('/api/v1/credit-notes', params={'customer': 1})
    assert notes.status_code == 200, notes.text
    assert notes.json()['total'] == 1
    # Nothing drawn against it yet, so the whole refund is available. Compared numerically: the
    # credit note carries its column's scale, which is wider than the money the order reports.
    from decimal import Decimal

    assert Decimal(notes.json()['items'][0]['remaining']) == Decimal(total)
