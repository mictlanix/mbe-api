"""Spec 037, T013: the line facts price lines to the cent, as `totals.document_totals` does (R3).

The SQL runs on an in-memory SQLite holding only the sales tables, and compiles for MariaDB, whose
window functions the integration tests run only through SQLite's.
"""

from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import insert, select
from sqlalchemy.dialects import mysql
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.db.base import Base
from app.enums import TransactionType
from app.models.core import Warehouse
from app.models.fiscal import FiscalDocument, FiscalDocumentDetail
from app.models.inventory import InventoryReceipt, InventoryReceiptDetail, LotSerialTracking
from app.models.product import Product
from app.models.purchases import PurchaseOrder, PurchaseOrderDetail
from app.models.sales import (
    CustomerRefund,
    CustomerRefundDetail,
    SalesOrder,
    SalesOrderDetail,
)
from app.schemas.analytics import DimensionKey, MeasureKey, VatTreatment
from app.services import purchase_order_service, totals
from app.services.analytics import balances, facts
from app.services.analytics.catalog import Fact
from app.services.analytics.engine import derive as engine_derive

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


@pytest.mark.parametrize('fact', [Fact.SALE_LINE, Fact.REFUND_LINE, Fact.CREDIT_NOTE, Fact.PAYMENT])
def test_every_fact_compiles_for_mariadb(fact: Fact) -> None:
    """Spec 037's document facts; each spec 038 fact has its own compile check."""
    statement = facts.query(
        fact, _slice(dimensions=(DimensionKey.DATE, DimensionKey.CUSTOMER_TYPE), facility=1)
    )
    sql = str(statement.compile(dialect=mysql.dialect())).upper()

    assert 'STRFTIME' not in sql
    assert 'GROUP BY K0, K1' in sql
    if fact is not Fact.PAYMENT:
        assert 'ROW_NUMBER() OVER (PARTITION BY' in sql
        assert 'SUM(' in sql and ') OVER (PARTITION BY' in sql


# ── Spec 038, T013: the movement fact (research R2) ───────────────────────────

LEDGER_TABLES = [t.__table__ for t in (LotSerialTracking, Warehouse, Product)]
MOVE = TransactionType


def _entry(entry_id: int, source: MOVE, reference: int, warehouse: int, qty: str) -> dict:
    return dict(
        lot_serial_tracking_id=entry_id, source=int(source), reference=reference,
        date=datetime(2026, 5, 10 + entry_id % 3, 9), warehouse=warehouse, product=1,
        quantity=Decimal(qty),
    )  # fmt: skip


#: Two facilities' warehouses; a sale of two lines, a receipt, and a transfer from 1 to 2.
ENTRIES = [
    _entry(1, MOVE.SALES_ORDER, 50, 1, '-2'),
    _entry(2, MOVE.SALES_ORDER, 50, 1, '-1'),
    _entry(3, MOVE.INVENTORY_RECEIPT, 7, 1, '10'),
    _entry(4, MOVE.INVENTORY_TRANSFER, 9, 1, '-4'),
    _entry(5, MOVE.INVENTORY_TRANSFER, 9, 2, '4'),
]


@pytest.fixture
async def ledger() -> AsyncEngine:
    engine = create_async_engine('sqlite+aiosqlite://')
    async with engine.begin() as connection:
        await connection.run_sync(lambda c: Base.metadata.create_all(c, tables=LEDGER_TABLES))
        await connection.execute(
            insert(Warehouse.__table__),
            [
                {'warehouse_id': 1, 'facility': 1, 'code': 'W1', 'name': 'W1'},
                {'warehouse_id': 2, 'facility': 2, 'code': 'W2', 'name': 'W2'},
            ],
        )
        await connection.execute(
            insert(Product.__table__).values(
                product_id=1, code='P', name='P', unit_of_measurement='H87', stockable=True,
                perishable=False, seriable=False, purchasable=True, salable=True,
                invoiceable=True, tax_rate=Decimal('0.16'), tax_included=True, price_type=0,
                currency=0, min_order_qty=1, status=0, stock_verification=True, supplier=3,
            )
        )  # fmt: skip
        await connection.execute(insert(LotSerialTracking.__table__), ENTRIES)
    yield engine
    await engine.dispose()


