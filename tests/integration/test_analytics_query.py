"""Spec 037: the query against real tables (SQLite), from fixture documents.

US1 (T014): one definition of each measure, reconciled with the sales order totals and the cash cut.
US2 (T022) and US6 (T053) extend it.
"""

from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import PaymentTerms, PaymentType
from app.services import totals
from app.services.analytics import cache
from app.services.cash_session_service import cut_figures
from tests.integration.analytics_seed import (
    CREDIT_CUSTOMER,
    L,
    cash_session,
    credit_note,
    payment,
    refund,
    sale,
    seed_catalog,
)

pytestmark = pytest.mark.usefixtures('seeded')

DAY = date(2026, 5, 4)


@pytest.fixture(autouse=True)
def _fresh_cache() -> Iterator[None]:
    """The response cache is per process; each test starts without another test's answers."""
    cache.responses.clear()
    yield
    cache.responses.clear()


AT = datetime(2026, 5, 4, 10, 30)
LATER = datetime(2026, 5, 20, 12, 0)


async def _query(client: AsyncClient, **definition: object) -> dict:
    body = {'start': DAY.isoformat(), 'end': DAY.isoformat(), **definition}
    response = await client.post('/api/v1/analytics/query', json=body)
    assert response.status_code == 200, response.text
    return response.json()


def _total(lines: list[L]) -> totals.DocumentTotals:
    return totals.document_totals(
        totals.Line(
            quantity=Decimal(line.qty),
            price=Decimal(line.price),
            discount_rate=Decimal(line.discount),
            tax_rate=Decimal(line.rate),
            tax_included=line.included,
        )
        for line in lines
    )


FIRST = [L('3', '115.50', cost='80'), L('1', '9.99', included=False)]
SECOND = [L('2', '100', discount='0.125', cost='50'), L('5', '13.37', rate='0')]


@pytest.fixture
async def day(db: AsyncSession) -> dict:
    """Two sales on DAY, a refund of part of the first, and a CFDI credit note."""
    await seed_catalog(db)
    first, first_lines = await sale(db, AT, FIRST)
    await sale(db, AT, SECOND, customer=CREDIT_CUSTOMER)
    await refund(db, first, [(first_lines[0], '1', FIRST[0])], datetime(2026, 5, 4, 17, 0))
    await credit_note(db, datetime(2026, 5, 4, 18, 0), [L('1', '58', rate='0.16')], serial=7101)
    return {'first': first, 'first_lines': first_lines}


async def test_net_sales_are_gross_less_refunds_and_credit_notes(
    client: AsyncClient, day: dict
) -> None:
    measures = ['gross_sales', 'refunds', 'credit_notes', 'net_sales']
    excluded = (await _query(client, measures=measures))['totals']['values']
    included = (await _query(client, measures=measures, vat='included'))['totals']['values']

    first, second = _total(FIRST), _total(SECOND)
    refunded = _total([L('1', '115.50')])
    note = _total([L('1', '58')])
    assert Decimal(included['gross_sales']) == first.total + second.total
    assert Decimal(excluded['gross_sales']) == first.subtotal + second.subtotal
    assert Decimal(included['refunds']) == refunded.total
    assert Decimal(included['credit_notes']) == note.total
    assert Decimal(included['net_sales']) == (
        first.total + second.total - refunded.total - note.total
    )
    assert Decimal(excluded['net_sales']) == (
        first.subtotal + second.subtotal - refunded.subtotal - note.subtotal
    )


async def test_an_order_cancelled_after_confirmation_counts_nothing(
    client: AsyncClient, db: AsyncSession
) -> None:
    await sale(db, AT, [L('1', '500')], cancelled=True)

    values = (await _query(client, measures=['gross_sales', 'orders']))['totals']['values']

    assert Decimal(values['gross_sales']) == 0
    assert Decimal(values['orders']) == 0


