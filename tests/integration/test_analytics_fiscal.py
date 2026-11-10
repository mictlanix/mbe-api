"""Spec 038, T043: invoices, coverage, status and output VAT against real tables (SQLite), R12.

May 2026 sells three orders, without VAT:
- A, facility 1, customer 1, salesperson 1: 2 units, 200.00;
- B, facility 2, customer 2, salesperson 2: 1 unit, 500.00;
- C, facility 1, customer 2, salesperson 1: 4 units, 100.00.

Invoice 1 (facility 1, May 10) invoices one unit of A and a standalone line of 100.00. Invoice 2
(facility 2, June 3) invoices B whole. Invoice 3 (facility 2, May 15) is standalone, 50.00. A
cancelled invoice (May 12) invoiced C, and a payment receipt of 300.00 (May 20) completes it.
So May sold 800.00, of which 600.00 is invoiced: coverage 0.75.
"""

from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_current_user
from app.enums import AccessRight, CurrencyCode, EntityStatus, SystemObject
from app.main import app as fastapi_app
from app.models.core import Employee
from app.models.fiscal import FiscalDocumentDetail
from app.models.user import AccessPrivilege
from app.services.analytics import cache
from tests.integration.analytics_seed import CREDIT_CUSTOMER, L, sale, seed_catalog
from tests.integration.detail_export import assert_export_matches
from tests.integration.seed import seed_facility, seed_fiscal_document
from tests.problem import assert_problem

pytestmark = pytest.mark.usefixtures('seeded')

URL = '/api/v1/analytics'
MAY = {'start': '2026-05-01', 'end': '2026-05-31'}
D = Decimal
CLERK = CurrentUser(
    user_id='tester', session_version=1, administrator=False, facility_id=1, employee_id=1
)


@pytest.fixture(autouse=True)
def _fresh_cache() -> Iterator[None]:
    cache.responses.clear()
    yield
    cache.responses.clear()


async def invoice(
    db: AsyncSession,
    at: datetime,
    lines: list[tuple[int | None, str, str]],
    *,
    serial: int,
    fixture: str = 'invoice',
    **columns: object,
) -> int:
    """A fiscal document issued at `at` with `(sale line, quantity, price without VAT)` lines."""
    document = await seed_fiscal_document(db, fixture, serial=serial, issued=at, **columns)
    await db.execute(delete(FiscalDocumentDetail).where(FiscalDocumentDetail.document == document))
    db.add_all(
        FiscalDocumentDetail(
            document=document, product=1, order_detail=line, product_name='Producto',
            quantity=D(qty), price=D(price), discount=D(0), tax_rate=D('0.16'),
            exchange_rate=D(1), currency=CurrencyCode.MXN, tax_included=False,
        )
        for line, qty, price in lines
    )  # fmt: skip
    await db.commit()
    return document


@pytest.fixture
async def book(db: AsyncSession) -> dict[str, int]:
    other = await seed_facility(db)
    await seed_catalog(db)
    db.add(
        Employee(
            employee_id=2, first_name='Bea', last_name='Soto', nickname='bea', gender=2,
            birthday=datetime(1991, 1, 1).date(), sales_person=True, status=EntityStatus.ACTIVE,
            start_job_date=datetime(2021, 1, 1).date(),
        )
    )  # fmt: skip
    await db.commit()
    _, [a] = await sale(db, datetime(2026, 5, 4, 10), [L('2', '116')])
    _, [b] = await sale(
        db, datetime(2026, 5, 5, 10), [L('1', '580')], facility=other, customer=CREDIT_CUSTOMER,
        salesperson=2,
    )  # fmt: skip
    _, [c] = await sale(db, datetime(2026, 5, 6, 10), [L('4', '29')], customer=CREDIT_CUSTOMER)
    first = await invoice(
        db, datetime(2026, 5, 10, 12), [(a, '1', '100'), (None, '1', '100')], serial=1
    )
    await invoice(db, datetime(2026, 6, 3, 12), [(b, '1', '500')], serial=2, facility=other)
    third = await invoice(
        db, datetime(2026, 5, 15, 12), [(None, '1', '50')], serial=3, facility=other
    )
    await invoice(
        db, datetime(2026, 5, 12, 12), [(c, '4', '25')], serial=4, cancelled=True,
    )  # fmt: skip
    receipt = await seed_fiscal_document(
        db, 'payment', serial=5, issued=datetime(2026, 5, 20, 12), payment_amount=D('300')
    )
    return {'a': a, 'b': b, 'c': c, 'first': first, 'third': third, 'receipt': receipt}