async def _movements(ledger: AsyncEngine, *dimensions: DimensionKey, **kw: object) -> list:
    async with ledger.connect() as connection:
        statement = facts.query(Fact.MOVEMENT, _slice(dimensions=dimensions, **kw))
        return (await connection.execute(statement)).mappings().all()


async def test_movements_count_units_in_and_out_by_type(ledger: AsyncEngine) -> None:
    rows = {r['k0']: r for r in await _movements(ledger, DimensionKey.MOVEMENT_TYPE)}

    sale = rows[int(MOVE.SALES_ORDER)]
    assert (sale['units_in'], sale['units_out'], sale['entries']) == (0, 3, 2)
    assert sale['documents'] == 1  # two lines of one order
    receipt = rows[int(MOVE.INVENTORY_RECEIPT)]
    assert (receipt['units_in'], receipt['units_out'], receipt['documents']) == (10, 0, 1)


async def test_a_transfer_nets_to_zero_company_wide(ledger: AsyncEngine) -> None:
    [total] = await _movements(
        ledger, filters={DimensionKey.MOVEMENT_TYPE: (int(MOVE.INVENTORY_TRANSFER),)}
    )
    by_facility = {r['k0']: r for r in await _movements(ledger, DimensionKey.FACILITY)}

    assert total['units_in'] - total['units_out'] == 0
    assert total['documents'] == 1
    # Each side is the warehouse's facility's: out of 1, into 2.
    assert by_facility[2]['units_in'] == 4
    assert by_facility[1]['units_out'] == 7


async def test_movements_respect_the_facility_scope_and_the_supplier(ledger: AsyncEngine) -> None:
    scoped = await _movements(ledger, DimensionKey.SUPPLIER, facility=2)

    assert [(r['k0'], r['units_in'], r['entries']) for r in scoped] == [(3, 4, 1)]


def test_the_movement_fact_compiles_for_mariadb() -> None:
    statement = facts.query(
        Fact.MOVEMENT,
        _slice(
            dimensions=(DimensionKey.DATE, DimensionKey.MOVEMENT_TYPE, DimensionKey.BRAND),
            facility=1,
        ),
    )
    sql = str(statement.compile(dialect=mysql.dialect())).upper()

    assert 'STRFTIME' not in sql
    assert 'GROUP BY K0, K1, K2' in sql
    assert 'COUNT(DISTINCT(MOVEMENTS.DOC))' in sql
    assert 'WAREHOUSE.FACILITY =' in sql


class _Recording:
    """A session that records each statement and answers with no rows."""

    def __init__(self) -> None:
        self.statements: list = []

    async def execute(self, statement: object) -> SimpleNamespace:
        self.statements.append(statement)
        return SimpleNamespace(all=list)


async def test_the_stock_ledger_queries_compile_for_mariadb() -> None:
    """T075: the opening sum, the CASE-bucketed movements of later moments, a checkpoint's
    difference and the units sold, which US1 ran on SQLite only, compile for MariaDB with nothing
    SQLite-only."""
    session = _Recording()
    filters = {DimensionKey.PRODUCT: (1, 2)}
    moments = [date(2026, 3, 31), date(2026, 4, 30), date(2026, 5, 31)]
    await balances._sold(session, moments[-1], 90, 1, filters)  # type: ignore[arg-type]
    entry = LotSerialTracking
    statements = (
        balances._opening_query(balances.day_end(moments[0]), 1, filters),
        balances._movements_query(moments, 1, filters),
        balances._delta_query(entry.lot_serial_tracking_id > 5, 1, filters),
        *session.statements,
    )

    opening, movements, delta, sold = (
        str(s.compile(dialect=mysql.dialect())).upper() for s in statements
    )
    for sql in (opening, movements, delta, sold):
        assert 'STRFTIME' not in sql and 'JULIANDAY' not in sql
        assert 'WAREHOUSE.FACILITY =' in sql
        assert 'LOT_SERIAL_TRACKING.PRODUCT IN' in sql
    assert 'SUM(LOT_SERIAL_TRACKING.QUANTITY)' in opening
    assert 'CASE WHEN (LOT_SERIAL_TRACKING.DATE <' in movements
    assert 'GROUP BY LOT_SERIAL_TRACKING.PRODUCT, LOT_SERIAL_TRACKING.WAREHOUSE, CASE' in movements
    assert 'LOT_SERIAL_TRACKING.LOT_SERIAL_TRACKING_ID >' in delta
    assert 'ROUND(LOT_SERIAL_TRACKING.QUANTITY' in sold


