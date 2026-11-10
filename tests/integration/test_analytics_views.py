"""Spec 038, T048: saved views against real tables (SQLite), FR-150–FR-153, research R14.

The owner is the administrator `tester`; `ana` is a facility-1 clerk with the sales reports only,
and `beto` an inactive user. A view runs as whoever opens it, with the period resolved against
the day it runs, and answers exactly what the route of its kind answers for those dates.
"""

import json
from collections.abc import Iterator
from datetime import date, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import local_now
from app.core.deps import CurrentUser, get_current_user
from app.enums import AccessRight, EntityStatus, SystemObject
from app.main import app as fastapi_app
from app.models.analytics import AnalyticsView, AnalyticsViewShare
from app.models.user import AccessPrivilege, User
from app.services import user_service
from app.services.analytics import cache, views
from tests.integration.analytics_seed import CREDIT_CUSTOMER, L, sale, seed_catalog
from tests.problem import assert_problem

pytestmark = pytest.mark.usefixtures('seeded')

URL = '/api/v1/analytics'
VIEWS = f'{URL}/views'
OWNER = CurrentUser(
    user_id='tester', session_version=1, administrator=True, facility_id=1, employee_id=1
)
ANA = CurrentUser(
    user_id='ana', session_version=1, administrator=False, facility_id=1, employee_id=1
)
LAST_MONTH = {'key': 'last_month'}


@pytest.fixture(autouse=True)
def _fresh_cache() -> Iterator[None]:
    cache.responses.clear()
    yield
    cache.responses.clear()


@pytest.fixture
async def users(db: AsyncSession) -> None:
    """`ana`, active, with the sales reports and sales orders; `beto`, inactive."""
    for user_id, status in (('ana', EntityStatus.ACTIVE), ('beto', EntityStatus.INACTIVE)):
        db.add(
            User(
                user_id=user_id, password='x', email=f'{user_id}@example.com', employee_id=1,
                administrator=False, status=status, session_version=1,
            )
        )  # fmt: skip
    await db.flush()
    for system_object in (SystemObject.SALES_BY_CUSTOMER, SystemObject.SALES_ORDERS):
        db.add(
            AccessPrivilege(
                user_id='ana', system_object=int(system_object), privileges=AccessRight.READ
            )
        )
    await db.commit()


@pytest.fixture
async def book(db: AsyncSession) -> None:
    """Sales in April and May 2026, in facility 1 and facility 2."""
    await seed_catalog(db)
    await sale(db, datetime(2026, 4, 10, 10), [L('2', '116', cost='50')])
    await sale(db, datetime(2026, 5, 12, 10), [L('1', '232', cost='80')], customer=CREDIT_CUSTOMER)
    await sale(db, datetime(2026, 5, 20, 10), [L('3', '58', cost='10', product=2)])


def _as(user: CurrentUser) -> None:
    fastapi_app.dependency_overrides[get_current_user] = lambda: user


def _on(monkeypatch: pytest.MonkeyPatch, today: date) -> None:
    """Freeze the day views resolve their periods against."""
    monkeypatch.setattr(views, 'local_today', lambda: today)


def _query_view(name: str = 'Ventas', **definition: object) -> dict:
    return {
        'name': name,
        'kind': 'query',
        'definition': {'measures': ['net_sales'], 'dimensions': ['product'], **definition},
        'period': LAST_MONTH,
        'shared_with': [],
    }


