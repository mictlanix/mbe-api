"""Spec 038, T059: the schedule routes on real tables (SQLite), FR-160–FR-162, R15–R16.

The owner is the administrator `tester`; `ana` is a facility-1 clerk with the sales reports only,
`beto` an inactive user. The clock is frozen at Monday 1 June 2026, 10:00, business time.
"""

from collections.abc import Iterator
from datetime import datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_current_user
from app.enums import AccessRight, EntityStatus, SystemObject
from app.main import app as fastapi_app
from app.models.analytics import AnalyticsSchedule, AnalyticsSend
from app.models.user import AccessPrivilege, User
from app.services.analytics import schedules
from tests.problem import assert_problem

pytestmark = pytest.mark.usefixtures('seeded')

URL = '/api/v1/analytics'
VIEWS = f'{URL}/views'
SCHEDULES = f'{URL}/schedules'
NOW = datetime(2026, 6, 1, 10)
ANA = CurrentUser(
    user_id='ana', session_version=1, administrator=False, facility_id=1, employee_id=1
)


@pytest.fixture(autouse=True)
def _frozen(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(schedules, 'local_now', lambda: NOW)
    yield


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


def _as(user: CurrentUser) -> None:
    fastapi_app.dependency_overrides[get_current_user] = lambda: user


async def _view(client: AsyncClient, name: str = 'Ventas', measures: tuple = ('net_sales',)) -> int:
    body = {
        'name': name,
        'kind': 'query',
        'definition': {'measures': list(measures)},
        'period': {'key': 'last_month'},
    }
    response = await client.post(VIEWS, json=body)
    assert response.status_code == 201, response.text
    return response.json()['analytics_view_id']


def _schedule(view: int, **changes: object) -> dict:
    return {
        'analytics_view': view,
        'cadence': 'daily',
        'weekdays': [],
        'month_day': None,
        'time_of_day': '08:00:00',
        'format': 'xlsx',
        'subject': 'Ventas del mes',
        'recipients': [],
        'enabled': True,
    } | changes


async def _create(client: AsyncClient, body: dict) -> dict:
    response = await client.post(SCHEDULES, json=body)
    assert response.status_code == 201, response.text
    return response.json()


# ── CRUD ──────────────────────────────────────────────────────────────────────


async def test_the_owner_creates_lists_reads_changes_disables_and_deletes(
    client: AsyncClient, users: None
) -> None:
    view = await _view(client)

    saved = await _create(client, _schedule(view, recipients=['ana']))

    assert saved['analytics_view'] == view
    assert saved['cadence'] == 'daily'
    assert saved['recipients'] == ['ana']
    assert saved['enabled'] is True
    # 08:00 already passed on 1 June: the first run is tomorrow.
    assert saved['next_run_time'] == '2026-06-02T08:00:00'
    schedule = saved['analytics_schedule_id']
    assert (await client.get(SCHEDULES)).json() == [saved]
    assert (await client.get(f'{SCHEDULES}/{schedule}')).json() == saved

    changed = await client.patch(
        f'{SCHEDULES}/{schedule}',
        json={'cadence': 'weekly', 'weekdays': [3, 5], 'time_of_day': '07:30:00'},
    )
    assert changed.status_code == 200, changed.text
    body = changed.json()
    assert body['weekdays'] == [3, 5]
    assert body['next_run_time'] == '2026-06-03T07:30:00'  # Wednesday
    assert body['subject'] == 'Ventas del mes'

    monthly = await client.patch(
        f'{SCHEDULES}/{schedule}', json={'cadence': 'monthly', 'month_day': 31}
    )
    assert monthly.status_code == 200, monthly.text
    assert monthly.json()['weekdays'] == []  # weekly only, so cleared
    assert monthly.json()['next_run_time'] == '2026-06-30T07:30:00'

    disabled = await client.patch(f'{SCHEDULES}/{schedule}', json={'enabled': False})
    assert disabled.json()['enabled'] is False

    deleted = await client.delete(f'{SCHEDULES}/{schedule}')
    assert deleted.status_code == 204
    assert (await client.get(SCHEDULES)).json() == []
    assert_problem(await client.get(f'{SCHEDULES}/{schedule}'), 404, 'resource.not_found')


async def test_administrators_list_every_schedule_and_others_their_own(
    client: AsyncClient, users: None
) -> None:
    mine = await _create(client, _schedule(await _view(client)))
    _as(ANA)
    hers = await _create(client, _schedule(await _view(client, 'De Ana')))

    assert [s['analytics_schedule_id'] for s in (await client.get(SCHEDULES)).json()] == [
        hers['analytics_schedule_id']
    ]
    assert_problem(
        await client.get(f'{SCHEDULES}/{mine["analytics_schedule_id"]}'), 404, 'resource.not_found'
    )

    fastapi_app.dependency_overrides.pop(get_current_user)
    fastapi_app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id='tester', session_version=1, administrator=True, facility_id=1, employee_id=1
    )
    listed = [s['analytics_schedule_id'] for s in (await client.get(SCHEDULES)).json()]
    assert sorted(listed) == sorted([mine['analytics_schedule_id'], hers['analytics_schedule_id']])
    # An administrator reads another's schedule and its sends, but changes only their own.
    other = f'{SCHEDULES}/{hers["analytics_schedule_id"]}'
    assert (await client.get(other)).status_code == 200
    assert (await client.get(f'{other}/sends')).status_code == 200
    assert_problem(await client.patch(other, json={'enabled': False}), 403, 'auth.forbidden')
    assert_problem(await client.delete(other), 403, 'auth.forbidden')