# ── Spec 038, T026: the credit flow fact (research R9) ────────────────────────


def test_the_credit_flow_fact_compiles_for_mariadb() -> None:
    """Credit sales, applications and the start balance due, in one grouped statement."""
    statement = facts.query(
        Fact.CREDIT_FLOW,
        _slice(
            dimensions=(DimensionKey.DATE, DimensionKey.SALES_AGENT, DimensionKey.PAYMENT_TERMS),
            facility=1,
        ),
    )
    sql = str(statement.compile(dialect=mysql.dialect())).upper()

    assert 'STRFTIME' not in sql
    assert 'UNION ALL' in sql
    assert 'GROUP BY K0, K1, K2' in sql
    assert 'RECEIVABLE_SNAPSHOT.MONTH_END =' in sql
    assert 'SALES_ORDER.FACILITY =' in sql


# ── Spec 038, T042: invoices and invoice coverage (research R12) ──────────────

FISCAL_TABLES = [
    t.__table__ for t in (SalesOrder, SalesOrderDetail, FiscalDocument, FiscalDocumentDetail)
]
SOLD = datetime(2026, 5, 4, 10, 0)


def _document(doc_id: int, kind: int, day: int, **kw: object) -> dict:
    """A fiscal document issued on May `day`: stamped and live unless told otherwise."""
    issued = datetime(2026, 5, day, 12, 0)
    return dict(
        fiscal_document_id=doc_id, creation_time=issued, modification_time=issued, creator=1,
        updater=1, issuer=kw.get('issuer', 'AAA010101AAA'), customer=1, recipient='XAXX010101000',
        type=kind, facility=kw.get('facility', 1), issued=issued, issued_location='Mérida',
        completed=True, cancelled=kw.get('cancelled', False),
        cancellation_date=issued if kw.get('cancelled') else None, payment_method=99,
        exchange_rate=1, currency=0, payment_terms=0, version=Decimal('4.0'), provider=0,
        retention_rate=0, local_retention_rate=0,
        stamped=issued if kw.get('stamped', True) else None,
        payment_amount=kw.get('payment'),
    )  # fmt: skip


def _fiscal_line(line_id: int, doc: int, qty: str, price: str, order_detail: int | None) -> dict:
    return dict(
        fiscal_document_detail_id=line_id, document=doc, product=1, order_detail=order_detail,
        product_name='P', quantity=Decimal(qty), price=Decimal(price), discount=Decimal(0),
        tax_rate=Decimal('0.16'), exchange_rate=1, currency=0, tax_included=False,
    )  # fmt: skip


