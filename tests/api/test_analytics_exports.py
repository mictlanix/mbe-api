"""The /analytics/exports routes (spec 037, T042 and T043): files that match the screen.

Like `test_analytics.py`, these run the real engine on the integration database, so the files are
compared with real JSON answers (SC-004).
"""

import csv
import dataclasses
import io
from collections.abc import AsyncIterator, Iterator
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import openpyxl
import pypdf
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import local_now
from app.core.config import settings
from app.core.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.enums import AccessRight, PaymentType, SystemObject
from app.main import app
from app.models.analytics import AnalyticsExport
from app.models.user import AccessPrivilege
from app.rendering import formatting
from app.services.analytics import cache, exports
from tests.integration.analytics_seed import (
    L,
    credit_note,
    payment,
    refund,
    sale,
    seed_catalog,
)
from tests.integration.conftest import db, engine, seeded, sessions  # noqa: F401  (fixtures)
from tests.problem import assert_problem

BASE = '/api/v1/analytics'
MAY = {'start': '2026-05-01', 'end': '2026-05-31'}
ADMIN = CurrentUser(
    user_id='tester', session_version=1, administrator=True, facility_id=1, employee_id=1
)


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path) -> Iterator[None]:
    original = settings.exports_dir
    settings.exports_dir = str(tmp_path / 'exports')
    cache.responses.clear()
    app.dependency_overrides[get_current_user] = lambda: ADMIN
    yield
    settings.exports_dir = original
    cache.responses.clear()


@pytest.fixture
async def http(sessions: async_sessionmaker, seeded: None) -> AsyncIterator[AsyncClient]:  # noqa: F811
    async def _db() -> AsyncIterator[AsyncSession]:
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = _db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            yield client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
async def month(db: AsyncSession) -> None:  # noqa: F811
    await seed_catalog(db)
    first, lines = await sale(
        db, datetime(2026, 5, 4, 9, 15), [L('2', '116', cost='58'), L('1', '58', product=2)]
    )
    await sale(db, datetime(2026, 5, 11, 13, 0), [L('4', '116', product=3)], customer=2)
    await sale(db, datetime(2026, 5, 17, 18, 45), [L('1', '232', product=2, cost='250')])
    await sale(db, datetime(2026, 4, 9, 10, 0), [L('1', '348')])
    await refund(db, first, [(lines[0], '1', L('2', '116'))], datetime(2026, 5, 20, 12, 0))
    await credit_note(db, datetime(2026, 5, 21, 12, 0), [L('1', '11.60')], serial=7501)
    await payment(db, datetime(2026, 5, 4, 9, 20), '290')
    await payment(db, datetime(2026, 5, 20, 12, 5), '-116')
    await payment(db, datetime(2026, 5, 22, 9, 0), '40', kind=PaymentType.CREDIT_NOTE, method=0)


def q(*measures: str, **definition: Any) -> dict:
    return {'measures': list(measures), **MAY, **definition}


#: Twenty fixed definitions (SC-004): every visible measure, each dimension at least once, a
#: comparison, top N with others, a label grouping and both VAT treatments.
DEFINITIONS = [
    q('net_sales', 'gross_sales', 'refunds', 'credit_notes', dimensions=['date']),
    q('net_sales', dimensions=['date'], grain='week', vat='included'),
    q('units', 'units_sold', 'units_refunded', dimensions=['product']),
    q('net_sales', dimensions=['product'], top=1),
    q('orders', 'average_ticket', dimensions=['customer']),
    q('net_sales', 'discounts', dimensions=['customer_type'], vat='included'),
    q('gross_margin', 'margin_pct', 'cost_of_sales', dimensions=['product']),
    q('costed_net_sales', 'cost_coverage', dimensions=['salesperson']),
    q('net_sales', dimensions=['brand']),
    q('units', dimensions=['model']),
    q('units', dimensions=['label']),
    q('net_sales', dimensions=['facility'], comparison='previous_period'),
    q('net_sales', dimensions=['point_of_sale', 'payment_terms']),
    q('orders', dimensions=['weekday', 'hour']),
    q('collected', 'payments', dimensions=['payment_method']),
    q('refunds_paid_out', 'store_credit_issued', dimensions=['cash_session']),
    q('collected', dimensions=['date'], grain='month', comparison='same_period_last_year'),
    q('net_sales', 'orders', dimensions=['salesperson', 'product'], vat='included'),
    q('gross_sales', dimensions=['date'], grain='quarter'),
    q('net_sales', 'gross_margin', 'units'),
]


async def _export(http: AsyncClient, fmt: str, **source: Any) -> Any:
    return await http.post(f'{BASE}/exports', json={'format': fmt, **source})


