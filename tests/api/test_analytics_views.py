"""Spec 038, T049: the saved-view routes' authentication, visibility and period validation.

As in `test_analytics_operations.py`, these run the real services on the integration database.
"""

import json
from collections.abc import AsyncIterator, Iterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import local_now
from app.core.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.main import app
from app.models.analytics import AnalyticsView
from tests.integration.conftest import db, engine, seeded, sessions  # noqa: F401  (fixtures)
from tests.problem import assert_problem

VIEWS = '/api/v1/analytics/views'
CLERK = CurrentUser(
    user_id='tester', session_version=1, administrator=False, facility_id=1, employee_id=1
)
ROUTES = [
    ('GET', VIEWS),
    ('POST', VIEWS),
    ('GET', f'{VIEWS}/1'),
    ('PATCH', f'{VIEWS}/1'),
    ('DELETE', f'{VIEWS}/1'),
    ('POST', f'{VIEWS}/1/run'),
]


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
def _clerk() -> Iterator[None]:
    app.dependency_overrides[get_current_user] = lambda: CLERK
    yield


@pytest.fixture
async def someone_elses(db: AsyncSession) -> int:  # noqa: F811
    """A view owned by `other`, shared with nobody."""
    from app.models.user import User

    db.add(User(user_id='other', password='x', email='o@example.com', employee_id=1))
    await db.flush()
    now = local_now().replace(microsecond=0)
    row = AnalyticsView(
        user='other', name='Ajena', kind=0, definition=json.dumps({'measures': ['net_sales']}),
        period=json.dumps({'key': 'today'}), creation_time=now, modification_time=now,
    )  # fmt: skip
    db.add(row)
    await db.commit()
    return row.analytics_view_id


@pytest.mark.parametrize(('method', 'url'), ROUTES)
async def test_every_view_route_needs_a_token(http: AsyncClient, method: str, url: str) -> None:
    app.dependency_overrides.pop(get_current_user)

    response = await http.request(method, url, json={} if method in ('POST', 'PATCH') else None)

    assert_problem(response, 401, 'auth.not_authenticated')


@pytest.mark.parametrize(('method', 'url'), [r for r in ROUTES if r[1] != VIEWS])
async def test_another_users_unshared_view_is_not_found(
    http: AsyncClient, someone_elses: int, method: str, url: str
) -> None:
    url = url.replace('/1', f'/{someone_elses}')

    response = await http.request(method, url, json={'name': 'Mía'} if method == 'PATCH' else None)

    body = assert_problem(response, 404, 'resource.not_found')
    assert body['params'] == {'resource': 'analytics_view', 'id': someone_elses}
    assert (await http.get(VIEWS)).json() == []


@pytest.mark.parametrize(
    'period',
    [
        {'key': 'last_fortnight'},
        {'key': 'last_n_days'},
        {'key': 'last_n_days', 'days': 0},
        {'key': 'last_n_days', 'days': 3651},
        {'key': 'fixed', 'start': '2026-05-31', 'end': '2026-05-01'},
        {'key': 'fixed', 'start': '2026-05-01'},
    ],
)
async def test_an_invalid_period_is_422(http: AsyncClient, period: dict) -> None:
    body = {
        'name': 'Ventas',
        'kind': 'query',
        'definition': {'measures': ['net_sales']},
        'period': period,
    }

    response = await http.post(VIEWS, json=body)

    assert response.status_code == 422, response.text
    assert any(error['loc'][:2] == ['body', 'period'] for error in response.json()['detail'])