INVOICE, NOTE, RECEIPT = 0, 100, 200
#: Order 21 (May 4) sells line 31 (3 at 100 with VAT) and line 32 (2 at 50 without VAT).
#: Line 31 is invoiced 2 on May 6 and 0.5 on May 20, and 0.5 more on a cancelled invoice; line 32
#: is invoiced 5 on May 20, more than it sold. A pending invoice and the first stamped one carry a
#: standalone line; a draft cancelled before stamping, a credit note and a payment receipt complete
#: the month.
DOCUMENTS = [
    _document(41, INVOICE, 6),
    _document(42, INVOICE, 20),
    _document(43, INVOICE, 7, cancelled=True),
    _document(44, INVOICE, 9, stamped=False),
    _document(45, INVOICE, 9, stamped=False, cancelled=True),
    _document(46, NOTE, 10),
    _document(47, RECEIPT, 12, payment=Decimal('500')),
]
DOCUMENT_LINES = {
    41: [_fiscal_line(51, 41, '2', '86.21', 31), _fiscal_line(52, 41, '1', '10.005', None)],
    42: [_fiscal_line(53, 42, '0.5', '86.21', 31), _fiscal_line(54, 42, '5', '50', 32)],
    43: [_fiscal_line(55, 43, '0.5', '86.21', 31)],
    44: [_fiscal_line(56, 44, '1', '20', None)],
    45: [_fiscal_line(57, 45, '1', '30', None)],
    46: [_fiscal_line(58, 46, '1', '25.125', None)],
    47: [_fiscal_line(59, 47, '1', '0', None)],
}


@pytest.fixture
async def fiscal() -> AsyncEngine:
    engine = create_async_engine('sqlite+aiosqlite://')
    async with engine.begin() as connection:
        await connection.run_sync(lambda c: Base.metadata.create_all(c, tables=FISCAL_TABLES))
        await connection.execute(insert(SalesOrder.__table__).values(**_order(21)))
        await connection.execute(
            insert(SalesOrderDetail.__table__),
            [_line(31, 21, '3', '100'), _line(32, 21, '2', '50', included=False)],
        )
        await connection.execute(insert(FiscalDocument.__table__), DOCUMENTS)
        await connection.execute(
            insert(FiscalDocumentDetail.__table__),
            [line for lines in DOCUMENT_LINES.values() for line in lines],
        )
    yield engine
    await engine.dispose()


async def _fact(engine: AsyncEngine, fact: Fact, **kw: object) -> list:
    async with engine.connect() as connection:
        statement = facts.query(fact, _slice(**kw))
        return (await connection.execute(statement)).mappings().all()


def _sums(fact: Fact, row: dict) -> dict:
    return {f'{fact.value}.{name}': value for name, value in row.items()}


def _document_tax(lines: list[dict]) -> Decimal:
    return totals.document_totals(
        totals.Line(
            quantity=line['quantity'], price=line['price'], discount_rate=line['discount'],
            tax_rate=line['tax_rate'], tax_included=line['tax_included'],
        )
        for line in lines
    ).tax_total  # fmt: skip


async def test_the_invoiced_quantity_is_capped_at_the_line_quantity(fiscal: AsyncEngine) -> None:
    rows = facts.coverage_lines(date(2026, 5, 1), date(2026, 5, 31), None)
    async with fiscal.connect() as connection:
        lines = {r['id']: r for r in (await connection.execute(select(rows))).mappings().all()}

    # 2 + 0.5 live; the cancelled invoice's 0.5 does not count.
    assert lines[31]['inv_qty'] == Decimal('2.5')
    # 5 invoiced, 2 sold.
    assert lines[32]['inv_qty'] == 2
    assert (lines[32]['inv_sub_c'], lines[32]['inv_tax_c']) == (
        lines[32]['sub_c'], lines[32]['tax_c'],
    )  # fmt: skip


async def test_invoiced_and_not_invoiced_add_up_to_the_sale(fiscal: AsyncEngine) -> None:
    """Each line's invoiced cents are its sale cents × invoiced ÷ sold, rounded, valued at the sale
    line's price; not invoiced is the rest, so the two add up to sold to the cent."""
    rows = facts.coverage_lines(date(2026, 5, 1), date(2026, 5, 31), None)
    async with fiscal.connect() as connection:
        lines = {r['id']: r for r in (await connection.execute(select(rows))).mappings().all()}
    [sold] = await _fact(fiscal, Fact.SALE_LINE)
    [covered] = await _fact(fiscal, Fact.INVOICE_COVERAGE)

    line = lines[31]
    share = Decimal(line['sub_c']) * Decimal('2.5') / 3
    assert line['inv_sub_c'] == int(share.quantize(Decimal(1), rounding='ROUND_HALF_UP'))
    for vat in VatTreatment:
        derived = engine_derive(_sums(Fact.INVOICE_COVERAGE, covered), vat)
        gross = engine_derive(_sums(Fact.SALE_LINE, sold), vat)[MeasureKey.GROSS_SALES]
        invoiced = derived[MeasureKey.INVOICED_SALES]
        assert invoiced + derived[MeasureKey.UNINVOICED_SALES] == gross, vat
        assert 0 < invoiced < gross, vat