async def _save(client: AsyncClient, body: dict) -> dict:
    response = await client.post(VIEWS, json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _without_as_of(body: object) -> object:
    """The body without `as_of`, its sections' included (customer 360)."""
    if isinstance(body, dict):
        return {key: _without_as_of(value) for key, value in body.items() if key != 'as_of'}
    return body


# ── CRUD ──────────────────────────────────────────────────────────────────────


async def test_the_owner_creates_lists_reads_renames_changes_and_deletes(
    client: AsyncClient, users: None
) -> None:
    saved = await _save(client, _query_view() | {'shared_with': ['ana']})

    assert saved['name'] == 'Ventas'
    assert saved['kind'] == 'query'
    assert saved['owner'] == 'tester'
    assert saved['definition'] == {'measures': ['net_sales'], 'dimensions': ['product']}
    assert saved['period'] == {'key': 'last_month', 'days': None, 'start': None, 'end': None}
    assert saved['shared_with'] == ['ana']
    view = saved['analytics_view_id']

    listed = (await client.get(VIEWS)).json()
    assert [v['analytics_view_id'] for v in listed] == [view]
    assert (await client.get(f'{VIEWS}/{view}')).json() == saved

    renamed = await client.patch(f'{VIEWS}/{view}', json={'name': 'Ventas del mes'})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()['name'] == 'Ventas del mes'
    assert renamed.json()['definition'] == saved['definition']

    changed = await client.patch(
        f'{VIEWS}/{view}',
        json={
            'definition': {'measures': ['net_sales', 'units'], 'grain': 'month'},
            'period': {'key': 'last_n_days', 'days': 30},
            'shared_with': [],
        },
    )
    assert changed.status_code == 200, changed.text
    body = changed.json()
    assert body['definition'] == {'measures': ['net_sales', 'units'], 'grain': 'month'}
    assert body['period'] == {'key': 'last_n_days', 'days': 30, 'start': None, 'end': None}
    assert body['shared_with'] == []
    assert body['name'] == 'Ventas del mes'

    deleted = await client.delete(f'{VIEWS}/{view}')
    assert deleted.status_code == 204
    assert (await client.get(VIEWS)).json() == []
    assert_problem(await client.get(f'{VIEWS}/{view}'), 404, 'resource.not_found')


async def test_a_definition_is_stored_without_its_dates(client: AsyncClient) -> None:
    body = _query_view()
    body['definition'] |= {'start': '2020-01-01', 'end': '2020-01-31'}

    saved = await _save(client, body)

    assert 'start' not in saved['definition'] and 'end' not in saved['definition']


async def test_names_are_unique_per_owner(client: AsyncClient, users: None) -> None:
    first = await _save(client, _query_view())

    again = await client.post(VIEWS, json=_query_view())
    body = assert_problem(again, 409, 'resource.already_exists')
    assert body['params'] == {'resource': 'analytics_view', 'field': 'name'}

    other = await _save(client, _query_view('Otra'))
    renamed = await client.patch(f'{VIEWS}/{other["analytics_view_id"]}', json={'name': 'Ventas'})
    assert_problem(renamed, 409, 'resource.already_exists')
    # Renaming a view to its own name is no clash.
    same = await client.patch(f'{VIEWS}/{first["analytics_view_id"]}', json={'name': 'Ventas'})
    assert same.status_code == 200, same.text

    _as(ANA)
    assert (await client.post(VIEWS, json=_query_view())).status_code == 201


async def test_a_view_is_shared_only_with_active_users(client: AsyncClient, users: None) -> None:
    for user in ('beto', 'nobody'):
        response = await client.post(VIEWS, json=_query_view() | {'shared_with': [user]})
        body = assert_problem(response, 404, 'resource.not_found')
        assert body['params'] == {'resource': 'user', 'id': user}
    saved = await _save(client, _query_view())
    patched = await client.patch(
        f'{VIEWS}/{saved["analytics_view_id"]}', json={'shared_with': ['beto']}
    )
    assert_problem(patched, 404, 'resource.not_found')


# ── Run ───────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ('today', 'start', 'end'),
    [
        (date(2026, 5, 15), '2026-04-01', '2026-04-30'),
        (date(2026, 6, 10), '2026-05-01', '2026-05-31'),
    ],
)
async def test_a_query_view_runs_its_period_resolved_today(
    client: AsyncClient,
    book: None,
    monkeypatch: pytest.MonkeyPatch,
    today: date,
    start: str,
    end: str,
) -> None:
    saved = await _save(client, _query_view())
    _on(monkeypatch, today)

    ran = await client.post(f'{VIEWS}/{saved["analytics_view_id"]}/run')
    queried = await client.post(
        f'{URL}/query',
        json={'measures': ['net_sales'], 'dimensions': ['product'], 'start': start, 'end': end},
    )

    assert ran.status_code == 200, ran.text
    assert (ran.json()['definition']['start'], ran.json()['definition']['end']) == (start, end)
    assert _without_as_of(ran.json()) == _without_as_of(queried.json())
    assert ran.json()['totals']['values']['net_sales'] != '0.00'


