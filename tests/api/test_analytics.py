"""The /analytics routes (spec 037): authentication, privileges, scope and the catalog's refusals.

Unlike most of `tests/api/`, these run the real engine on the integration database: the point of
SC-005 is that a caller without the cost privilege never receives a cost or margin key, and only a
real response can show that. The integration fixtures are imported for that reason.
"""

from collections.abc import AsyncIterator, Iterator
from datetime import datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.enums import AccessRight, SystemObject
from app.main import app
from app.models.user import AccessPrivilege
from app.services.analytics import cache
from tests.integration.analytics_seed import L, sale
from tests.integration.conftest import db, engine, seeded, sessions  # noqa: F401  (fixtures)
from tests.problem import assert_problem

BASE = '/api/v1/analytics'
AT = datetime(2026, 5, 4, 10, 30)
COST = (37, 38, 39)
SALES = SystemObject.SALES_BY_CUSTOMER
PAYMENTS = SystemObject.RECEIVED_PAYMENTS
ADMIN = CurrentUser(
    user_id='tester', session_version=1, administrator=True, facility_id=1, employee_id=1
)
CLERK = CurrentUser(
    user_id='tester', session_version=1, administrator=False, facility_id=1, employee_id=1
)


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


@pytest.fixture(autouse=True)
def _fresh_cache() -> Iterator[None]:
    """The response cache is per process; each test starts without another test's answers."""
    cache.responses.clear()
    yield
    cache.responses.clear()


@pytest.fixture(autouse=True)
def _clerk() -> Iterator[None]:
    """Every test runs as a non-administrator unless it says otherwise."""
    app.dependency_overrides[get_current_user] = lambda: CLERK
    yield


def _as(user: CurrentUser) -> None:
    app.dependency_overrides[get_current_user] = lambda: user


async def grant(db: AsyncSession, *objects: int) -> None:  # noqa: F811
    for system_object in objects:
        db.add(
            AccessPrivilege(
                user_id='tester', system_object=int(system_object), privileges=AccessRight.READ
            )
        )
    await db.commit()


def query(*measures: str, **definition: Any) -> dict:
    return {'measures': list(measures), 'start': '2026-05-04', 'end': '2026-05-04', **definition}


def keys_in(body: Any) -> set[str]:
    """Every dict key anywhere in a JSON body, and every measure or dimension key it names."""
    found: set[str] = set()
    if isinstance(body, dict):
        for key, value in body.items():
            found.add(key)
            if key in ('key', 'dimension') and isinstance(value, str):
                found.add(value)
            found |= keys_in(value)
    elif isinstance(body, list):
        for item in body:
            found |= keys_in(item)
    return found


def sensitive_keys(body: Any) -> set[str]:
    """Keys naming cost or margin, in a JSON body or a list of file headers (SC-005)."""
    names = set(body) if isinstance(body, list) and all(isinstance(b, str) for b in body) else None
    found = names if names is not None else keys_in(body)
    words = ('cost', 'margin', 'costed', 'coverage', 'costo', 'margen')
    return {k for k in found if any(w in k.lower() for w in words)}


# ── US1 (T015) ────────────────────────────────────────────────────────────────


async def test_both_routes_need_a_token(http: AsyncClient) -> None:
    app.dependency_overrides.pop(get_current_user)

    catalog = await http.get(f'{BASE}/catalog')
    answered = await http.post(f'{BASE}/query', json=query('net_sales'))

    assert_problem(catalog, 401, 'auth.not_authenticated')
    assert_problem(answered, 401, 'auth.not_authenticated')


async def test_without_a_family_the_routes_are_forbidden(http: AsyncClient) -> None:
    catalog = await http.get(f'{BASE}/catalog')
    answered = await http.post(f'{BASE}/query', json=query('net_sales'))

    assert_problem(catalog, 403, 'auth.forbidden')
    assert_problem(answered, 403, 'auth.forbidden')


async def test_the_catalog_hides_sensitive_measures_without_the_cost_privilege(
    http: AsyncClient,
    db: AsyncSession,  # noqa: F811
) -> None:
    await grant(db, SALES)

    body = (await http.get(f'{BASE}/catalog')).json()

    keys = {m['key'] for m in body['measures']}
    assert 'net_sales' in keys
    assert not keys & {'costed_net_sales', 'cost_coverage', 'cost_of_sales', 'gross_margin'}
    assert 'margin_pct' not in keys
    assert 'collected' not in keys  # the payments family is not held either
    assert body['as_of']