async def test_the_lag_is_weighted_by_invoiced_cents(fiscal: AsyncEngine) -> None:
    """Line 31 was first invoiced 2 days after the sale, line 32 16 days after: the average is
    weighted by each line's invoiced cents, not a plain mean of 9 days."""
    rows = facts.coverage_lines(date(2026, 5, 1), date(2026, 5, 31), None)
    async with fiscal.connect() as connection:
        lines = {r['id']: r for r in (await connection.execute(select(rows))).mappings().all()}
    [covered] = await _fact(fiscal, Fact.INVOICE_COVERAGE)

    weights = {31: lines[31]['inv_sub_c'], 32: lines[32]['inv_sub_c']}
    expected = Decimal(2 * weights[31] + 16 * weights[32]) / (weights[31] + weights[32])
    lag = engine_derive(_sums(Fact.INVOICE_COVERAGE, covered), VatTreatment.EXCLUDED)[
        MeasureKey.INVOICING_LAG
    ]
    assert lag == expected.quantize(Decimal('0.1'), rounding='ROUND_HALF_UP')
    assert lag != Decimal('9.0')


async def test_statuses_follow_r12(fiscal: AsyncEngine) -> None:
    """Stamped and live, cancelled after stamping, and pending; the draft cancelled before
    stamping is not counted at all. Without a status grouping or filter, only stamped count."""
    by_status = {
        r['k0']: r
        for r in await _fact(
            fiscal, Fact.INVOICE_LINE, dimensions=(DimensionKey.DOCUMENT_STATUS,),
            stamped_only=False,
        )
    }  # fmt: skip
    [stamped_only] = await _fact(fiscal, Fact.INVOICE_LINE, stamped_only=True)

    assert {k: r['invoices'] for k, r in by_status.items()} == {
        'stamped': 2, 'cancelled': 1, 'pending': 1,
    }  # fmt: skip
    assert stamped_only['invoices'] == 2
    # The pending invoice's standalone 20.00; the excluded draft's 30.00 is nowhere.
    pending = engine_derive(_sums(Fact.INVOICE_LINE, by_status['pending']), VatTreatment.EXCLUDED)
    assert pending[MeasureKey.STANDALONE_INVOICED] == Decimal('20.00')
    assert pending[MeasureKey.INVOICED] == Decimal('20.00')


async def test_output_vat_is_invoice_vat_less_credit_note_vat(fiscal: AsyncEngine) -> None:
    """Stamped live invoices' VAT less stamped live credit notes' VAT; the payment receipt is
    listed, not added, and cancelled or pending invoices add nothing whatever the grouping."""
    expected = (
        _document_tax(DOCUMENT_LINES[41]) + _document_tax(DOCUMENT_LINES[42])
        - _document_tax(DOCUMENT_LINES[46])
    )  # fmt: skip
    [stamped] = await _fact(fiscal, Fact.INVOICE_LINE, stamped_only=True)
    [every] = await _fact(fiscal, Fact.INVOICE_LINE, stamped_only=False)

    for row in (stamped, every):
        derived = engine_derive(_sums(Fact.INVOICE_LINE, row), VatTreatment.EXCLUDED)
        assert derived[MeasureKey.OUTPUT_VAT] == expected
        assert derived[MeasureKey.PAYMENT_RECEIPTS] == Decimal('500.00')