async def _query(client: AsyncClient, *measures: str, **body: object) -> dict:
    response = await client.post(f'{URL}/query', json={'measures': list(measures), **MAY, **body})
    assert response.status_code == 200, response.text
    return response.json()


async def _detail(client: AsyncClient, kind: str, **body: object) -> dict:
    response = await client.post(f'{URL}/detail', json={'kind': kind, **MAY, 'limit': 100, **body})
    assert response.status_code == 200, response.text
    return response.json()


def _rows(body: dict, *measures: str) -> dict[tuple, tuple]:
    return {
        tuple(k['value'] for k in row['keys']): tuple(D(row['values'][m]) for m in measures)
        for row in body['rows']
    }


def _totals(body: dict) -> dict[str, Decimal | None]:
    return {k: None if v is None else D(v) for k, v in body['totals']['values'].items()}


async def _grant(db: AsyncSession, *objects: SystemObject) -> None:
    for system_object in objects:
        db.add(
            AccessPrivilege(
                user_id='tester', system_object=int(system_object), privileges=AccessRight.READ
            )
        )
    await db.commit()
    cache.responses.clear()


def _as_clerk() -> None:
    fastapi_app.dependency_overrides[get_current_user] = lambda: CLERK


COVERAGE = ('invoiced_sales', 'uninvoiced_sales')


# ── Coverage ──────────────────────────────────────────────────────────────────


async def test_coverage_by_facility_customer_and_salesperson(
    client: AsyncClient, book: dict[str, int]
) -> None:
    total = await _query(client, *COVERAGE, 'invoice_coverage', 'gross_sales')
    by_facility = await _query(client, *COVERAGE, dimensions=['facility'])
    by_customer = await _query(client, *COVERAGE, dimensions=['customer'])
    by_salesperson = await _query(client, *COVERAGE, dimensions=['salesperson'])

    assert _totals(total) == {
        'invoiced_sales': D('600.00'), 'uninvoiced_sales': D('200.00'),
        'invoice_coverage': D('0.7500'), 'gross_sales': D('800.00'),
    }  # fmt: skip
    assert _rows(by_facility, *COVERAGE) == {
        ('1',): (D('100.00'), D('200.00')),
        ('2',): (D('500.00'), D('0.00')),
    }
    assert _rows(by_customer, *COVERAGE) == {
        ('1',): (D('100.00'), D('100.00')),
        (str(CREDIT_CUSTOMER),): (D('500.00'), D('100.00')),
    }
    assert _rows(by_salesperson, *COVERAGE) == {
        ('1',): (D('100.00'), D('200.00')),
        ('2',): (D('500.00'), D('0.00')),
    }


async def test_with_vat_invoiced_and_not_invoiced_add_up_to_sold(
    client: AsyncClient, book: dict[str, int]
) -> None:
    body = await _query(client, *COVERAGE, 'gross_sales', vat='included')

    totals = _totals(body)
    assert totals['invoiced_sales'] + totals['uninvoiced_sales'] == totals['gross_sales']
    assert totals['gross_sales'] == D('928.00')


async def test_standalone_invoices_are_apart_from_coverage(
    client: AsyncClient, book: dict[str, int]
) -> None:
    body = await _query(client, 'invoiced', 'standalone_invoiced', 'invoices', 'invoiced_sales')

    # Invoice 1's two lines and invoice 3's; the cancelled one and June's don't count.
    assert _totals(body) == {
        'invoiced': D('250.00'), 'standalone_invoiced': D('150.00'), 'invoices': D(2),
        'invoiced_sales': D('600.00'),
    }  # fmt: skip


async def test_an_invoice_issued_after_the_period_raises_its_coverage(
    client: AsyncClient, db: AsyncSession, book: dict[str, int]
) -> None:
    before = _totals(await _query(client, *COVERAGE))
    await invoice(db, datetime(2026, 7, 1, 12), [(book['c'], '4', '25')], serial=6)
    cache.responses.clear()
    after = _totals(await _query(client, *COVERAGE))

    assert before['uninvoiced_sales'] == D('200.00')
    assert after == {'invoiced_sales': D('700.00'), 'uninvoiced_sales': D('100.00')}


async def test_the_lag_is_days_to_the_first_invoice(
    client: AsyncClient, book: dict[str, int]
) -> None:
    """A: 6 days on 100.00; B: 29 days on 500.00."""
    body = await _query(client, 'invoicing_lag')

    expected = (D(6 * 100 + 29 * 500) / 600).quantize(D('0.1'))
    assert _totals(body) == {'invoicing_lag': expected}


# ── Status and VAT ────────────────────────────────────────────────────────────


