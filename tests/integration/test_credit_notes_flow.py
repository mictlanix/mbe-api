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

from app.core.deps import CurrentUser
from app.enums import CurrencyCode, EntityStatus, PaymentType
from app.models.customer import Customer
from app.models.sales import (
    CreditNote,
    CustomerPayment,
    CustomerRefund,
    SalesOrderPayment,
)
from app.services import credit_note_service
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


class TestOpenOnly:
    """US2 (#278): the open filter runs in the query, before counting and paging."""

    async def _pool(self, client: AsyncClient, db: AsyncSession, order: int) -> list[int]:
        """5 open notes among 2 spent, 1 paid back in cash and 2 of the walk-in customer,
        interleaved by id. Returns the open ones, newest first."""
        session = await _session(client)
        open_ = []
        for kind in ('open', 'spent', 'walk-in', 'open', 'cash', 'open', 'spent', 'open'):
            if kind == 'walk-in':
                await _note(db, order, '10', customer=1)
            elif kind == 'cash':
                await _note(db, order, '10', refunded='10', cash_session=session)
            else:
                note, payment = await _note(db, order, '10')
                if kind == 'spent':
                    await _apply(db, order, payment, '10')
                else:
                    open_.append(note)
        await _note(db, order, '10', customer=1)
        note, _ = await _note(db, order, '10')
        open_.append(note)
        return sorted(open_, reverse=True)

    async def test_pages_are_full_and_total_counts_open_notes(
        self, client: AsyncClient, db: AsyncSession, ledger: dict
    ) -> None:
        open_ = await self._pool(client, db, ledger['order'])

        pages, seen = [], []
        for skip in (0, 2, 4):
            page = await client.get(URL, params={'open_only': 'true', 'limit': 2, 'skip': skip})
            assert page.status_code == 200, page.text
            pages.append(page.json())
            seen += [row['credit_note_id'] for row in page.json()['items']]

        assert [len(p['items']) for p in pages] == [2, 2, 1]
        assert {p['total'] for p in pages} == {5}
        assert seen == open_, 'each open note once, newest first'
        everything = await client.get(URL, params={'limit': 100})
        assert everything.json()['total'] == 10

    async def test_a_customer_filter_counts_only_their_open_notes(
        self, client: AsyncClient, db: AsyncSession, ledger: dict
    ) -> None:
        await self._pool(client, db, ledger['order'])

        walk_in = await client.get(URL, params={'open_only': 'true', 'customer': 1})
        customer = await client.get(URL, params={'open_only': 'true', 'customer': CUSTOMER})

        assert walk_in.json()['total'] == 0, "the walk-in customer's notes are never open"
        assert customer.json()['total'] == 5


class TestTheOpenRule:
    """FR-004 called directly, one case per branch: the rule is SQL, so it runs against SQLite."""

    async def _open_ids(self, db: AsyncSession) -> set[int]:
        admin = CurrentUser(
            user_id='tester', session_version=1, administrator=True, facility_id=1, employee_id=1
        )
        notes, _ = await credit_note_service.list_credit_notes(
            db, current=admin, open_only=True, limit=100
        )
        return {note.credit_note_id for note in notes}

    async def test_more_than_a_cent_left_is_open(
        self, client: AsyncClient, db: AsyncSession, ledger: dict
    ) -> None:
        cent, cent_payment = await _note(db, ledger['order'], '10')
        await _apply(db, ledger['order'], cent_payment, '9.99')
        two, two_payment = await _note(db, ledger['order'], '10')
        await _apply(db, ledger['order'], two_payment, '9.98')

        opened = await self._open_ids(db)

        assert cent not in opened, '0.01 left is settled'
        assert two in opened

    async def test_paid_back_in_cash_is_never_open(
        self, client: AsyncClient, db: AsyncSession, ledger: dict
    ) -> None:
        session = await _session(client)
        note, _ = await _note(db, ledger['order'], '10', refunded='10', cash_session=session)

        assert note not in await self._open_ids(db)

    async def test_the_walk_in_customers_notes_are_never_open(
        self, client: AsyncClient, db: AsyncSession, ledger: dict
    ) -> None:
        note, _ = await _note(db, ledger['order'], '10', customer=1)

        assert note not in await self._open_ids(db)