def test_the_invoice_fact_compiles_for_mariadb() -> None:
    statement = facts.query(
        Fact.INVOICE_LINE,
        _slice(
            dimensions=(DimensionKey.DATE, DimensionKey.ISSUER, DimensionKey.DOCUMENT_STATUS),
            facility=1,
            stamped_only=True,
        ),
    )
    sql = str(statement.compile(dialect=mysql.dialect())).upper()

    assert 'STRFTIME' not in sql
    assert 'UNION ALL' in sql
    assert 'GROUP BY K0, K1, K2' in sql
    assert 'ROW_NUMBER() OVER (PARTITION BY' in sql
    assert 'FISCAL_DOCUMENT.FACILITY =' in sql


def test_the_coverage_fact_compiles_for_mariadb() -> None:
    statement = facts.query(
        Fact.INVOICE_COVERAGE,
        _slice(dimensions=(DimensionKey.DATE, DimensionKey.SALESPERSON), facility=1),
    )
    sql = str(statement.compile(dialect=mysql.dialect())).upper()

    assert 'STRFTIME' not in sql and 'JULIANDAY' not in sql
    assert 'DATEDIFF(' in sql
    assert 'GROUP BY K0, K1' in sql
    assert 'SALES_ORDER.FACILITY =' in sql


# ── Spec 038, T054: the purchase line fact (research R13) ─────────────────────

PURCHASE_TABLES = [
    t.__table__
    for t in (
        PurchaseOrder, PurchaseOrderDetail, InventoryReceipt, InventoryReceiptDetail, Warehouse,
    )
]  # fmt: skip


def _po(po_id: int, supplier: int, day: date, **kw: object) -> dict:
    """A purchase order confirmed on `day`: completed and not cancelled unless told otherwise."""
    at = datetime(day.year, day.month, day.day, 11)
    return dict(
        purchase_order_id=po_id, supplier=supplier, creation_time=datetime(2026, 4, 1),
        modification_time=at, creator=1, updater=1, completed=kw.get('completed', True),
        cancelled=kw.get('cancelled', False), approved=True,
    )  # fmt: skip


def _po_line(
    line_id: int, po_id: int, qty: str, price: str, warehouse: int | None = 1, **kw: object
) -> dict:
    return dict(
        purchase_order_detail_id=line_id, purchase_order=po_id, product=kw.get('product', 1),
        warehouse=warehouse, quantity=Decimal(qty), price=Decimal(price),
        discount=Decimal(kw.get('discount', '0')), tax_rate=Decimal('0.16'), product_code='P',
        product_name='P', exchange_rate=1, currency=0, tax_included=kw.get('included', False),
    )  # fmt: skip


#: PO 61 (supplier 7, May 5) has a tax-included line with a discount, a line in facility 2's
#: warehouse and two half-cent lines, which round up one by one but not together: the order's
#: residual of −1 cent lands on its largest line, in facility 2. PO 64 (supplier 8, May 8) has a
#: line with no warehouse. PO 62 is cancelled, PO 63 a draft and PO 65 confirmed in June: none of
#: them counts in May.
PURCHASE_LINES = {
    61: [
        _po_line(71, 61, '3', '10.005', discount='0.1', included=True),
        _po_line(72, 61, '2', '50', warehouse=2),
        _po_line(73, 61, '1', '0.005'),
        _po_line(79, 61, '1', '0.005'),
    ],
    62: [_po_line(74, 62, '1', '100')],
    63: [_po_line(75, 63, '1', '100')],
    64: [_po_line(76, 64, '4', '20'), _po_line(77, 64, '1', '10', warehouse=None)],
    65: [_po_line(78, 65, '1', '999')],
}
PURCHASE_ORDERS = [
    _po(61, 7, date(2026, 5, 5)),
    _po(62, 7, date(2026, 5, 6), cancelled=True),
    _po(63, 8, date(2026, 5, 7), completed=False),
    _po(64, 8, date(2026, 5, 8)),
    _po(65, 8, date(2026, 6, 2)),
]


def _receipt(receipt_id: int, po_id: int, **kw: object) -> dict:
    at = datetime(2026, 5, 20, 9)
    return dict(
        inventory_receipt_id=receipt_id, facility=1, creation_time=at, modification_time=at,
        creator=1, updater=1, warehouse=1, purchase_order=po_id,
        completed=kw.get('completed', True), cancelled=kw.get('cancelled', False),
    )  # fmt: skip