async def test_status_and_output_vat_by_issuer(client: AsyncClient, book: dict[str, int]) -> None:
    by_status = await _query(client, 'invoiced', 'invoices', dimensions=['document_status'])
    vat = await _query(
        client, 'output_vat', 'payment_receipts', dimensions=['issuer', 'date'], grain='month'
    )

    assert _rows(by_status, 'invoiced', 'invoices') == {
        ('stamped',): (D('250.00'), D(2)),
        ('cancelled',): (D('100.00'), D(1)),
    }
    # The receipt's 300.00 is listed, not added: VAT is the two stamped invoices' 32.00 and 8.00.
    assert _rows(vat, 'output_vat', 'payment_receipts') == {
        ('EKU9003173C9', '2026-05-01'): (D('40.00'), D('300.00')),
    }


# ── Detail ────────────────────────────────────────────────────────────────────


async def test_the_invoices_detail_sums_to_the_query(
    client: AsyncClient, book: dict[str, int]
) -> None:
    body = await _query(client, 'invoiced', 'invoices', 'output_vat', 'payment_receipts')
    listed = await _detail(client, 'invoices')
    invoices_only = await _detail(
        client, 'invoices', filters=[{'dimension': 'document_type', 'values': ['invoice']}]
    )

    totals = _totals(body)
    assert D(listed['sums']['invoiced']) == totals['invoiced']
    assert D(listed['sums']['output_vat']) == totals['output_vat']
    assert D(listed['sums']['payment_receipts']) == totals['payment_receipts']
    assert invoices_only['total'] == totals['invoices']
    assert {i['id'] for i in listed['items']} == {book['first'], book['third'], book['receipt']}
    first = next(i for i in listed['items'] if i['id'] == book['first'])
    assert first['folio'] == 'RH-1'
    assert first['amount'] == '200.00'
    values = first['figures']['values']
    assert (values['type'], values['status'], values['issuer']) == (
        'invoice', 'stamped', 'EKU9003173C9',
    )  # fmt: skip
    assert (values['subtotal'], values['vat'], values['total']) == ('200.00', '32.00', '232.00')
    assert values['uuid'] and values['stamped'] and values['cancelled'] is None


async def test_the_cancelled_invoice_shows_when_it_was_cancelled(
    client: AsyncClient, book: dict[str, int]
) -> None:
    listed = await _detail(
        client, 'invoices', filters=[{'dimension': 'document_status', 'values': ['cancelled']}]
    )

    [item] = listed['items']
    assert item['figures']['values']['status'] == 'cancelled'
    assert item['figures']['values']['cancelled'].startswith('2026-09-29')


async def test_the_invoices_detail_by_issuer_and_status(
    client: AsyncClient, book: dict[str, int]
) -> None:
    """Legacy question 23: which CFDI an issuer stamped or cancelled in the period."""
    issuer = {'dimension': 'issuer', 'values': ['EKU9003173C9']}
    either = {'dimension': 'document_status', 'values': ['stamped', 'cancelled']}

    listed = await _detail(client, 'invoices', filters=[issuer, either])
    elsewhere = await _detail(
        client, 'invoices', filters=[{'dimension': 'issuer', 'values': ['XAXX010101000']}]
    )

    assert listed['total'] == 4
    assert {i['figures']['values']['status'] for i in listed['items']} == {'stamped', 'cancelled'}
    assert {i['figures']['values']['issuer'] for i in listed['items']} == {'EKU9003173C9'}
    assert elsewhere['total'] == 0


async def test_the_uninvoiced_lines_sum_to_the_query(
    client: AsyncClient, book: dict[str, int]
) -> None:
    body = await _query(client, 'uninvoiced_sales')
    listed = await _detail(client, 'uninvoiced_lines')

    assert D(listed['sums']['amount']) == _totals(body)['uninvoiced_sales']
    assert {i['id']: (i['quantity'], i['amount']) for i in listed['items']} == {
        book['a']: ('1.0000', '100.00'),
        book['c']: ('4.0000', '100.00'),
    }


async def test_the_fiscal_exports_have_the_documents_figures(
    client: AsyncClient, book: dict[str, int]
) -> None:
    invoices = await assert_export_matches(client, {'kind': 'invoices', **MAY})
    lines = await assert_export_matches(client, {'kind': 'uninvoiced_lines', **MAY})

    first = next(line for line in invoices if line['document_id'] == str(book['first']))
    assert (first['issuer'], first['subtotal'], first['vat']) == ('EKU9003173C9', '200.00', '32.00')
    assert {line['sold'] for line in lines[:-1]} == {'2.0000', '4.0000'}


# ── Who sees what ─────────────────────────────────────────────────────────────