async def test_the_catalog_lists_them_with_the_cost_privilege(
    http: AsyncClient,
    db: AsyncSession,  # noqa: F811
) -> None:
    await grant(db, SALES, SystemObject.GROSS_PROFITS_BY_PRODUCT)

    keys = {m['key'] for m in (await http.get(f'{BASE}/catalog')).json()['measures']}

    assert {'gross_margin', 'margin_pct', 'cost_coverage'} <= keys


async def test_a_margin_query_without_the_cost_privilege_is_refused(
    http: AsyncClient,
    db: AsyncSession,  # noqa: F811
) -> None:
    await grant(db, SALES)

    response = await http.post(f'{BASE}/query', json=query('net_sales', 'gross_margin'))

    body = assert_problem(response, 403, 'auth.forbidden')
    assert sorted(g['system_object'] for g in body['params']['required']) == list(COST)


async def test_a_payments_only_caller_queries_collected_but_not_sales(
    http: AsyncClient,
    db: AsyncSession,  # noqa: F811
) -> None:
    await grant(db, PAYMENTS)

    collected = await http.post(f'{BASE}/query', json=query('collected'))
    sales = await http.post(f'{BASE}/query', json=query('net_sales'))

    assert collected.status_code == 200, collected.text
    assert_problem(sales, 403, 'auth.forbidden')


async def test_every_response_states_as_of(
    http: AsyncClient,
    db: AsyncSession,  # noqa: F811
) -> None:
    _as(ADMIN)
    await sale(db, AT, [L('1', '116')])

    catalog = (await http.get(f'{BASE}/catalog')).json()
    answered = (await http.post(f'{BASE}/query', json=query('net_sales'))).json()

    assert catalog['as_of'] and answered['as_of']


# ── US2 (T023): the catalog's refusals ────────────────────────────────────────


async def test_more_than_three_dimensions(http: AsyncClient) -> None:
    _as(ADMIN)
    body = query('net_sales', dimensions=['product', 'customer', 'salesperson', 'facility'])

    problem = assert_problem(
        await http.post(f'{BASE}/query', json=body), 422, 'analytics.too_many_dimensions'
    )
    assert problem['params'] == {'max': 3, 'given': 4}


async def test_a_measure_its_dimension_cannot_split(http: AsyncClient) -> None:
    _as(ADMIN)
    grouped = query('collected', dimensions=['product'])
    filtered = query('collected', filters=[{'dimension': 'label', 'values': ['1']}])

    problem = assert_problem(
        await http.post(f'{BASE}/query', json=grouped), 422, 'analytics.dimension_not_applicable'
    )
    assert problem['params'] == {'measure': 'collected', 'dimension': 'product'}
    assert_problem(
        await http.post(f'{BASE}/query', json=filtered), 422, 'analytics.dimension_not_applicable'
    )


@pytest.mark.parametrize(
    ('start', 'end'), [('2026-05-04', '2026-05-03'), ('2020-01-01', '2026-01-01')]
)
async def test_an_invalid_period(http: AsyncClient, start: str, end: str) -> None:
    _as(ADMIN)
    body = {**query('net_sales'), 'start': start, 'end': end}

    problem = assert_problem(
        await http.post(f'{BASE}/query', json=body), 422, 'analytics.period_invalid'
    )
    assert problem['params']['max_days'] == 1826


async def test_a_sort_on_an_unrequested_key(http: AsyncClient) -> None:
    _as(ADMIN)
    body = query('net_sales', dimensions=['product'], sort='-orders')

    problem = assert_problem(
        await http.post(f'{BASE}/query', json=body), 422, 'analytics.sort_invalid'
    )
    assert problem['params'] == {'sort': '-orders'}


async def test_an_unknown_measure_fails_request_validation(http: AsyncClient) -> None:
    _as(ADMIN)

    response = await http.post(f'{BASE}/query', json=query('profit'))

    assert response.status_code == 422
    assert response.json()['detail'][0]['type'] == 'enum'


async def test_a_malformed_filter_value_fails_request_validation(http: AsyncClient) -> None:
    _as(ADMIN)
    body = query('net_sales', filters=[{'dimension': 'product', 'values': ['P-1']}])

    assert (await http.post(f'{BASE}/query', json=body)).status_code == 422