async def test_a_refund_nets_on_its_own_date_and_facility(
    client: AsyncClient, db: AsyncSession
) -> None:
    from tests.integration.seed import seed_facility

    await seed_facility(db)
    order, lines = await sale(db, AT, [L('2', '116')])
    await refund(db, order, [(lines[0], '1', L('2', '116'))], LATER, facility=2)

    sale_day = await _query(client, measures=['net_sales'], dimensions=['facility'])
    refund_day = await _query(
        client,
        measures=['net_sales', 'refunds'],
        dimensions=['facility'],
        start='2026-05-20',
        end='2026-05-20',
    )

    assert [r['values']['net_sales'] for r in sale_day['rows']] == ['200.00']
    assert [(r['keys'][0]['value'], r['values']['refunds']) for r in refund_day['rows']] == [
        ('2', '100.00')
    ]
    assert refund_day['totals']['values']['net_sales'] == '-100.00'


async def test_a_cancelled_credit_note_reduces_nothing(
    client: AsyncClient, db: AsyncSession
) -> None:
    await credit_note(db, AT, [L('1', '116')], serial=7201, cancelled=True)
    await credit_note(db, AT, [L('1', '58')], serial=7202)

    values = (await _query(client, measures=['credit_notes', 'net_sales']))['totals']['values']

    assert values['credit_notes'] == '50.00'
    assert values['net_sales'] == '-50.00'


async def test_lines_without_cost_are_left_out_of_margin(
    client: AsyncClient, db: AsyncSession
) -> None:
    # 116 with VAT, cost 58 with VAT: subtotal 100, cost 50, margin 50. The 232 line has no cost.
    await sale(db, AT, [L('1', '116', cost='58'), L('2', '116')])
    measures = ['net_sales', 'costed_net_sales', 'cost_coverage', 'cost_of_sales']
    values = (await _query(client, measures=[*measures, 'gross_margin', 'margin_pct']))['totals'][
        'values'
    ]

    assert values['net_sales'] == '300.00'
    assert values['costed_net_sales'] == '100.00'
    assert values['cost_coverage'] == '0.3333'
    assert values['cost_of_sales'] == '50.00'
    assert values['gross_margin'] == '50.00'
    assert values['margin_pct'] == '0.5000'


async def test_with_no_costed_line_margin_pct_is_null(
    client: AsyncClient, db: AsyncSession
) -> None:
    await sale(db, AT, [L('1', '116')])

    values = (await _query(client, measures=['margin_pct', 'cost_coverage']))['totals']['values']

    assert values == {'margin_pct': None, 'cost_coverage': '0.0000'}


async def test_collected_equals_the_cash_cut(client: AsyncClient, db: AsyncSession) -> None:
    session = await cash_session(db, AT)
    order, _ = await sale(db, AT, [L('1', '464')])
    await payment(db, AT, '500', session=session, applied_to=order, change='36')
    await payment(db, AT, '200', method=4, session=session)
    await payment(db, AT, '-80', session=session, kind=PaymentType.IMMEDIATE)
    await payment(db, AT, '120', session=session, kind=PaymentType.CREDIT_NOTE, method=0)

    result = await _query(
        client,
        measures=['collected', 'refunds_paid_out', 'store_credit_issued', 'payments'],
        dimensions=['payment_method'],
        filters=[{'dimension': 'cash_session', 'values': [str(session)]}],
    )
    cut = await cut_figures(db, session)

    collected = {
        int(r['keys'][0]['value']): Decimal(r['values']['collected'])
        for r in result['rows']
        if Decimal(r['values']['collected'])
    }
    refunded = {
        int(r['keys'][0]['value']): Decimal(r['values']['refunds_paid_out'])
        + Decimal(r['values']['store_credit_issued'])
        for r in result['rows']
        if Decimal(r['values']['refunds_paid_out']) or Decimal(r['values']['store_credit_issued'])
    }
    assert collected == dict(cut.sales_by_method)
    assert refunded == dict(cut.refunds_by_method)
    assert result['totals']['values']['payments'] == '2'


