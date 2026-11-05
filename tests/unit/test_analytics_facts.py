"""Spec 037, T013: the line facts price lines to the cent, as `totals.document_totals` does (R3).

The SQL runs on an in-memory SQLite holding only the sales tables, and compiles for MariaDB, whose
window functions the integration tests run only through SQLite's.
"""

from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import insert, select
from sqlalchemy.dialects import mysql
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.db.base import Base
from app.models.sales import (
    CustomerRefund,
    CustomerRefundDetail,
    SalesOrder,
    SalesOrderDetail,
)
from app.schemas.analytics import DimensionKey
from app.services import totals
from app.services.analytics import facts
from app.services.analytics.catalog import Fact

DAY = datetime(2026, 5, 4, 10, 0)
TABLES = [t.__table__ for t in (SalesOrder, SalesOrderDetail, CustomerRefund, CustomerRefundDetail)]


def _order(order_id: int, *, cancelled: bool = False, completed: bool = True) -> dict:
    return dict(
        sales_order_id=order_id, creator=1, updater=1, creation_time=DAY,
        modification_time=DAY, facility=1, point_sale=1, salesperson=1, customer=1, date=DAY,
        confirmation_time=DAY if completed else None, promise_date=DAY, due_date=DAY, currency=0,
        exchange_rate=1, payment_terms=0, priority=0, completed=completed, cancelled=cancelled,
        paid=False, delivered=False,
    )  # fmt: skip


def _line(line_id: int, order_id: int, qty: str, price: str, **kw: object) -> dict:
    return {
        'sales_order_detail_id': line_id,
        'sales_order': order_id,
        'product': 1,
        'quantity': Decimal(qty),
        'cost': Decimal(kw.get('cost', '0')),
        'price': Decimal(price),
        'discount_rate': Decimal(kw.get('discount', '0')),
        'tax_rate': Decimal(kw.get('rate', '0.16')),
        'product_code': 'P',
        'product_name': 'P',
        'exchange_rate': 1,
        'currency': 0,
        'tax_included': kw.get('included', True),
    }


#: Fixture orders: tax-included and tax-excluded prices, line discounts, a 0% VAT line, and three
#: lines whose half cents each round up alone but not together.
ORDERS = {
    1: [_line(1, 1, '3', '115.50'), _line(2, 1, '1', '9.99', included=False)],
    2: [_line(3, 2, '2', '100', discount='0.125'), _line(4, 2, '5', '13.37', rate='0')],
    3: [_line(5, 3, '1', '0.125', included=False, rate='0')] * 1
    + [_line(6, 3, '1', '0.125', included=False, rate='0')]
    + [_line(7, 3, '1', '0.125', included=False, rate='0')],
    4: [_line(8, 4, '7', '33.335'), _line(9, 4, '2', '1.005', cost='0.80')],
    6: [
        _line(11, 6, '1', '0.90', cost='0.80'),
        _line(12, 6, '1', '0.90', cost='0.80', included=False),
    ],
}


@pytest.fixture
async def engine() -> AsyncEngine:
    engine = create_async_engine('sqlite+aiosqlite://')
    async with engine.begin() as connection:
        await connection.run_sync(lambda c: Base.metadata.create_all(c, tables=TABLES))
        for order_id, lines in ORDERS.items():
            await connection.execute(insert(SalesOrder.__table__).values(**_order(order_id)))
            await connection.execute(insert(SalesOrderDetail.__table__), lines)
        await connection.execute(insert(SalesOrder.__table__).values(**_order(5, cancelled=True)))
        await connection.execute(insert(SalesOrderDetail.__table__), [_line(10, 5, '1', '1000')])
    yield engine
    await engine.dispose()


def _expected(lines: list[dict]) -> totals.DocumentTotals:
    return totals.document_totals(
        totals.Line(
            quantity=line['quantity'],
            price=line['price'],
            discount_rate=line['discount_rate'],
            tax_rate=line['tax_rate'],
            tax_included=line['tax_included'],
        )
        for line in lines
    )


async def _lines(engine: AsyncEngine) -> list:
    rows = facts.sale_lines(date(2026, 5, 1), date(2026, 5, 31), None)
    async with engine.connect() as connection:
        return (await connection.execute(select(rows))).mappings().all()


async def test_each_orders_line_cents_sum_to_its_totals(engine: AsyncEngine) -> None:
    lines = await _lines(engine)

    for order_id, fixture in ORDERS.items():
        expected = _expected(fixture)
        own = [line for line in lines if line['doc'] == order_id]
        assert sum(line['sub_c'] for line in own) == expected.subtotal * 100, order_id
        assert sum(line['tax_c'] for line in own) == expected.tax_total * 100, order_id