async def test_another_facility_is_refused_without_101(
    http: AsyncClient,
    db: AsyncSession,  # noqa: F811
) -> None:
    await grant(db, SALES)
    as_parameter = query('net_sales', facility=2)
    as_filter = query('net_sales', filters=[{'dimension': 'facility', 'values': ['2']}])

    assert_problem(
        await http.post(f'{BASE}/query', json=as_parameter), 403, 'facility.other_facility'
    )
    assert_problem(await http.post(f'{BASE}/query', json=as_filter), 403, 'facility.other_facility')


async def test_omitting_the_facility_gives_the_callers_own(
    http: AsyncClient,
    db: AsyncSession,  # noqa: F811
) -> None:
    await grant(db, SALES)

    body = (await http.post(f'{BASE}/query', json=query('net_sales'))).json()

    assert body['definition']['facility'] == 1


async def test_holders_of_101_see_every_facility(
    http: AsyncClient,
    db: AsyncSession,  # noqa: F811
) -> None:
    await grant(db, SALES, SystemObject.SEARCH_CREDITS_FROM_ALL_STORES)

    everywhere = (await http.post(f'{BASE}/query', json=query('net_sales'))).json()
    other = await http.post(f'{BASE}/query', json=query('net_sales', facility=2))

    assert everywhere['definition']['facility'] is None
    assert other.status_code == 200, other.text


async def test_margin_shows_only_with_the_cost_privilege(
    http: AsyncClient,
    db: AsyncSession,  # noqa: F811
) -> None:
    await sale(db, AT, [L('1', '116', cost='58')])
    await grant(db, SALES)
    body = query('net_sales', 'orders', dimensions=['product'], comparison='previous_period')

    without = (await http.post(f'{BASE}/query', json=body)).json()
    await grant(db, SystemObject.GROSS_PROFITS_BY_CUSTOMER)
    with_cost = (await http.post(f'{BASE}/query', json=query('margin_pct'))).json()

    assert sensitive_keys(without) == set()
    assert with_cost['totals']['values'] == {'margin_pct': '0.5000'}


async def test_a_repeated_query_answers_304_to_its_etag(http: AsyncClient) -> None:
    _as(ADMIN)

    first = await http.post(f'{BASE}/query', json=query('net_sales'))
    again = await http.post(
        f'{BASE}/query', json=query('net_sales'), headers={'If-None-Match': first.headers['etag']}
    )

    assert first.status_code == 200
    assert first.headers['cache-control'] == 'private, max-age=60'
    assert again.status_code == 304
    assert again.headers['etag'] == first.headers['etag']


# ── US6 (T054) ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    'privilege', [SystemObject.RECEIVED_PAYMENTS, SystemObject.RECEIVED_PAYMENTS_SUMMARY]
)
async def test_either_payments_report_opens_payment_measures(
    http: AsyncClient,
    db: AsyncSession,  # noqa: F811
    privilege: SystemObject,
) -> None:
    await grant(db, privilege)
    body = query('collected', 'refunds_paid_out', dimensions=['payment_method', 'cash_session'])

    response = await http.post(f'{BASE}/query', json=body)

    assert response.status_code == 200, response.text


async def test_collected_by_label_is_refused(
    http: AsyncClient,
    db: AsyncSession,  # noqa: F811
) -> None:
    await grant(db, PAYMENTS)

    response = await http.post(f'{BASE}/query', json=query('collected', dimensions=['label']))

    assert_problem(response, 422, 'analytics.dimension_not_applicable')


# ── US3 (T031): the dashboard trims what a caller can't see ───────────────────

MAY = {'start': '2026-05-01', 'end': '2026-05-31'}


async def _dashboard(http: AsyncClient) -> dict:
    response = await http.get(f'{BASE}/dashboard', params=MAY)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
async def priced(db: AsyncSession) -> None:  # noqa: F811
    """A line below cost and one without, so the cost alerts have something to count."""
    await sale(db, datetime(2026, 5, 4, 10, 0), [L('1', '116', cost='200'), L('1', '58')])


async def test_the_dashboard_needs_a_token(http: AsyncClient) -> None:
    app.dependency_overrides.pop(get_current_user)

    assert_problem(await http.get(f'{BASE}/dashboard', params=MAY), 401, 'auth.not_authenticated')