def _receipt_line(line_id: int, receipt: int, po_line: int, qty: str) -> dict:
    return dict(
        receipt_detail_id=line_id, receipt=receipt, product=1, purchase_order_detail=po_line,
        quantity=Decimal(qty), quantity_ordered=Decimal(qty),
    )  # fmt: skip


#: Receipt 81 is confirmed; 82 was cancelled and 83 is a draft: only 81's quantities are received.
RECEIPTS = [_receipt(81, 61), _receipt(82, 61, cancelled=True), _receipt(83, 64, completed=False)]
RECEIPT_LINES = [
    _receipt_line(91, 81, 71, '2'),
    _receipt_line(92, 81, 72, '2'),
    _receipt_line(93, 82, 71, '1'),
    _receipt_line(94, 83, 76, '4'),
]


@pytest.fixture
async def purchases() -> AsyncEngine:
    engine = create_async_engine('sqlite+aiosqlite://')
    async with engine.begin() as connection:
        await connection.run_sync(lambda c: Base.metadata.create_all(c, tables=PURCHASE_TABLES))
        await connection.execute(
            insert(Warehouse.__table__),
            [
                {'warehouse_id': 1, 'facility': 1, 'code': 'W1', 'name': 'W1'},
                {'warehouse_id': 2, 'facility': 2, 'code': 'W2', 'name': 'W2'},
            ],
        )
        await connection.execute(insert(PurchaseOrder.__table__), PURCHASE_ORDERS)
        await connection.execute(
            insert(PurchaseOrderDetail.__table__),
            [line for lines in PURCHASE_LINES.values() for line in lines],
        )
        await connection.execute(insert(InventoryReceipt.__table__), RECEIPTS)
        await connection.execute(insert(InventoryReceiptDetail.__table__), RECEIPT_LINES)
    yield engine
    await engine.dispose()


def _po_subtotal_cents(po_id: int) -> int:
    """Spec 031's subtotal of the PO, which ignores the tax-included flag (FR-034)."""
    lines = [
        SimpleNamespace(
            quantity=line['quantity'], price=line['price'], discount=line['discount'],
            tax_rate=line['tax_rate'],
        )
        for line in PURCHASE_LINES[po_id]
    ]  # fmt: skip
    return int(purchase_order_service.order_totals(lines)[0] * 100)


async def test_a_purchase_line_is_quantity_by_price_less_discount_before_tax(
    purchases: AsyncEngine,
) -> None:
    """The tax-included flag is ignored: 3 × 10.005 × 0.9 = 27.0135 is before tax as typed."""
    rows = facts.purchase_lines(date(2026, 5, 1), date(2026, 5, 31), None)
    async with purchases.connect() as connection:
        lines = {r['id']: r for r in (await connection.execute(select(rows))).mappings().all()}

    assert set(lines) == {71, 72, 73, 76, 77, 79}, 'cancelled, draft and June orders are left out'
    assert lines[71]['sub_c'] == 2701
    assert (lines[72]['sub_c'], lines[73]['sub_c'], lines[79]['sub_c']) == (9999, 1, 1)
    assert sum(lines[i]['sub_c'] for i in (71, 72, 73, 79)) == _po_subtotal_cents(61)
    assert (lines[71]['supplier'], lines[71]['facility'], lines[71]['warehouse']) == (7, 1, 1)
    assert lines[72]['facility'] == 2, "each line is placed by its own warehouse's facility"
    assert lines[77]['facility'] is None
    assert lines[71]['at'] == datetime(2026, 5, 5, 11)


async def test_received_quantity_comes_from_confirmed_receipts(purchases: AsyncEngine) -> None:
    rows = facts.purchase_lines(date(2026, 5, 1), date(2026, 5, 31), None)
    async with purchases.connect() as connection:
        lines = {r['id']: r for r in (await connection.execute(select(rows))).mappings().all()}

    assert {i: lines[i]['received'] for i in lines} == {71: 2, 72: 2, 73: 0, 76: 0, 77: 0, 79: 0}


