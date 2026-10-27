"""Credit notes over real rows (spec 034, US1 and US2).

Notes are seeded the way the current system writes them (`CustomerRefundsController.cs:353-378`):
the amount on a type-4 backing payment, and the note itself with `refunded = 0` and no cash session.
982 of mbe_dev's 984 notes look like this. A note paid back in cash carries a cash session and,
in `refunded`, what was handed back.
"""

from datetime import datetime
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import CurrencyCode, EntityStatus, PaymentType
from app.models.customer import Customer
from app.models.sales import (
    CreditNote,
    CustomerPayment,
    CustomerRefund,
    SalesOrderPayment,
)
from tests.integration.seed import seed_facility, seed_sales_order

NOW = datetime(2026, 9, 29, 12, 55)
URL = '/api/v1/credit-notes'
CUSTOMER = 2


@pytest.fixture
async def ledger(db: AsyncSession, seeded: None) -> dict[str, int]:
    """Customer 2, a second facility, and a completed order to hang refunds and applications on."""
    db.add(
        Customer(
            customer_id=CUSTOMER,
            code='C2',
            name='Cliente Dos',
            credit_limit=Decimal(0),
            credit_days=0,
            price_list=1,
            status=EntityStatus.ACTIVE,
        )  # fmt: skip
    )
    await db.commit()
    return {
        'order': await seed_sales_order(db, completed=True),
        'facility': await seed_facility(db),
    }


async def _note(
    db: AsyncSession,
    order: int,
    amount: str,
    *,
    customer: int = CUSTOMER,
    facility: int = 1,
    refunded: str = '0',
    cash_session: int | None = None,
) -> tuple[int, int]:
    """A credit note the current system's way; returns (note, backing payment)."""
    refund = CustomerRefund(
        sales_order=order, customer=customer, creator=1, updater=1, sales_person=1,
        creation_time=NOW, modification_time=NOW, completed=True, cancelled=False,
        facility=facility, serial=1, date=NOW, currency=CurrencyCode.MXN,
        exchange_rate=Decimal('1'),
    )  # fmt: skip
    payment = CustomerPayment(
        facility=facility, customer=customer, amount=Decimal(amount), method=0, date=NOW,
        currency=CurrencyCode.MXN, creator=1, updater=1, creation_time=NOW,
        modification_time=NOW, payment_type=int(PaymentType.CREDIT_NOTE), serial=0,
    )  # fmt: skip
    db.add_all([refund, payment])
    await db.flush()
    note = CreditNote(
        sales_order=order, customer_refund=refund.customer_refund_id,
        customer_payment=payment.customer_payment_id, customer=customer,
        refunded=Decimal(refunded), cash_session=cash_session, date=NOW,
    )  # fmt: skip
    db.add(note)
    await db.commit()
    return note.credit_note_id, payment.customer_payment_id


async def _apply(
    db: AsyncSession, order: int, payment: int, amount: str, *, change: str = '0',
    cancelled: bool = False,
) -> None:  # fmt: skip
    db.add(
        SalesOrderPayment(
            sales_order=order,
            customer_payment=payment,
            amount=Decimal(amount),
            amount_change=Decimal(change),
            applier=1,
            date=NOW,
            confirmed=True,
            cancelled=cancelled,
        )  # fmt: skip
    )
    await db.commit()


async def _read(client: AsyncClient, note: int) -> dict:
    response = await client.get(f'{URL}/{note}')
    assert response.status_code == 200, response.text
    return response.json()


async def _session(client: AsyncClient) -> int:
    opened = await client.post(
        '/api/v1/cash-sessions', json={'cash_drawer': 1, 'opening_amount': '0'}
    )
    assert opened.status_code == 201, opened.text
    return opened.json()['cash_session_id']


class TestBalances:
    async def test_a_legacy_note_reads_its_payments_unapplied_amount(
        self, client: AsyncClient, db: AsyncSession, ledger: dict
    ) -> None:
        """Note 990's shape: 27.00 issued, 24.00 applied, 3.00 left (US1 scenario 1)."""
        note, payment = await _note(db, ledger['order'], '27')
        await _apply(db, ledger['order'], payment, '24')

        body = await _read(client, note)

        assert Decimal(body['amount']) == Decimal('27')
        assert Decimal(body['remaining']) == Decimal('3')
        assert Decimal(body['refunded']) == 0
        assert body['refunded_to_customer'] is False
        assert body['cash_session'] is None

    async def test_change_handed_back_counts_as_used(
        self, client: AsyncClient, db: AsyncSession, ledger: dict
    ) -> None:
        """Scenario 2: 100 issued, 60 applied with 15 of change, 25 left, as on the payment."""
        note, payment = await _note(db, ledger['order'], '100')
        await _apply(db, ledger['order'], payment, '60', change='15')

        assert Decimal((await _read(client, note))['remaining']) == Decimal('25')

    async def test_a_note_paid_back_in_cash_has_nothing_left(
        self, client: AsyncClient, db: AsyncSession, ledger: dict
    ) -> None:
        """Note 462's shape: 3,510 issued, 1,965 applied, 1,545 handed back (scenario 3)."""
        session = await _session(client)
        note, payment = await _note(
            db, ledger['order'], '3510', refunded='1545', cash_session=session
        )
        await _apply(db, ledger['order'], payment, '1965')

        body = await _read(client, note)

        assert Decimal(body['amount']) == Decimal('3510')
        assert Decimal(body['remaining']) == 0
        assert body['refunded_to_customer'] is True
        assert Decimal(body['refunded']) == Decimal('1545')

    async def test_a_reversed_application_restores_the_remainder(
        self, client: AsyncClient, db: AsyncSession, ledger: dict
    ) -> None:
        note, payment = await _note(db, ledger['order'], '50')
        await _apply(db, ledger['order'], payment, '50', cancelled=True)

        assert Decimal((await _read(client, note))['remaining']) == Decimal('50')

    async def test_a_note_reports_its_payments_facility(
        self, client: AsyncClient, db: AsyncSession, ledger: dict
    ) -> None:
        """FR-006; the caller here is an administrator."""
        note, _ = await _note(db, ledger['order'], '10', facility=ledger['facility'])

        assert (await _read(client, note))['facility'] == ledger['facility']

    async def test_the_list_and_the_detail_agree(
        self, client: AsyncClient, db: AsyncSession, ledger: dict
    ) -> None:
        note, payment = await _note(db, ledger['order'], '27')
        await _apply(db, ledger['order'], payment, '24')

        listed = await client.get(URL, params={'customer': CUSTOMER})

        assert listed.status_code == 200, listed.text
        (row,) = listed.json()['items']
        detail = await _read(client, note)
        for key in ('amount', 'remaining', 'refunded', 'refunded_to_customer', 'facility'):
            assert row[key] == detail[key], key
