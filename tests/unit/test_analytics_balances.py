"""Spec 038, T012: the stock balance fact over fixture ledger entries (SQLite), research R2–R5.

Stock at the end of a day is the ledger sum before the next day, the rule spec 030's
`stock_ledger.on_hand` and kardex use, so every figure here is checked against those.
"""

from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import local_now
from app.core.deps import CurrentUser
from app.schemas.analytics import (
    AnalyticsQueryRequest,
    DimensionKey,
    Grain,
    MeasureKey,
    StockOptions,
)
from app.services import kardex_service, stock_ledger
from app.services.analytics import balances, facts
from app.services.analytics import engine as analytics
from tests.integration.conftest import db, engine, seeded, sessions  # noqa: F401
from tests.integration.inventory_seed import Inventory, cost, product, reserve, setup_inventory
from tests.integration.stock_seed import T, entry

pytestmark = pytest.mark.usefixtures('seeded')

ADMIN = CurrentUser(
    user_id='tester', session_version=1, administrator=True, facility_id=1, employee_id=1
)
MARCH = date(2026, 3, 31)


@pytest.fixture
async def site(db: AsyncSession) -> Inventory:  # noqa: F811
    """Two facilities' warehouses and transit warehouses, with entries over three months."""
    found = await setup_inventory(db)
    p = found.stockable
    await entry(db, p, found.a.warehouse, '10', datetime(2026, 1, 10, 9))
    await entry(db, p, found.a.warehouse, '-3', datetime(2026, 2, 3, 12), source=T.SALES_ORDER)
    await entry(db, p, found.a.warehouse, '5', datetime(2026, 3, 31, 23, 59))
    await entry(db, p, found.a.warehouse, '-1', datetime(2026, 4, 1, 0, 0), source=T.SALES_ORDER)
    await entry(db, p, found.b.warehouse, '7', datetime(2026, 2, 20, 8))
    return found


def _slice(*dimensions: DimensionKey, moments: tuple[date, ...], **kw: object) -> facts.Slice:
    return facts.Slice(dimensions=tuple(dimensions), moments=moments, **kw)


def _by(rows: list[dict], *names: str) -> dict:
    return {tuple(row[n] for n in names): row for row in rows}


async def test_on_hand_today_equals_the_ledger_per_pair(db: AsyncSession, site: Inventory) -> None:  # noqa: F811
    today = local_now().date()
    rows = await balances.stock(
        db,
        _slice(DimensionKey.PRODUCT, DimensionKey.WAREHOUSE, moments=(today,)),
    )

    for (p, w), row in _by(rows, 'k0', 'k1').items():
        assert row['on_hand'] == await stock_ledger.on_hand(db, product=p, warehouse=w)
    assert _by(rows, 'k0', 'k1')[(site.stockable, site.a.warehouse)]['on_hand'] == 11


async def test_on_hand_as_of_a_past_day_equals_the_kardex_balance(
    db: AsyncSession,  # noqa: F811
    site: Inventory,
) -> None:
    """The end of March 31 includes an entry at 23:59 and leaves out one at 00:00 on April 1."""
    p, w = site.stockable, site.a.warehouse
    rows = await balances.stock(
        db, _slice(DimensionKey.WAREHOUSE, moments=(MARCH,), filters={DimensionKey.PRODUCT: (p,)})
    )

    kardex = await kardex_service.kardex(
        db, product=p, warehouse=w, from_=date(2026, 1, 1), to=MARCH
    )
    assert _by(rows, 'k0')[(w,)]['on_hand'] == kardex.closing == 12


async def test_each_month_bucket_is_the_as_of_sum_at_its_end(
    db: AsyncSession,  # noqa: F811
    site: Inventory,
) -> None:
    """Opening plus cumulative movements (R2) gives what one as-of read gives at every month-end."""
    definition = AnalyticsQueryRequest(
        measures=[MeasureKey.ON_HAND],
        dimensions=[DimensionKey.DATE],
        grain=Grain.MONTH,
        start=date(2026, 1, 1),
        end=date(2026, 4, 30),
    )
    answer = await analytics.run(db, definition, ADMIN)

    by_month = {row.keys[0].value: row.values['on_hand'] for row in answer.rows}
    for month, end in (
        ('2026-01-01', date(2026, 1, 31)),
        ('2026-02-01', date(2026, 2, 28)),
        ('2026-03-01', MARCH),
        ('2026-04-01', date(2026, 4, 30)),
    ):
        alone = await balances.stock(db, _slice(moments=(end,)))
        assert by_month[month] == analytics.quantity(alone[0]['on_hand']), month
    assert by_month == {
        '2026-01-01': Decimal('10.0000'),
        '2026-02-01': Decimal('14.0000'),
        '2026-03-01': Decimal('19.0000'),
        '2026-04-01': Decimal('18.0000'),
    }
    # A balance is never added across months: the totals are the period's end.
    assert answer.totals.values['on_hand'] == Decimal('18.0000')