async def test_purchases_group_by_supplier_and_respect_the_line_facility(
    purchases: AsyncEngine,
) -> None:
    [total] = await _fact(purchases, Fact.PURCHASE_LINE)
    by_supplier = {
        r['k0']: r for r in await _fact(purchases, Fact.PURCHASE_LINE, dimensions=(
            DimensionKey.SUPPLIER,
        ))
    }  # fmt: skip
    [scoped] = await _fact(purchases, Fact.PURCHASE_LINE, facility=2)

    assert (total['qty'], total['received'], total['orders']) == (12, 4, 2)
    assert total['sub_c'] == _po_subtotal_cents(61) + 9000
    assert {k: (r['sub_c'], r['qty'], r['orders']) for k, r in by_supplier.items()} == {
        7: (_po_subtotal_cents(61), 7, 1),
        8: (9000, 5, 1),
    }
    # The scope keeps the residual where the whole order placed it.
    assert (scoped['sub_c'], scoped['qty'], scoped['orders']) == (9999, 2, 1)
    derived = engine_derive(_sums(Fact.PURCHASE_LINE, total), VatTreatment.EXCLUDED)
    assert derived[MeasureKey.PURCHASES] == Decimal(total['sub_c']) / 100
    assert derived[MeasureKey.PURCHASE_QUANTITY] == Decimal(12)
    assert derived[MeasureKey.RECEIVED_QUANTITY] == Decimal(4)
    assert derived[MeasureKey.PURCHASE_ORDERS] == Decimal(2)
    assert derived[MeasureKey.AVERAGE_PURCHASE_PRICE] == (
        Decimal(total['sub_c']) / 100 / 12
    ).quantize(Decimal('0.01'), rounding='ROUND_HALF_UP')


def test_average_sale_price_and_the_spread() -> None:
    """Average sale price is gross sales without VAT ÷ units sold; the spread is the difference
    of the two averages, and its % is over the average sale price."""
    sums = {
        'sale_line.sub_c': 30000, 'sale_line.tax_c': 4800, 'sale_line.qty': 4,
        'purchase_line.sub_c': 12000, 'purchase_line.qty': 2,
    }  # fmt: skip
    derived = engine_derive(sums, VatTreatment.INCLUDED)

    assert derived[MeasureKey.AVERAGE_SALE_PRICE] == Decimal('75.00')
    assert derived[MeasureKey.AVERAGE_PURCHASE_PRICE] == Decimal('60.00')
    assert derived[MeasureKey.PURCHASE_SALE_SPREAD] == Decimal('15.00')
    assert derived[MeasureKey.PURCHASE_SALE_SPREAD_PCT] == Decimal('0.2000')
    nothing = engine_derive({}, VatTreatment.EXCLUDED)
    assert nothing[MeasureKey.AVERAGE_PURCHASE_PRICE] is None
    assert nothing[MeasureKey.AVERAGE_SALE_PRICE] is None
    assert nothing[MeasureKey.PURCHASE_SALE_SPREAD] is None
    assert nothing[MeasureKey.PURCHASE_SALE_SPREAD_PCT] is None


def test_the_purchase_fact_compiles_for_mariadb() -> None:
    statement = facts.query(
        Fact.PURCHASE_LINE,
        _slice(
            dimensions=(DimensionKey.DATE, DimensionKey.SUPPLIER, DimensionKey.WAREHOUSE),
            facility=1,
        ),
    )
    sql = str(statement.compile(dialect=mysql.dialect())).upper()

    assert 'STRFTIME' not in sql
    assert 'GROUP BY K0, K1, K2' in sql
    assert 'ROW_NUMBER() OVER (PARTITION BY' in sql
    assert 'PLACED_LINES.FACILITY =' in sql, 'scoped after the residual is placed'
    assert 'PURCHASE_ORDER.MODIFICATION_TIME >=' in sql
    assert 'INVENTORY_RECEIPT.COMPLETED' in sql