async def test_the_sum_over_orders_is_the_sum_of_the_totals(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        row = (await connection.execute(facts.query(Fact.SALE_LINE, _slice()))).mappings().one()

    expected = sum((_expected(lines).total for lines in ORDERS.values()), Decimal(0))
    assert row['sub_c'] + row['tax_c'] == expected * 100
    assert row['orders'] == len(ORDERS)


def _slice(**kw: object) -> facts.Slice:
    return facts.Slice(start=date(2026, 5, 1), end=date(2026, 5, 31), **kw)


async def test_the_half_cents_round_once_per_order(engine: AsyncEngine) -> None:
    """Each 0.125 line rounds to 0.13 alone; the order rounds 0.375 to 0.38, so the residual is
    -0.01, and it lands on the lowest id among the equal lines."""
    lines = {line['id']: line for line in await _lines(engine) if line['doc'] == 3}

    assert [lines[i]['sub_c'] for i in (5, 6, 7)] == [12, 13, 13]


async def test_the_residual_lands_on_the_largest_line(engine: AsyncEngine) -> None:
    lines = {line['id']: line for line in await _lines(engine) if line['doc'] == 4}
    alone = totals.line_amounts(
        quantity=Decimal('2'), price=Decimal('1.005'), discount_rate=Decimal(0),
        tax_rate=Decimal('0.16'), tax_included=True,
    )[0].quantize(Decimal('0.01'), rounding='ROUND_HALF_UP')  # fmt: skip

    assert lines[9]['sub_c'] == alone * 100


async def test_cost_is_as_recorded_and_only_on_costed_lines(engine: AsyncEngine) -> None:
    """Cost is recorded without VAT (spec 031), so it is never divided by 1 + the VAT rate."""
    lines = {line['id']: line for line in await _lines(engine)}

    # 2 × 0.80 = 1.60; the price 1.005 is 0.8664 without VAT, above the cost, so not below.
    assert lines[9]['cost_c'] == 160
    assert lines[9]['costed'] == 1 and lines[9]['below'] == 0
    assert lines[8]['costed'] == 0 and lines[8]['cost_c'] == 0


async def test_below_cost_compares_the_price_without_vat(engine: AsyncEngine) -> None:
    lines = {line['id']: line for line in await _lines(engine)}

    # 0.90 with VAT is 0.7759 without it: below a cost of 0.80, though 0.90 itself is above.
    assert lines[11]['below'] == 1
    # 0.90 without VAT is above the cost.
    assert lines[12]['below'] == 0


async def test_a_cancelled_order_is_not_a_sale(engine: AsyncEngine) -> None:
    assert 5 not in {line['doc'] for line in await _lines(engine)}


async def test_the_refund_takes_its_cost_from_the_sale_line(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            insert(CustomerRefund.__table__).values(
                customer_refund_id=1,
                sales_order=4,
                customer=1,
                creator=1,
                updater=1,
                sales_person=2,
                creation_time=DAY,
                modification_time=DAY,
                completed=True,
                cancelled=False,
                facility=2,
                serial=1,
                date=datetime(2026, 5, 20, 9, 0),
                currency=0,
                exchange_rate=1,
            )  # fmt: skip
        )
        await connection.execute(
            insert(CustomerRefundDetail.__table__).values(
                customer_refund_detail_id=1,
                customer_refund=1,
                sales_order_detail=9,
                quantity=Decimal('1'),
                product=1,
                price=Decimal('1.005'),
                product_code='P',
                product_name='P',
                tax_rate=Decimal('0.16'),
                discount=Decimal('0'),
                exchange_rate=1,
                currency=0,
                tax_included=True,
            )  # fmt: skip
        )
        rows = facts.refund_lines(date(2026, 5, 1), date(2026, 5, 31), None)
        line = (await connection.execute(select(rows))).mappings().one()

    # One of the two units returned, at the sale line's cost: 0.80.
    assert line['cost_c'] == 80
    assert line['costed'] == 1
    assert (line['facility'], line['salesperson']) == (2, 2)


@pytest.mark.parametrize('fact', list(Fact))
def test_every_fact_compiles_for_mariadb(fact: Fact) -> None:
    statement = facts.query(
        fact, _slice(dimensions=(DimensionKey.DATE, DimensionKey.CUSTOMER_TYPE), facility=1)
    )
    sql = str(statement.compile(dialect=mysql.dialect())).upper()

    assert 'STRFTIME' not in sql
    assert 'GROUP BY K0, K1' in sql
    if fact is not Fact.PAYMENT:
        assert 'ROW_NUMBER() OVER (PARTITION BY' in sql
        assert 'SUM(' in sql and ') OVER (PARTITION BY' in sql