async def test_a_run_keeps_the_query_cache_and_its_etag(
    client: AsyncClient, book: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    saved = await _save(client, _query_view())
    _on(monkeypatch, date(2026, 6, 10))
    url = f'{VIEWS}/{saved["analytics_view_id"]}/run'

    first = await client.post(url)
    again = await client.post(url, headers={'If-None-Match': first.headers['ETag']})

    assert first.headers['Cache-Control'].startswith('private')
    assert again.status_code == 304


@pytest.mark.parametrize(
    ('kind', 'definition', 'route'),
    [
        ('dashboard', {'comparison': 'previous_period'}, ('GET', f'{URL}/dashboard', {})),
        ('detail', {'kind': 'orders'}, ('POST', f'{URL}/detail', {'kind': 'orders'})),
        (
            'customer',
            {'customer': CREDIT_CUSTOMER},
            ('GET', f'{URL}/customers/{CREDIT_CUSTOMER}', {}),
        ),
    ],
)
async def test_each_kind_runs_as_its_route(
    client: AsyncClient,
    book: None,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    definition: dict,
    route: tuple[str, str, dict],
) -> None:
    saved = await _save(
        client,
        {'name': kind, 'kind': kind, 'definition': definition, 'period': LAST_MONTH},
    )
    _on(monkeypatch, date(2026, 6, 10))

    ran = await client.post(f'{VIEWS}/{saved["analytics_view_id"]}/run')
    method, url, body = route
    dates = {'start': '2026-05-01', 'end': '2026-05-31'}
    if method == 'GET':
        direct = await client.get(url, params=dates)
    else:
        direct = await client.post(url, json=body | dates)

    assert ran.status_code == 200, ran.text
    assert direct.status_code == 200, direct.text
    assert _without_as_of(ran.json()) == _without_as_of(direct.json())


async def test_a_dashboard_view_runs_with_its_families(
    client: AsyncClient, db: AsyncSession, book: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The UI loads tile groups in parallel (R18): a stored `families` runs with it."""
    saved = await _save(
        client,
        {
            'name': 'Cobranza', 'kind': 'dashboard', 'definition': {'families': ['payments']},
            'period': LAST_MONTH,
        },
    )  # fmt: skip
    _on(monkeypatch, date(2026, 6, 10))

    ran = await client.post(f'{VIEWS}/{saved["analytics_view_id"]}/run')
    direct = await client.get(
        f'{URL}/dashboard',
        params={'start': '2026-05-01', 'end': '2026-05-31', 'families': ['payments']},
    )

    stored = await db.get(AnalyticsView, saved['analytics_view_id'])
    assert json.loads(stored.definition) == {'families': ['payments']}
    assert ran.status_code == 200, ran.text
    assert {k['key'] for k in ran.json()['kpis']} == {'collected', 'collected_today'}
    assert {s['key'] for s in ran.json()['series']} == {'payment_mix'}
    assert ran.json()['alerts'] == []
    assert _without_as_of(ran.json()) == _without_as_of(direct.json())


async def test_a_shared_user_runs_it_with_their_own_scope(
    client: AsyncClient, db: AsyncSession, users: None, book: None
) -> None:
    saved = await _save(client, _query_view() | {'shared_with': ['ana']})
    url = f'{VIEWS}/{saved["analytics_view_id"]}/run'
    owners = await client.post(url)

    _as(ANA)
    anas = await client.post(url)

    assert owners.json()['definition']['facility'] is None
    assert anas.status_code == 200, anas.text
    assert anas.json()['definition']['facility'] == 1


async def test_a_shared_user_is_refused_a_cost_measure_without_37_to_39(
    client: AsyncClient, users: None, book: None
) -> None:
    view = _query_view() | {'shared_with': ['ana']}
    view['definition']['measures'] = ['net_sales', 'gross_margin']
    saved = await _save(client, view)

    _as(ANA)
    response = await client.post(f'{VIEWS}/{saved["analytics_view_id"]}/run')

    body = assert_problem(response, 403, 'auth.forbidden')
    assert {r['system_object'] for r in body['params']['required']} == {37, 38, 39}


async def test_saving_needs_the_definitions_families(client: AsyncClient, users: None) -> None:
    view = _query_view()
    view['definition']['measures'] = ['gross_margin']
    _as(ANA)

    assert_problem(await client.post(VIEWS, json=view), 403, 'auth.forbidden')


# ── Validation ────────────────────────────────────────────────────────────────


async def test_a_view_naming_a_removed_key_is_refused_at_save(client: AsyncClient) -> None:
    view = _query_view()
    view['definition']['measures'] = ['net_sales', 'retired_measure']

    body = assert_problem(await client.post(VIEWS, json=view), 422, 'analytics.view_invalid')

    assert body['params']['key'] == 'measures.1'
    assert 'net_sales' in body['params']['reason']


async def test_a_view_naming_a_removed_key_is_refused_at_run(
    client: AsyncClient, db: AsyncSession
) -> None:
    now = local_now().replace(microsecond=0)
    row = AnalyticsView(
        user='tester', name='Vieja', kind=0,
        definition=json.dumps({'measures': ['net_sales'], 'dimensions': ['retired_dimension']}),
        period=json.dumps(LAST_MONTH), creation_time=now, modification_time=now,
    )  # fmt: skip
    db.add(row)
    await db.commit()

    opened = await client.get(f'{VIEWS}/{row.analytics_view_id}')
    response = await client.post(f'{VIEWS}/{row.analytics_view_id}/run')

    assert opened.status_code == 200, opened.text
    body = assert_problem(response, 422, 'analytics.view_invalid')
    assert body['params']['key'] == 'dimensions.0'


# ── Ownership and sharing ─────────────────────────────────────────────────────


async def test_only_the_owner_changes_or_deletes_it(client: AsyncClient, users: None) -> None:
    saved = await _save(client, _query_view() | {'shared_with': ['ana']})
    url = f'{VIEWS}/{saved["analytics_view_id"]}'

    _as(ANA)

    assert_problem(await client.patch(url, json={'name': 'Mía'}), 403, 'auth.forbidden')
    assert_problem(await client.delete(url), 403, 'auth.forbidden')
    _as(OWNER)
    assert (await client.get(url)).json()['name'] == 'Ventas'


async def test_shared_users_list_it_but_not_whom_it_is_shared_with(
    client: AsyncClient, users: None
) -> None:
    shared = await _save(client, _query_view() | {'shared_with': ['ana']})
    await _save(client, _query_view('Privada'))

    _as(ANA)
    own = await _save(client, _query_view('Mía'))
    listed = (await client.get(VIEWS)).json()
    opened = (await client.get(f'{VIEWS}/{shared["analytics_view_id"]}')).json()

    assert {v['name'] for v in listed} == {'Ventas', 'Mía'}
    assert opened['owner'] == 'tester'
    assert opened['shared_with'] == []
    assert all(v['shared_with'] == [] for v in listed)
    assert own['owner'] == 'ana'


async def test_deleting_the_owner_removes_their_views(
    client: AsyncClient, db: AsyncSession, users: None
) -> None:
    kept = await _save(client, _query_view() | {'shared_with': ['ana']})
    _as(ANA)
    gone = await _save(client, _query_view('De Ana') | {'shared_with': ['tester']})

    user = await db.get(User, 'ana')
    await user_service.delete_user(db, user, updater=1)

    assert (await db.get(AnalyticsView, gone['analytics_view_id'])) is None
    assert (await db.get(AnalyticsView, kept['analytics_view_id'])) is not None
    shares = (await db.execute(select(AnalyticsViewShare))).scalars().all()
    assert shares == []