async def test_store_credit_spent_later_is_not_collected_again(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Spending store credit is an application of the credit-note payment, never a new payment."""
    order, _ = await sale(db, AT, [L('1', '116')])
    await payment(db, AT, '116', kind=PaymentType.CREDIT_NOTE, method=0, applied_to=order)

    values = (await _query(client, measures=['collected', 'store_credit_issued']))['totals'][
        'values'
    ]

    assert values == {'collected': '0.00', 'store_credit_issued': '116.00'}


async def test_an_empty_period_answers_zero(client: AsyncClient) -> None:
    result = await _query(
        client, measures=['net_sales', 'orders', 'margin_pct'], dimensions=['product']
    )

    assert result['rows'] == []
    assert result['totals']['values'] == {
        'net_sales': '0.00',
        'orders': '0',
        'margin_pct': None,
    }
    assert result['as_of']


# ── US2 (T022): every dimension, filters, labels, top N ───────────────────────


@pytest.fixture
async def month(db: AsyncSession) -> None:
    """Sales across products, customers, salespeople and days of May 2026."""
    from tests.integration.seed import seed_facility

    await seed_catalog(db)
    await seed_facility(db)
    await sale(db, datetime(2026, 5, 4, 9, 15), [L('1', '116', product=1), L('2', '58', product=2)])
    await sale(
        db, datetime(2026, 5, 11, 13, 0), [L('4', '116', product=3)],
        customer=CREDIT_CUSTOMER, terms=PaymentTerms.NET_D, facility=2,
    )  # fmt: skip
    await sale(db, datetime(2026, 5, 17, 18, 45), [L('1', '232', product=2)])
    await credit_note(db, datetime(2026, 5, 20, 12, 0), [L('1', '11.60')], serial=7301)


MAY = {'start': '2026-05-01', 'end': '2026-05-31'}
ADDITIVE = ['gross_sales', 'refunds', 'credit_notes', 'net_sales', 'units', 'orders']


def _sum(rows: list[dict], measure: str) -> Decimal:
    return sum((Decimal(r['values'][measure]) for r in rows), Decimal(0))


@pytest.mark.parametrize(
    'dimension',
    ['product', 'customer', 'salesperson', 'brand', 'model', 'point_of_sale', 'payment_terms',
     'customer_type', 'facility', 'weekday', 'hour'],
)  # fmt: skip
async def test_rows_add_up_to_the_totals(client: AsyncClient, month: None, dimension: str) -> None:
    measures = [
        m for m in ADDITIVE if not (m == 'orders' and dimension in ('product', 'brand', 'model'))
    ]
    result = await _query(client, measures=measures, dimensions=[dimension], **MAY)

    for measure in measures:
        assert _sum(result['rows'], measure) == Decimal(result['totals']['values'][measure]), (
            dimension,
            measure,
        )


@pytest.mark.parametrize(
    ('grain', 'expected'),
    [
        ('day', ['2026-05-04', '2026-05-11', '2026-05-17', '2026-05-20']),
        ('week', ['2026-05-04', '2026-05-11', '2026-05-18']),
        ('month', ['2026-05-01']),
        ('quarter', ['2026-04-01']),
        ('year', ['2026-01-01']),
    ],
)
async def test_date_at_each_grain(
    client: AsyncClient, month: None, grain: str, expected: list
) -> None:
    result = await _query(client, measures=['net_sales'], dimensions=['date'], grain=grain, **MAY)

    assert [r['keys'][0]['value'] for r in result['rows']] == expected


async def test_weekday_by_hour(client: AsyncClient, month: None) -> None:
    result = await _query(client, measures=['orders'], dimensions=['weekday', 'hour'], **MAY)

    cells = {
        (r['keys'][0]['value'], r['keys'][1]['value']): r['values']['orders']
        for r in result['rows']
    }
    # May 4 and 11 are Mondays, May 17 a Sunday. Orders read only the sales, so the credit note's
    # Wednesday has no cell.
    assert cells == {('1', '9'): '1', ('1', '13'): '1', ('7', '18'): '1'}


async def test_filters_narrow_to_the_values_given(client: AsyncClient, month: None) -> None:
    by_customer = await _query(
        client, measures=['net_sales'], filters=[{'dimension': 'customer', 'values': ['2']}], **MAY
    )
    by_terms = await _query(
        client,
        measures=['net_sales'],
        filters=[{'dimension': 'payment_terms', 'values': ['credit']}],
        **MAY,
    )
    by_type = await _query(
        client,
        measures=['net_sales'],
        filters=[{'dimension': 'customer_type', 'values': ['credit']}],
        **MAY,
    )

    assert by_customer['totals']['values']['net_sales'] == '400.00'
    assert by_terms['totals']['values'] == by_type['totals']['values'] == {'net_sales': '400.00'}


async def test_brand_groups_trimmed_and_case_insensitive(client: AsyncClient, month: None) -> None:
    result = await _query(client, measures=['units'], dimensions=['brand'], **MAY)
    filtered = await _query(
        client, measures=['units'], filters=[{'dimension': 'brand', 'values': ['CEMEX']}], **MAY
    )

    rows = {r['keys'][0]['value']: r['values']['units'] for r in result['rows']}
    assert rows == {'cemex': '7.0000', None: '1.0000'}
    labels = {r['keys'][0]['label'] for r in result['rows']}
    assert labels <= {'Cemex', 'cemex', '(none)'}
    assert filtered['totals']['values']['units'] == '7.0000'


async def test_label_rows_overlap_and_the_totals_stay_true(
    client: AsyncClient, month: None
) -> None:
    result = await _query(client, measures=['units'], dimensions=['label'], **MAY)

    rows = {r['keys'][0]['label']: r['values']['units'] for r in result['rows']}
    # Product 2 (3 units) carries both labels; product 3 (4 units) one; product 1 (1 unit) none.
    assert rows == {'Cementos': '7.0000', 'Obra negra': '3.0000', '(none)': '1.0000'}
    assert result['overlapping'] is True
    assert result['totals']['values']['units'] == '8.0000'


async def test_missing_values_group_under_none(client: AsyncClient, month: None) -> None:
    by_model = await _query(client, measures=['units'], dimensions=['model'], **MAY)
    by_product = await _query(client, measures=['net_sales'], dimensions=['product'], **MAY)

    assert {r['keys'][0]['value'] for r in by_model['rows']} == {'gris 50', None}
    none = [r for r in by_product['rows'] if r['keys'][0]['value'] is None]
    assert [r['values']['net_sales'] for r in none] == ['-10.00']  # the credit note


async def test_top_three_and_others(client: AsyncClient, db: AsyncSession) -> None:
    for i, price in enumerate(['116', '232', '348', '464', '580']):
        await sale(db, AT, [L('1', price)], salesperson=1, customer=1)
        del i
    await sale(db, AT, [L('1', '1160')])

    result = await _query(client, measures=['net_sales'], dimensions=['date'], top=3)
    by_order = await _query(
        client, measures=['net_sales', 'orders'], dimensions=['customer'], top=1
    )

    assert len(result['rows']) == 1 and result['others'] is None
    assert by_order['rows'][0]['values']['net_sales'] == by_order['totals']['values']['net_sales']


async def test_top_n_folds_the_rest_into_others(client: AsyncClient, month: None) -> None:
    result = await _query(client, measures=['net_sales'], dimensions=['product'], top=2, **MAY)

    assert [r['values']['net_sales'] for r in result['rows']] == ['400.00', '300.00']
    assert result['others']['values']['net_sales'] == '90.00'
    total = _sum(result['rows'], 'net_sales') + Decimal(result['others']['values']['net_sales'])
    assert total == Decimal(result['totals']['values']['net_sales'])


async def test_sort_by_a_measure_and_by_a_dimension(client: AsyncClient, month: None) -> None:
    desc = await _query(
        client, measures=['net_sales'], dimensions=['product'], sort='-net_sales', **MAY
    )
    asc = await _query(
        client, measures=['net_sales'], dimensions=['product'], sort='product', **MAY
    )

    assert [r['values']['net_sales'] for r in desc['rows']] == [
        '400.00',
        '300.00',
        '100.00',
        '-10.00',
    ]
    assert [r['keys'][0]['value'] for r in asc['rows']] == ['1', '2', '3', None]


async def test_each_row_carries_the_filter_that_reproduces_it(
    client: AsyncClient, month: None
) -> None:
    result = await _query(client, measures=['net_sales'], dimensions=['product'], **MAY)

    for row in result['rows']:
        again = await _query(client, measures=['net_sales'], filters=row['drill'], **MAY)
        assert again['totals']['values'] == row['values']


async def test_comparison_against_the_previous_period(
    client: AsyncClient, db: AsyncSession
) -> None:
    await sale(db, datetime(2026, 4, 6, 10, 0), [L('1', '116')])  # a Monday, in April
    await sale(db, datetime(2026, 5, 4, 10, 0), [L('1', '348')])

    result = await _query(
        client, measures=['net_sales'], dimensions=['date'], grain='week',
        start='2026-05-04', end='2026-05-10', comparison='same_period_last_year',
    )  # fmt: skip
    previous = await _query(
        client, measures=['net_sales'], start='2026-05-04', end='2026-05-10',
        comparison='previous_period',
    )  # fmt: skip

    assert result['totals']['comparison'] == {'net_sales': '0.00'}
    assert result['totals']['change_pct'] == {'net_sales': None}
    assert previous['totals']['comparison'] == {'net_sales': '0.00'}
    april = await _query(
        client, measures=['net_sales'], start='2026-05-04', end='2026-05-31',
        comparison='previous_period',
    )  # fmt: skip
    assert april['totals']['comparison'] == {'net_sales': '100.00'}
    assert april['totals']['change'] == {'net_sales': '200.00'}
    assert april['totals']['change_pct'] == {'net_sales': '2.0000'}


# ── US6 (T053): payments received, reconciled with the cash cuts ──────────────


async def test_collected_over_two_sessions_equals_their_cuts(
    client: AsyncClient, db: AsyncSession
) -> None:
    morning = await cash_session(db, datetime(2026, 5, 4, 8, 0))
    evening = await cash_session(db, datetime(2026, 5, 4, 15, 0))
    order, _ = await sale(db, AT, [L('1', '464')])
    await payment(db, AT, '500', session=morning, applied_to=order, change='36')
    await payment(db, AT, '300', method=3, session=evening)
    await payment(db, AT, '-40', session=evening)
    await payment(db, AT, '60', session=evening, kind=PaymentType.CREDIT_NOTE, method=0)

    result = await _query(
        client,
        measures=['collected', 'refunds_paid_out', 'store_credit_issued'],
        dimensions=['payment_method'],
    )
    cuts = [await cut_figures(db, session) for session in (morning, evening)]

    sales: dict[int, Decimal] = {}
    refunds: dict[int, Decimal] = {}
    for cut in cuts:
        for method, amount in cut.sales_by_method:
            sales[method] = sales.get(method, Decimal(0)) + amount
        for method, amount in cut.refunds_by_method:
            refunds[method] = refunds.get(method, Decimal(0)) + amount
    rows = {int(r['keys'][0]['value']): r['values'] for r in result['rows']}
    assert {m: Decimal(v['collected']) for m, v in rows.items() if Decimal(v['collected'])} == sales
    assert {
        m: Decimal(v['refunds_paid_out']) + Decimal(v['store_credit_issued'])
        for m, v in rows.items()
        if Decimal(v['refunds_paid_out']) or Decimal(v['store_credit_issued'])
    } == refunds
    assert rows[0]['store_credit_issued'] == '60.00'  # store credit has method N/A


async def test_a_cash_refund_is_paid_out_never_negative_collected(
    client: AsyncClient, db: AsyncSession
) -> None:
    await payment(db, AT, '200')
    await payment(db, AT, '-80')

    values = (await _query(client, measures=['collected', 'refunds_paid_out']))['totals']['values']

    assert values == {'collected': '200.00', 'refunds_paid_out': '80.00'}


async def test_payments_by_salesperson_use_the_sessions_cashier(
    client: AsyncClient, db: AsyncSession
) -> None:
    session = await cash_session(db, AT, cashier=1)
    await payment(db, AT, '150', session=session)
    await payment(db, AT, '50')  # no session: no cashier

    result = await _query(client, measures=['collected'], dimensions=['salesperson'])

    rows = {
        r['keys'][0]['value']: (r['keys'][0]['label'], r['values']['collected'])
        for r in result['rows']
    }
    assert rows == {'1': ('Ana Ruiz', '150.00'), None: ('(none)', '50.00')}


async def test_a_cash_session_filter_keeps_one_session(
    client: AsyncClient, db: AsyncSession
) -> None:
    first = await cash_session(db, AT)
    second = await cash_session(db, AT)
    await payment(db, AT, '100', session=first)
    await payment(db, AT, '250', session=second)

    result = await _query(
        client,
        measures=['collected', 'payments'],
        filters=[{'dimension': 'cash_session', 'values': [str(second)]}],
    )

    assert result['totals']['values'] == {'collected': '250.00', 'payments': '1'}