async def test_fiscal_needs_23_or_48(
    client: AsyncClient, db: AsyncSession, book: dict[str, int]
) -> None:
    _as_clerk()
    await _grant(db, SystemObject.SALES_BY_CUSTOMER, SystemObject.SALES_ORDERS)
    query = await client.post(f'{URL}/query', json={'measures': ['invoiced'], **MAY})
    invoices = await client.post(f'{URL}/detail', json={'kind': 'invoices', **MAY})
    lines = await client.post(f'{URL}/detail', json={'kind': 'uninvoiced_lines', **MAY})

    body = assert_problem(query, 403, 'auth.forbidden')
    assert sorted(g['system_object'] for g in body['params']['required']) == [
        SystemObject.FISCAL_DOCUMENTS, SystemObject.FISCAL_DOCUMENTS_REPORT,
    ]  # fmt: skip
    assert_problem(invoices, 403, 'auth.forbidden')
    assert_problem(lines, 403, 'auth.forbidden')

    # The report opens the family; the invoices detail also needs read on fiscal documents.
    await _grant(db, SystemObject.FISCAL_DOCUMENTS_REPORT)
    query = await client.post(f'{URL}/query', json={'measures': ['invoiced'], **MAY})
    invoices = await client.post(f'{URL}/detail', json={'kind': 'invoices', **MAY})
    lines = await client.post(f'{URL}/detail', json={'kind': 'uninvoiced_lines', **MAY})

    assert query.status_code == 200, query.text
    assert_problem(invoices, 403, 'auth.forbidden')
    assert lines.status_code == 200, lines.text


async def test_fiscal_scope_is_the_issuing_facility(
    client: AsyncClient, db: AsyncSession, book: dict[str, int]
) -> None:
    """A clerk of facility 1 sees none of facility 2's invoices."""
    _as_clerk()
    await _grant(db, SystemObject.FISCAL_DOCUMENTS)

    body = await _query(client, 'invoiced', 'standalone_invoiced')
    listed = await _detail(client, 'invoices')
    elsewhere = await client.post(
        f'{URL}/query', json={'measures': ['invoiced'], **MAY, 'facility': 2}
    )

    assert _totals(body) == {'invoiced': D('200.00'), 'standalone_invoiced': D('100.00')}
    assert book['third'] not in {i['id'] for i in listed['items']}
    assert {i['facility'] for i in listed['items']} == {1}
    assert_problem(elsewhere, 403, 'facility.other_facility')


# ── Dashboard ─────────────────────────────────────────────────────────────────

FISCAL_TILE, FISCAL_SERIES = 'uninvoiced_sales', 'invoice_coverage'


async def _dashboard(client: AsyncClient) -> dict:
    response = await client.get(f'{URL}/dashboard', params=MAY)
    assert response.status_code == 200, response.text
    return response.json()


async def test_the_fiscal_tiles_equal_the_query(client: AsyncClient, book: dict[str, int]) -> None:
    body = await _dashboard(client)
    kpi = next(k for k in body['kpis'] if k['key'] == FISCAL_TILE)
    series = next(s for s in body['series'] if s['key'] == FISCAL_SERIES)

    answered = await _query(client, 'uninvoiced_sales')
    assert kpi['value'] == answered['totals']['values']['uninvoiced_sales'] == '200.00'
    drilled = await client.post(f'{URL}/query', json=kpi['drill'])
    assert drilled.json()['totals']['values']['uninvoiced_sales'] == kpi['value']
    coverage = await client.post(f'{URL}/query', json=series['query']['definition'])
    assert series['chart'] == 'bar'
    assert series['query']['definition']['dimensions'] == ['facility']
    assert series['query']['rows'] == coverage.json()['rows']
    assert series['query']['totals'] == coverage.json()['totals']
    assert series['query']['totals']['values']['invoice_coverage'] == '0.7500'


async def test_the_fiscal_tiles_are_for_fiscal_readers_only(
    client: AsyncClient, db: AsyncSession, book: dict[str, int]
) -> None:
    _as_clerk()
    await _grant(db, SystemObject.SALES_BY_CUSTOMER)
    body = await _dashboard(client)
    assert FISCAL_TILE not in {k['key'] for k in body['kpis']}
    assert FISCAL_SERIES not in {s['key'] for s in body['series']}

    await _grant(db, SystemObject.FISCAL_DOCUMENTS_REPORT)
    body = await _dashboard(client)
    kpi = next(k for k in body['kpis'] if k['key'] == FISCAL_TILE)
    assert FISCAL_SERIES in {s['key'] for s in body['series']}
    # The clerk's scope: orders A and C of facility 1.
    answered = await _query(client, 'uninvoiced_sales')
    assert kpi['value'] == answered['totals']['values']['uninvoiced_sales'] == '200.00'