async def test_only_the_views_owner_schedules_it(client: AsyncClient, users: None) -> None:
    unshared = await _view(client)
    shared = await _view(client, 'Compartida')
    await client.patch(f'{VIEWS}/{shared}', json={'shared_with': ['ana']})
    _as(ANA)

    assert_problem(await client.post(SCHEDULES, json=_schedule(shared)), 403, 'auth.forbidden')
    assert_problem(
        await client.post(SCHEDULES, json=_schedule(unshared)), 404, 'resource.not_found'
    )


@pytest.mark.parametrize(
    'changes',
    [
        {'cadence': 'weekly'},
        {'cadence': 'weekly', 'weekdays': [8]},
        {'cadence': 'monthly'},
        {'cadence': 'monthly', 'month_day': 0},
        {'cadence': 'monthly', 'month_day': 32},
        {'cadence': 'daily', 'weekdays': [1]},
        {'cadence': 'daily', 'month_day': 1},
        {'cadence': 'hourly'},
        {'subject': ''},
        {'recipients': [f'u{n}' for n in range(11)]},
    ],
)
async def test_an_invalid_schedule_is_422(client: AsyncClient, changes: dict) -> None:
    view = await _view(client)

    response = await client.post(SCHEDULES, json=_schedule(view, **changes))

    assert response.status_code == 422, response.text


async def test_a_change_that_leaves_weekly_without_weekdays_is_422(client: AsyncClient) -> None:
    saved = await _create(client, _schedule(await _view(client)))

    response = await client.patch(
        f'{SCHEDULES}/{saved["analytics_schedule_id"]}', json={'cadence': 'weekly'}
    )

    assert response.status_code == 422, response.text


async def test_a_dashboard_is_scheduled_only_as_a_pdf(client: AsyncClient) -> None:
    response = await client.post(
        VIEWS,
        json={'name': 'Tablero', 'kind': 'dashboard', 'definition': {}, 'period': {'key': 'today'}},
    )
    view = response.json()['analytics_view_id']

    refused = await client.post(SCHEDULES, json=_schedule(view, format='xlsx'))

    assert_problem(refused, 422, 'analytics.export_source_invalid')
    await _create(client, _schedule(view, format='pdf'))


# ── Recipients ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ('measures', 'recipient', 'family'),
    [
        (('net_sales',), 'beto', 'sales'),  # inactive
        (('on_hand',), 'ana', 'inventory'),  # lacks the family
        (('net_sales', 'gross_margin'), 'ana', 'cost'),  # a sensitive measure
    ],
)
async def test_an_ineligible_recipient_is_refused(
    client: AsyncClient, users: None, measures: tuple, recipient: str, family: str
) -> None:
    view = await _view(client, measures=measures)

    response = await client.post(SCHEDULES, json=_schedule(view, recipients=['ana', recipient]))

    body = assert_problem(response, 422, 'analytics.recipient_ineligible')
    assert body['params'] == {'user': recipient, 'family': family}


async def test_a_change_of_recipients_is_checked_again(client: AsyncClient, users: None) -> None:
    saved = await _create(client, _schedule(await _view(client), recipients=['ana']))

    response = await client.patch(
        f'{SCHEDULES}/{saved["analytics_schedule_id"]}', json={'recipients': ['beto']}
    )

    assert_problem(response, 422, 'analytics.recipient_ineligible')


async def test_an_unknown_recipient_is_not_found(client: AsyncClient) -> None:
    response = await client.post(SCHEDULES, json=_schedule(await _view(client), recipients=['x']))

    body = assert_problem(response, 404, 'resource.not_found')
    assert body['params'] == {'resource': 'user', 'id': 'x'}


# ── Deleting and history ──────────────────────────────────────────────────────


async def test_deleting_a_view_deletes_its_schedules_and_their_sends(
    client: AsyncClient, db: AsyncSession
) -> None:
    view = await _view(client)
    saved = await _create(client, _schedule(view))
    db.add(
        AnalyticsSend(
            analytics_schedule=saved['analytics_schedule_id'], occurrence=NOW, started_at=NOW,
            finished_at=NOW, outcome=1, recipients='{"sent_to": [], "dropped": []}', format=0,
        )
    )  # fmt: skip
    await db.commit()

    assert (await client.delete(f'{VIEWS}/{view}')).status_code == 204

    assert (await db.execute(select(func.count()).select_from(AnalyticsSchedule))).scalar() == 0
    assert (await db.execute(select(func.count()).select_from(AnalyticsSend))).scalar() == 0


async def test_the_send_history_is_newest_first_and_paged(
    client: AsyncClient, db: AsyncSession
) -> None:
    saved = await _create(client, _schedule(await _view(client)))
    schedule = saved['analytics_schedule_id']
    for day in range(1, 4):
        at = datetime(2026, 5, day, 8)
        db.add(
            AnalyticsSend(
                analytics_schedule=schedule, occurrence=at, started_at=at,
                finished_at=at + timedelta(seconds=5), outcome=1, format=0, size=100, row_count=2,
                recipients='{"sent_to": ["ana"], "dropped": [{"user": "beto", "family": "sales"}]}',
            )
        )  # fmt: skip
    await db.commit()

    first = (await client.get(f'{SCHEDULES}/{schedule}/sends', params={'limit': 2})).json()
    rest = (await client.get(f'{SCHEDULES}/{schedule}/sends', params={'skip': 2})).json()

    assert first['total'] == 3
    assert [s['occurrence'] for s in first['items']] == [
        '2026-05-03T08:00:00',
        '2026-05-02T08:00:00',
    ]
    assert [s['occurrence'] for s in rest['items']] == ['2026-05-01T08:00:00']
    item = first['items'][0]
    assert item['outcome'] == 'sent'
    assert item['sent_to'] == ['ana']
    assert item['dropped'] == ['beto']
    assert item['size'] == 100 and item['row_count'] == 2