def _measure_values(answer: dict) -> list[list[str | None]]:
    """The query's rows, others and totals as measure values, in file order."""
    measures = answer['definition']['measures']
    rows = [*answer['rows'], *([answer['others']] if answer['others'] else []), answer['totals']]
    return [[row['values'][m] for m in measures] for row in rows]


@pytest.mark.parametrize('definition', DEFINITIONS, ids=[str(i) for i in range(20)])
async def test_every_value_matches_the_json(
    http: AsyncClient, month: None, definition: dict
) -> None:
    answer = (await http.post(f'{BASE}/query', json=definition)).json()
    expected = _measure_values(answer)
    width = len(definition['measures'])

    xlsx = await _export(http, 'xlsx', query=definition)
    csv_file = await _export(http, 'csv', query=definition)
    pdf = await _export(http, 'pdf', query=definition)

    assert xlsx.status_code == csv_file.status_code == pdf.status_code == 200
    assert xlsx.headers['content-disposition'].startswith('attachment;')

    book = openpyxl.load_workbook(io.BytesIO(xlsx.content))
    sheet = book['Datos']
    assert sheet.freeze_panes == 'A2' and sheet.auto_filter.ref
    cells = [[c.value for c in row][-width:] for row in sheet.iter_rows(min_row=2)]
    assert [
        [None if v is None else Decimal(str(v)).quantize(Decimal(e or '0')) for v, e in zip(r, x)]
        for r, x in zip(cells, expected, strict=True)
    ] == [[None if e is None else Decimal(e) for e in x] for x in expected]
    cover = {row[0].value: row[1].value for row in book['Resumen'].iter_rows(min_row=3)}
    assert {'Periodo', 'Comparación', 'Filtros', 'IVA', 'Generado', 'Generado por'} <= set(cover)

    assert csv_file.content.startswith('﻿'.encode())
    rows = list(csv.reader(io.StringIO(csv_file.content.decode('utf-8-sig'))))
    assert rows[0][-width:] == definition['measures']
    assert [r[-width:] for r in rows[1:]] == [[e or '' for e in x] for x in expected]
    assert rows[-1][0] == 'TOTAL'

    assert pdf.content.startswith(b'%PDF')
    text = ''.join(page.extract_text() for page in pypdf.PdfReader(io.BytesIO(pdf.content)).pages)
    for row in expected:
        for value, measure in zip(row, definition['measures'], strict=True):
            if value is not None and measure in ('net_sales', 'gross_sales', 'collected'):
                assert formatting.money(Decimal(value)) in text


async def test_an_empty_result_exports_its_cover_header_and_zero_totals(http: AsyncClient) -> None:
    response = await _export(http, 'csv', query=q('net_sales', dimensions=['product']))

    rows = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'))))
    assert rows == [['product', 'net_sales'], ['TOTAL', '0.00']]


async def test_without_the_cost_privilege_no_export_has_cost_or_margin(
    http: AsyncClient,
    db: AsyncSession,  # noqa: F811
    month: None,
) -> None:
    clerk = CurrentUser(
        user_id='tester', session_version=1, administrator=False, facility_id=1, employee_id=1
    )
    app.dependency_overrides[get_current_user] = lambda: clerk
    for obj in (SystemObject.SALES_BY_CUSTOMER, SystemObject.SALES_ORDERS):
        db.add(
            AccessPrivilege(user_id='tester', system_object=int(obj), privileges=AccessRight.READ)
        )
    await db.commit()
    detail = {'kind': 'lines', 'start': '2026-05-04', 'end': '2026-05-04'}

    lines = await _export(http, 'csv', detail=detail)
    refused = await _export(http, 'xlsx', query=q('gross_margin'))

    header = lines.content.decode('utf-8-sig').splitlines()[0]
    assert lines.status_code == 200, lines.text
    assert 'cost' not in header and 'margin' not in header
    assert_problem(refused, 403, 'auth.forbidden')


# ── T043: refusals and background jobs ────────────────────────────────────────


@pytest.mark.parametrize(
    ('fmt', 'source'),
    [
        ('csv', {}),
        ('csv', {'query': q('net_sales'), 'detail': {'kind': 'lines', **MAY}}),
        ('xlsx', {'dashboard': MAY}),
    ],
)
async def test_one_source_and_a_dashboard_only_as_pdf(
    http: AsyncClient, fmt: str, source: dict
) -> None:
    assert_problem(await _export(http, fmt, **source), 422, 'analytics.export_source_invalid')


async def test_a_dashboard_exports_to_pdf(http: AsyncClient, month: None) -> None:
    response = await _export(http, 'pdf', dashboard=MAY)

    assert response.status_code == 200, response.text
    assert response.content.startswith(b'%PDF')


