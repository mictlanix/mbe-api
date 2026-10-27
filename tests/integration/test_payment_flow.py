"""Money in and money back, driven through the API against a real database.

The payment services do the arithmetic that mocked tests can only assert *about*: an application
reduces an order's balance, a reversal restores it, and a refund is refused until the sale is fully
paid. Each of those is a read of rows the previous request wrote, which is exactly what a mock
cannot represent.
"""

from datetime import datetime
from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import CurrencyCode, EntityStatus, PaymentMethod
from app.models.customer import Customer
from app.models.sales import CustomerPayment, SalesOrder, SalesOrderPayment
from tests.integration.seed import seed_facility, seed_sales_order


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


async def test_an_application_above_what_the_order_owes_is_refused(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    """Spec 033 FR-021. Until then nothing checked the order's side, so a payment larger than the
    sale could be applied whole. Change is how a cash tender hands back the excess."""
    order_id, total = await _completed_order_with_total(client, db)
    payment = await client.post(
        '/api/v1/customer-payments',
        json={'customer': 1, 'amount': '99999', 'method': PaymentMethod.CASH},
    )
    payment_id = payment.json()['customer_payment_id']

    refused = await client.post(
        f'/api/v1/customer-payments/{payment_id}/applications',
        json={'sales_order': order_id, 'amount': '99999'},
    )

    assert refused.status_code == 422, refused.text
    assert refused.json()['detail'] == f'Amount 99999 exceeds the order balance of {total}'


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


async def test_an_outstanding_row_carries_the_customers_own_name(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    """#174 — the mirror image of #172.

    This list's *search* already matched the customer's name; only the projection read the
    per-document override, which was null on all 1,840 outstanding orders in the deployment. So a
    cashier could find an order by typing a customer's name and the row that came back did not say
    that name.
    """
    order_id, _ = await _completed_order_with_total(client, db)

    response = await client.get(
        '/api/v1/customer-payments/outstanding-orders', params={'customer': 1}
    )

    assert response.status_code == 200, response.text
    row = next(r for r in response.json()['items'] if r['sales_order_id'] == order_id)
    assert row['customer_display_name'] == 'Cliente Uno'
    # The override keeps its documented meaning: untouched, so still null.
    assert row['customer_name'] is None


async def test_the_outstanding_search_finds_and_then_shows_the_same_name(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    """The two halves agreeing is the point of #174: what you searched by is what you see."""
    order_id, _ = await _completed_order_with_total(client, db)

    response = await client.get(
        '/api/v1/customer-payments/outstanding-orders', params={'search': 'Cliente'}
    )

    assert response.status_code == 200, response.text
    row = next(r for r in response.json()['items'] if r['sales_order_id'] == order_id)
    assert row['customer_display_name'] == 'Cliente Uno'


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


async def test_the_open_session_expects_cash_net_of_change(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    """#239 — 1,200 tendered on a 1,160 sale with 40 handed back leaves 1,160 in the drawer.

    `payments_by_method` still reports the gross 1,200 taken, which is why the close screen,
    working from it, told an exact cashier they were 40 short.
    """
    opened = await client.post(
        '/api/v1/cash-sessions', json={'cash_drawer': 1, 'opening_amount': '500'}
    )
    assert opened.status_code == 201, opened.text
    session_id = opened.json()['cash_session_id']
    order_id, total = await _completed_order_with_total(client, db)

    payment = await client.post(
        '/api/v1/customer-payments',
        json={'customer': 1, 'amount': '1200', 'method': PaymentMethod.CASH},
    )
    assert payment.status_code == 201, payment.text
    applied = await client.post(
        f'/api/v1/customer-payments/{payment.json()["customer_payment_id"]}/applications',
        json={'sales_order': order_id, 'amount': total, 'amount_change': '40'},
    )
    assert applied.status_code == 201, applied.text

    read = await client.get(f'/api/v1/cash-sessions/{session_id}')
    assert read.status_code == 200, read.text
    assert read.json()['expected_cash'] == '1660.00'
    assert read.json()['payments_by_method'] == [
        {'method': PaymentMethod.CASH, 'total': '1200.0000'}
    ]

    current = await client.get('/api/v1/cash-sessions/current')
    assert current.json()['session']['expected_cash'] == '1660.00'


async def test_the_session_list_does_not_compute_expected_cash(
    client: AsyncClient, seeded: None
) -> None:
    """A cut per row would be a page of cuts; the list leaves the field empty (#239)."""
    await client.post('/api/v1/cash-sessions', json={'cash_drawer': 1, 'opening_amount': '500'})

    listed = await client.get('/api/v1/cash-sessions')

    assert listed.status_code == 200, listed.text
    assert listed.json()['items'][0]['expected_cash'] is None


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
    assert Decimal(notes.json()['items'][0]['remaining']) == Decimal(total)


async def _payment_with_change(client: AsyncClient, db: AsyncSession) -> tuple[int, int]:
    """Payment 300789's shape: 600 tendered, 530 applied, 70 handed back. Returns the payment
    and its application."""
    order_id, _ = await _completed_order_with_total(client, db)
    payment = await client.post(
        '/api/v1/customer-payments',
        json={'customer': 1, 'amount': '600', 'method': PaymentMethod.CASH},
    )
    payment_id = payment.json()['customer_payment_id']
    applied = await client.post(
        f'/api/v1/customer-payments/{payment_id}/applications',
        json={'sales_order': order_id, 'amount': '530', 'amount_change': '70'},
    )
    assert applied.status_code == 201, applied.text
    return payment_id, applied.json()['sales_order_payment_id']


async def test_change_handed_back_is_not_unapplied(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    """Spec 033 FR-005a: 600 − 530 − 70 = 0, as the payment's ticket says (#275)."""
    payment_id, _ = await _payment_with_change(client, db)

    read = await client.get(f'/api/v1/customer-payments/{payment_id}')
    listed = await client.get('/api/v1/customer-payments', params={'customer': 1})

    assert read.status_code == 200, read.text
    assert Decimal(read.json()['unapplied']) == 0
    row = next(r for r in listed.json()['items'] if r['customer_payment_id'] == payment_id)
    assert Decimal(row['unapplied']) == 0


async def test_a_reversed_applications_change_counts_again(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    payment_id, application_id = await _payment_with_change(client, db)

    await client.post(
        f'/api/v1/customer-payments/{payment_id}/applications/{application_id}/reverse',
        json={'reason': 'Cobro duplicado'},
    )

    read = await client.get(f'/api/v1/customer-payments/{payment_id}')
    assert Decimal(read.json()['unapplied']) == Decimal('600')


async def _order_row(db: AsyncSession, order_id: int) -> SalesOrder:
    db.expire_all()
    return await db.get(SalesOrder, order_id)


async def _pay(client: AsyncClient, order_id: int, amount: str) -> tuple[int, int]:
    payment = await client.post(
        '/api/v1/customer-payments',
        json={'customer': 1, 'amount': amount, 'method': PaymentMethod.CASH},
    )
    payment_id = payment.json()['customer_payment_id']
    applied = await client.post(
        f'/api/v1/customer-payments/{payment_id}/applications',
        json={'sales_order': order_id, 'amount': amount},
    )
    assert applied.status_code == 201, applied.text
    return payment_id, applied.json()['sales_order_payment_id']


async def _reverse(client: AsyncClient, payment_id: int, application_id: int) -> None:
    reversed_ = await client.post(
        f'/api/v1/customer-payments/{payment_id}/applications/{application_id}/reverse',
        json={'reason': 'Cobro duplicado'},
    )
    assert reversed_.status_code == 200, reversed_.text


class TestThePaidFlagFollowsTheBalance:
    """Spec 033 FR-011: set when the balance reaches 0.01 or less; cleared only when an event takes
    it from there back above 0.01, so a legacy paid order never reopens."""

    async def test_settling_sets_paid_and_reversing_clears_it(
        self, client: AsyncClient, db: AsyncSession, seeded: None
    ) -> None:
        order_id, total = await _completed_order_with_total(client, db)

        payment_id, application_id = await _pay(client, order_id, total)
        settled = await _order_row(db, order_id)
        assert settled.paid is True
        stamped = settled.balance_zeroed_time
        assert stamped is not None

        await _reverse(client, payment_id, application_id)
        reopened = await _order_row(db, order_id)
        assert reopened.paid is False
        assert reopened.balance_zeroed_time == stamped, 'the commission month never moves'

    async def test_the_legacy_sentinel_counts_as_unset(
        self, client: AsyncClient, db: AsyncSession, seeded: None
    ) -> None:
        order_id, total = await _completed_order_with_total(client, db)
        await db.execute(
            update(SalesOrder)
            .where(SalesOrder.sales_order_id == order_id)
            .values(balance_zeroed_time=datetime(1, 1, 1))
        )
        await db.commit()

        await _pay(client, order_id, total)

        assert (await _order_row(db, order_id)).balance_zeroed_time.year > 1900

    async def test_a_partial_payment_leaves_the_order_unpaid(
        self, client: AsyncClient, db: AsyncSession, seeded: None
    ) -> None:
        order_id, _ = await _completed_order_with_total(client, db)

        await _pay(client, order_id, '100')

        assert (await _order_row(db, order_id)).paid is False

    async def test_a_legacy_paid_order_stays_paid_when_a_payment_is_reversed(
        self, client: AsyncClient, db: AsyncSession, seeded: None
    ) -> None:
        """Flagged paid with 1,060 still computed: the balance was never at zero, so reversing
        its 100 doesn't cross back over, and the order stays closed (clarification Q2)."""
        order_id = await seed_sales_order(db, completed=True, paid=True)
        now = datetime(2026, 9, 1, 10, 0)
        payment = CustomerPayment(
            facility=1, customer=1, amount=Decimal('100'), method=1, date=now,
            currency=CurrencyCode.MXN, creator=1, updater=1, creation_time=now,
            modification_time=now, payment_type=2, serial=0,
        )  # fmt: skip
        db.add(payment)
        await db.flush()
        application = SalesOrderPayment(
            sales_order=order_id, customer_payment=payment.customer_payment_id,
            amount=Decimal('100'), amount_change=Decimal(0), applier=1, date=now,
            confirmed=True, cancelled=False,
        )  # fmt: skip
        db.add(application)
        await db.commit()

        await _reverse(client, payment.customer_payment_id, application.sales_order_payment_id)

        assert (await _order_row(db, order_id)).paid is True


async def _apply(client: AsyncClient, payment_id: int, order_id: int, amount: str, **body):  # noqa: ANN003, ANN202
    return await client.post(
        f'/api/v1/customer-payments/{payment_id}/applications',
        json={'sales_order': order_id, 'amount': amount, **body},
    )


async def _new_payment(client: AsyncClient, amount: str, customer: int = 1) -> int:
    payment = await client.post(
        '/api/v1/customer-payments',
        json={'customer': customer, 'amount': amount, 'method': PaymentMethod.CASH},
    )
    assert payment.status_code == 201, payment.text
    return payment.json()['customer_payment_id']


class TestApplyingAPayment:
    """Spec 033 FR-021. These orders are cash sales; credit orders go through the same service, so
    the same refusals hold for them."""

    async def test_one_payment_across_two_orders(
        self, client: AsyncClient, db: AsyncSession, seeded: None
    ) -> None:
        """US4 scenario 1: 2,000 over two orders of 1,160 settles one and leaves 320 owing."""
        first, total = await _completed_order_with_total(client, db)
        second, _ = await _completed_order_with_total(client, db)
        payment_id = await _new_payment(client, '2000')

        assert (await _apply(client, payment_id, first, total)).status_code == 201
        assert (await _apply(client, payment_id, second, '840')).status_code == 201

        assert (await _order_row(db, first)).paid is True
        second_read = await client.get(f'/api/v1/sales-orders/{second}')
        assert second_read.json()['balance'] == '320.00'
        payment = await client.get(f'/api/v1/customer-payments/{payment_id}')
        assert Decimal(payment.json()['unapplied']) == 0

    async def test_another_customers_payment_is_refused(
        self, client: AsyncClient, db: AsyncSession, seeded: None
    ) -> None:
        db.add(
            Customer(
                customer_id=2,
                code='C2',
                name='Cliente Dos',
                credit_limit=Decimal('0'),
                credit_days=0,
                price_list=1,
                status=EntityStatus.ACTIVE,
            )  # fmt: skip
        )
        await db.commit()
        order_id, _ = await _completed_order_with_total(client, db)
        payment_id = await _new_payment(client, '100', customer=2)

        refused = await _apply(client, payment_id, order_id, '100')

        assert refused.status_code == 422, refused.text
        assert 'different customer' in refused.json()['detail']

    async def test_a_settled_order_is_refused(
        self, client: AsyncClient, db: AsyncSession, seeded: None
    ) -> None:
        flagged = await seed_sales_order(db, completed=True, paid=True)
        settled, total = await _completed_order_with_total(client, db)
        await _pay(client, settled, total)
        payment_id = await _new_payment(client, '100')

        for order_id in (flagged, settled):
            refused = await _apply(client, payment_id, order_id, '10')
            assert refused.status_code == 409, refused.text
            assert refused.json()['detail'] == 'Sales order is already settled'

    async def test_change_counts_against_what_the_payment_holds(
        self, client: AsyncClient, db: AsyncSession, seeded: None
    ) -> None:
        order_id, _ = await _completed_order_with_total(client, db)
        payment_id = await _new_payment(client, '100')

        refused = await _apply(client, payment_id, order_id, '90', amount_change='20')

        assert refused.status_code == 422, refused.text
        assert 'unapplied' in refused.json()['detail']

    async def test_another_facilitys_order_is_still_accepted(
        self, client: AsyncClient, db: AsyncSession, seeded: None
    ) -> None:
        """14% of 2026's credit applications in mbe_dev used another facility's payment."""
        order_id, _ = await _completed_order_with_total(client, db)
        await db.execute(
            update(SalesOrder)
            .where(SalesOrder.sales_order_id == order_id)
            .values(facility=await seed_facility(db))
        )
        await db.commit()
        payment_id = await _new_payment(client, '100')

        assert (await _apply(client, payment_id, order_id, '100')).status_code == 201