async def test_a_sales_only_caller_gets_no_payment_tiles(
    http: AsyncClient,
    db: AsyncSession,  # noqa: F811
) -> None:
    await grant(db, SALES)

    body = await _dashboard(http)

    assert not {'collected', 'collected_today'} & {k['key'] for k in body['kpis']}
    assert 'payment_mix' not in {s['key'] for s in body['series']}


async def test_a_payments_only_caller_gets_no_sales_tiles(
    http: AsyncClient,
    db: AsyncSession,  # noqa: F811
) -> None:
    await grant(db, PAYMENTS)

    body = await _dashboard(http)

    assert [k['key'] for k in body['kpis']] == ['collected', 'collected_today']
    assert [s['key'] for s in body['series']] == ['payment_mix']
    assert body['alerts'] == []


async def test_without_the_cost_privilege_margin_tiles_are_absent(
    http: AsyncClient,
    db: AsyncSession,  # noqa: F811
    priced: None,
) -> None:
    await grant(db, SALES, PAYMENTS)

    body = await _dashboard(http)

    assert not {'margin_pct', 'cost_coverage'} & {k['key'] for k in body['kpis']}
    assert not {'lines_below_cost', 'lines_without_cost'} & {a['key'] for a in body['alerts']}
    assert sensitive_keys(body) == set()


async def test_with_the_cost_privilege_they_are_present(
    http: AsyncClient,
    db: AsyncSession,  # noqa: F811
    priced: None,
) -> None:
    await grant(db, SALES, SystemObject.GROSS_PROFITS_BY_SALES_PERSON)

    body = await _dashboard(http)

    assert {'margin_pct', 'cost_coverage'} <= {k['key'] for k in body['kpis']}
    assert {'lines_below_cost', 'lines_without_cost'} <= {a['key'] for a in body['alerts']}


async def test_the_dashboard_definition_is_the_export_source_shape(http: AsyncClient) -> None:
    """An export of the dashboard takes the same request the dashboard answered (FR-050)."""
    from app.schemas.analytics import AnalyticsDashboardRequest

    _as(ADMIN)
    body = await _dashboard(http)

    assert (
        AnalyticsDashboardRequest.model_validate(body['definition']).start.isoformat()
        == (MAY['start'])
    )


# ── US4 (T037): the detail's privileges and refusals ──────────────────────────


def detail_body(kind: str, **body: Any) -> dict:
    return {'kind': kind, **MAY, **body}


async def test_the_detail_needs_a_token(http: AsyncClient) -> None:
    app.dependency_overrides.pop(get_current_user)

    response = await http.post(f'{BASE}/detail', json=detail_body('orders'))

    assert_problem(response, 401, 'auth.not_authenticated')


@pytest.mark.parametrize(
    ('kind', 'family', 'document'),
    [
        ('orders', SALES, SystemObject.SALES_ORDERS),
        ('lines', SALES, SystemObject.SALES_ORDERS),
        ('refunds', SALES, SystemObject.CUSTOMER_REFUNDS),
        ('credit_notes', SALES, SystemObject.FISCAL_DOCUMENTS),
        ('payments', PAYMENTS, SystemObject.CUSTOMER_PAYMENTS),
    ],
)
async def test_the_detail_needs_the_family_and_read_on_the_kind(
    http: AsyncClient,
    db: AsyncSession,  # noqa: F811
    kind: str,
    family: SystemObject,
    document: SystemObject,
) -> None:
    await grant(db, family)
    refused = await http.post(f'{BASE}/detail', json=detail_body(kind))
    await grant(db, document)
    allowed = await http.post(f'{BASE}/detail', json=detail_body(kind))

    body = assert_problem(refused, 403, 'auth.forbidden')
    assert body['params']['required'] == [
        {'system_object': int(document), 'right': int(AccessRight.READ)}
    ]
    assert allowed.status_code == 200, allowed.text


async def test_cost_keys_are_absent_without_the_privilege_and_null_with_it(
    http: AsyncClient,
    db: AsyncSession,  # noqa: F811
    priced: None,
) -> None:
    await grant(db, SALES, SystemObject.SALES_ORDERS)
    without = (await http.post(f'{BASE}/detail', json=detail_body('lines'))).json()
    await grant(db, SystemObject.GROSS_PROFITS_BY_PRODUCT)
    with_cost = (await http.post(f'{BASE}/detail', json=detail_body('lines'))).json()

    assert sensitive_keys(without) == set()
    assert all('cost' in item and 'margin' in item for item in with_cost['items'])
    assert None in {item['cost'] for item in with_cost['items']}  # the uncosted line
    assert {'cost', 'margin'} <= set(with_cost['sums'])