async def test_transit_stock_counts_once_for_the_destination_facility(
    db: AsyncSession,  # noqa: F811
    site: Inventory,
) -> None:
    """A transfer from A to B leaves A and lands in B's transit warehouse: it is B's stock, in
    transit, and the company total is unchanged."""
    p = site.stockable
    await entry(db, p, site.a.warehouse, '-4', datetime(2026, 3, 1), source=T.INVENTORY_TRANSFER)
    await entry(db, p, site.b.transit, '4', datetime(2026, 3, 1), source=T.INVENTORY_TRANSFER)

    rows = await balances.stock(db, _slice(DimensionKey.FACILITY, moments=(MARCH,)))
    total = await balances.stock(db, _slice(moments=(MARCH,)))

    by_facility = _by(rows, 'k0')
    assert by_facility[(site.a.facility,)]['on_hand'] == 8
    assert by_facility[(site.b.facility,)]['on_hand'] == 11
    assert by_facility[(site.b.facility,)]['in_transit'] == 4
    assert by_facility[(site.a.facility,)]['in_transit'] == 0
    assert total[0]['on_hand'] == 19 == sum(row['on_hand'] for row in rows)


async def test_reserved_and_available_are_today_only(db: AsyncSession, site: Inventory) -> None:  # noqa: F811
    p, w = site.stockable, site.a.warehouse
    await reserve(db, p, w, '2')
    today = local_now().date()

    now = await balances.stock_today(db, _slice(DimensionKey.WAREHOUSE, moments=(today,)))
    past = await balances.stock_today(db, _slice(DimensionKey.WAREHOUSE, moments=(MARCH,)))

    expected = (await stock_ledger.reserved_by_warehouse(db, products={p}))[(p, w)]
    assert _by(now, 'k0')[(w,)]['reserved'] == expected == 2
    assert _by(now, 'k0')[(w,)]['available'] == 11 - 2
    assert past == []

    definition = AnalyticsQueryRequest(
        measures=[MeasureKey.ON_HAND, MeasureKey.RESERVED, MeasureKey.AVAILABLE],
        start=date(2026, 3, 1),
        end=MARCH,
    )
    answer = await analytics.run(db, definition, ADMIN)
    assert answer.totals.values['reserved'] is None
    assert answer.totals.values['available'] is None
    assert answer.totals.values['on_hand'] == Decimal('19.0000')


async def test_value_is_todays_cost_and_placeholders_are_uncosted(
    db: AsyncSession,  # noqa: F811
    site: Inventory,
) -> None:
    """R4: list-0 cost without VAT; a cost of 0 or 99,999 is uncosted, has no value, lowers the
    coverage and adds its on hand to the uncosted quantity."""
    zero = await product(db, code='ZERO')
    placeholder = await product(db, code='PH')
    await cost(db, site.stockable, '2.50')
    await cost(db, zero, '0')
    await cost(db, placeholder, '99999')
    await entry(db, zero, site.a.warehouse, '4', datetime(2026, 3, 2))
    await entry(db, placeholder, site.a.warehouse, '6', datetime(2026, 3, 2))

    definition = AnalyticsQueryRequest(
        measures=[
            MeasureKey.STOCK_VALUE,
            MeasureKey.STOCK_COST_COVERAGE,
            MeasureKey.UNCOSTED_QUANTITY,
            MeasureKey.ON_HAND,
        ],
        start=date(2026, 3, 1),
        end=MARCH,
    )
    totals = (await analytics.run(db, definition, ADMIN)).totals.values

    # 19 on hand of the costed product × 2.50; the other two pairs are uncosted.
    assert totals['stock_value'] == Decimal('47.50')
    assert totals['stock_cost_coverage'] == Decimal('0.5000')  # 2 costed pairs of 4 stocked
    assert totals['uncosted_quantity'] == Decimal('10.0000')
    assert totals['on_hand'] == Decimal('29.0000')


async def test_negative_on_hand_is_a_stock_out_and_adds_no_value(
    db: AsyncSession,  # noqa: F811
    site: Inventory,
) -> None:
    p = await product(db, code='NEG')
    await cost(db, p, '10')
    await entry(db, p, site.a.warehouse, '-2', datetime(2026, 3, 20), source=T.SALES_ORDER)

    rows = await balances.stock(
        db,
        _slice(
            moments=(MARCH,), filters={DimensionKey.PRODUCT: (p,)}, stock_options=StockOptions()
        ),
    )

    assert rows[0]['on_hand'] == -2
    assert rows[0]['value_c'] == 0
    assert rows[0]['stock_outs'] == 1