async def test_a_pdf_over_the_cap_is_refused(
    http: AsyncClient, month: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(exports, 'PDF_ROWS', 1)

    response = await _export(http, 'pdf', query=q('net_sales', dimensions=['product']))

    problem = assert_problem(response, 422, 'analytics.pdf_too_large')
    assert problem['params'] == {'rows': 4, 'max_rows': 1}  # products 1–3 and the credit note


async def test_a_large_export_runs_in_the_background(
    http: AsyncClient, month: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(exports, 'INLINE_ROWS', 1)

    started = await _export(http, 'csv', detail={'kind': 'lines', **MAY})
    await exports.drain()
    job = started.json()
    status = await http.get(f'{BASE}/exports/{job["analytics_export_id"]}')
    downloaded = await http.get(f'{BASE}/exports/{job["analytics_export_id"]}/file')

    assert started.status_code == 202, started.text
    assert job['status'] == 'pending' and job['download_url'] is None
    assert status.json()['status'] == 'ready'
    assert status.json()['row_count'] == 4
    assert status.json()['download_url'].endswith(f'/{job["analytics_export_id"]}/file')
    assert downloaded.status_code == 200
    assert downloaded.content.startswith('﻿'.encode())
    assert len(downloaded.content.decode('utf-8-sig').splitlines()) == 6  # header, 4, total


async def _row(db: AsyncSession, **values: Any) -> int:  # noqa: F811
    now = local_now()
    row = AnalyticsExport(
        user='tester', source=2, format=2, definition='{}', creation_time=now, **values
    )
    db.add(row)
    await db.commit()
    return row.analytics_export_id


async def test_another_users_export_is_not_found(
    http: AsyncClient,
    db: AsyncSession,  # noqa: F811
) -> None:
    mine = await _row(db, status=0)
    other = dataclasses.replace(ADMIN, user_id='other')
    app.dependency_overrides[get_current_user] = lambda: other

    for path in (f'{BASE}/exports/{mine}', f'{BASE}/exports/{mine}/file', f'{BASE}/exports/999'):
        problem = assert_problem(await http.get(path), 404, 'resource.not_found')
        assert problem['params']['resource'] == 'analytics_export'


async def test_a_running_export_is_not_ready(
    http: AsyncClient,
    db: AsyncSession,  # noqa: F811
) -> None:
    running = await _row(db, status=1)

    problem = assert_problem(
        await http.get(f'{BASE}/exports/{running}/file'), 409, 'analytics.export_not_ready'
    )
    assert problem['params'] == {'status': 'running'}


async def test_after_expiry_the_export_is_gone(
    http: AsyncClient,
    db: AsyncSession,  # noqa: F811
) -> None:
    gone = await _row(
        db, status=2, file_name='old.csv', completion_time=local_now() - timedelta(hours=25),
        expiry_time=local_now() - timedelta(hours=1),
    )  # fmt: skip
    path = Path(settings.exports_dir) / 'old.csv'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('x')

    response = await http.get(f'{BASE}/exports/{gone}/file')

    assert_problem(response, 410, 'analytics.export_expired')
    assert not path.exists()


async def test_a_running_export_older_than_ten_minutes_reads_as_failed(
    http: AsyncClient,
    db: AsyncSession,  # noqa: F811
) -> None:
    stale = AnalyticsExport(
        user='tester', source=0, format=2, definition='{}', status=1,
        creation_time=local_now() - timedelta(minutes=11),
    )  # fmt: skip
    db.add(stale)
    await db.commit()

    body = (await http.get(f'{BASE}/exports/{stale.analytics_export_id}')).json()

    assert body['status'] == 'failed'


async def test_bulk_detail_exports_need_113(
    http: AsyncClient,
    db: AsyncSession,  # noqa: F811
) -> None:
    clerk = CurrentUser(
        user_id='tester', session_version=1, administrator=False, facility_id=1, employee_id=1
    )
    app.dependency_overrides[get_current_user] = lambda: clerk
    for obj in (SystemObject.SALES_BY_CUSTOMER, SystemObject.SALES_ORDERS):
        db.add(
            AccessPrivilege(user_id='tester', system_object=int(obj), privileges=AccessRight.READ)
        )
    await db.commit()
    bulk = {'kind': 'lines', **MAY}
    one_day = {'kind': 'lines', 'start': '2026-05-04', 'end': '2026-05-04'}

    refused = await _export(http, 'csv', detail=bulk)
    narrow = await _export(http, 'csv', detail=one_day)
    db.add(AccessPrivilege(user_id='tester', system_object=113, privileges=AccessRight.READ))
    await db.commit()
    allowed = await _export(http, 'csv', detail=bulk)
    app.dependency_overrides[get_current_user] = lambda: ADMIN
    administrator = await _export(http, 'csv', detail=bulk)

    problem = assert_problem(refused, 403, 'auth.forbidden')
    assert problem['params']['required'] == [{'system_object': 113, 'right': int(AccessRight.READ)}]
    assert narrow.status_code == allowed.status_code == administrator.status_code == 200