@pytest.mark.parametrize('flag', ['below_cost', 'without_cost'])
async def test_cost_flags_need_the_cost_privilege(
    http: AsyncClient,
    db: AsyncSession,  # noqa: F811
    flag: str,
) -> None:
    await grant(db, SALES, SystemObject.SALES_ORDERS)

    response = await http.post(f'{BASE}/detail', json=detail_body('lines', flag=flag))

    body = assert_problem(response, 403, 'auth.forbidden')
    assert sorted(g['system_object'] for g in body['params']['required']) == list(COST)


@pytest.mark.parametrize(
    ('kind', 'flag'), [('lines', 'overdue'), ('payments', 'below_cost'), ('orders', 'collected')]
)
async def test_a_flag_on_another_kind_fails_validation(
    http: AsyncClient, kind: str, flag: str
) -> None:
    _as(ADMIN)

    response = await http.post(f'{BASE}/detail', json=detail_body(kind, flag=flag))

    assert response.status_code == 422
    assert response.json()['detail'][0]['type'] == 'analytics_flag_kind'


async def test_the_detail_refuses_another_facility_without_101(
    http: AsyncClient,
    db: AsyncSession,  # noqa: F811
) -> None:
    await grant(db, SALES, SystemObject.SALES_ORDERS)

    response = await http.post(f'{BASE}/detail', json=detail_body('orders', facility=2))

    assert_problem(response, 403, 'facility.other_facility')


async def test_a_page_holds_at_most_one_hundred(http: AsyncClient) -> None:
    _as(ADMIN)

    response = await http.post(f'{BASE}/detail', json=detail_body('orders', limit=101))

    assert response.status_code == 422


# ── Polish (T056): the SC-005 sweep over every route ──────────────────────────

VISIBLE = [
    'gross_sales', 'discounts', 'refunds', 'credit_notes', 'net_sales', 'units_sold',
    'units_refunded', 'units', 'orders', 'average_ticket',
]  # fmt: skip
PAYMENT_MEASURES = ['collected', 'refunds_paid_out', 'store_credit_issued', 'payments']


async def test_no_route_gives_cost_or_margin_without_the_cost_privilege(
    http: AsyncClient,
    db: AsyncSession,  # noqa: F811
    priced: None,
) -> None:
    import io

    import openpyxl

    await grant(
        db, SALES, PAYMENTS, SystemObject.DOWNLOAD_CSV_FILES, SystemObject.SALES_ORDERS,
        SystemObject.CUSTOMER_PAYMENTS, SystemObject.CUSTOMER_REFUNDS,
        SystemObject.FISCAL_DOCUMENTS,
    )  # fmt: skip
    found: set[str] = set()

    found |= sensitive_keys((await http.get(f'{BASE}/catalog')).json())
    for measures in (VISIBLE, PAYMENT_MEASURES):
        body = {
            **MAY,
            'measures': measures,
            'dimensions': ['date'],
            'comparison': 'previous_period',
        }
        found |= sensitive_keys((await http.post(f'{BASE}/query', json=body)).json())
    found |= sensitive_keys(await _dashboard(http))
    for kind in ('orders', 'lines', 'refunds', 'credit_notes', 'payments'):
        found |= sensitive_keys((await http.post(f'{BASE}/detail', json=detail_body(kind))).json())

    query = {**MAY, 'measures': VISIBLE, 'dimensions': ['product']}
    for source in ({'query': query}, {'detail': detail_body('lines')}):
        as_csv = await http.post(f'{BASE}/exports', json={'format': 'csv', **source})
        as_xlsx = await http.post(f'{BASE}/exports', json={'format': 'xlsx', **source})
        assert as_csv.status_code == as_xlsx.status_code == 200, (as_csv.text, as_xlsx.text)
        found |= sensitive_keys(as_csv.content.decode('utf-8-sig').splitlines()[0].split(','))
        sheet = openpyxl.load_workbook(io.BytesIO(as_xlsx.content))['Datos']
        found |= sensitive_keys([str(c.value).lower() for c in sheet[1]])
    pdf = await http.post(f'{BASE}/exports', json={'format': 'pdf', 'dashboard': MAY})
    assert pdf.status_code == 200, pdf.text

    assert found == set()
