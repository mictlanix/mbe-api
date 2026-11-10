"""Spec 038, T039: commissions against real tables (SQLite), research R11 and R17.

March 2026 is closed: agent 1 sold order A (10000, 1% label, lifetime share 100%), so March stores
one line of 100.00. April is open: agent 2 sold order B (5000) to agent 1's customer, which splits
25.00 each, and half of order A came back on April 5, -50.00 for agent 1. Agent 1's user works in
facility 1; agent 2 has no user until a test gives them one.
"""

from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_current_user
from app.enums import AccessRight, EntityStatus, SystemObject
from app.main import app as fastapi_app
from app.models.commission import Commission
from app.models.user import AccessPrivilege, User, UserSettings
from app.services import commission_service
from app.services.analytics import cache
from tests.integration.commission_seed import (
    AGENT_A,
    AGENT_B,
    order,
    pay,
    refund,
    setup_commissions,
)
from tests.integration.detail_export import assert_export_matches
from tests.integration.seed import seed_facility
from tests.problem import assert_problem

pytestmark = pytest.mark.usefixtures('seeded')

URL = '/api/v1/analytics'
PERIOD = {'start': '2026-03-01', 'end': '2026-04-30'}
ADMIN = CurrentUser(
    user_id='tester', session_version=1, administrator=True, facility_id=1, employee_id=1
)
D = Decimal


def _caller(employee: int) -> CurrentUser:
    return CurrentUser(
        user_id='tester', session_version=1, administrator=False, facility_id=1,
        employee_id=employee,
    )  # fmt: skip


@pytest.fixture(autouse=True)
def _fresh_cache() -> Iterator[None]:
    cache.responses.clear()
    yield
    cache.responses.clear()


@pytest.fixture
async def book(db: AsyncSession) -> dict[str, int]:
    await setup_commissions(db)
    db.add(UserSettings(user_id='tester', facility_id=1))
    order_a, line_a = await order(
        db, paid=True, zeroed=datetime(2026, 3, 9), when=datetime(2026, 3, 2)
    )
    with patch.object(commission_service, 'local_now', return_value=datetime(2026, 4, 2)):
        await commission_service.close(db, 2026, 3, current=ADMIN)
    order_b, _ = await order(
        db, salesperson=AGENT_B, price='5000', paid=True, zeroed=datetime(2026, 4, 10),
        when=datetime(2026, 4, 8),
    )  # fmt: skip
    await refund(db, order_a, line_a, quantity='0.5', price='10000', when=datetime(2026, 4, 5))
    return {'a': order_a, 'b': order_b}


async def _query(client: AsyncClient, *measures: str, **body: object) -> dict:
    response = await client.post(
        f'{URL}/query', json={'measures': list(measures), **PERIOD, **body}
    )
    assert response.status_code == 200, response.text
    return response.json()


def _rows(body: dict, measure: str = 'commission') -> dict[tuple, Decimal]:
    return {
        tuple(k['value'] for k in row['keys']): D(row['values'][measure]) for row in body['rows']
    }


def _as(caller: CurrentUser) -> None:
    fastapi_app.dependency_overrides[get_current_user] = lambda: caller


async def _grant(db: AsyncSession, *objects: SystemObject) -> None:
    for system_object in objects:
        db.add(
            AccessPrivilege(
                user_id='tester', system_object=int(system_object), privileges=AccessRight.READ
            )
        )
    await db.commit()


# ── Spec 025's figures ────────────────────────────────────────────────────────


async def test_a_closed_month_reads_its_stored_lines(
    client: AsyncClient, db: AsyncSession, book: dict[str, int]
) -> None:
    # A later rate change cannot reach a closed month.
    await db.execute(update(Commission).values(commission_rate=D('0.05')))
    await db.commit()

    body = await _query(
        client, 'commission', 'commission_lines',
        dimensions=['commission_status', 'salesperson'],
        filters=[{'dimension': 'date', 'values': ['2026-03-01']}], grain='month',
    )  # fmt: skip

    assert _rows(body) == {('closed', str(AGENT_A)): D('100.00')}
    stored = await commission_service.read_period(db, 2026, 3)
    assert stored.status == 'closed'
    assert D(body['totals']['values']['commission']) == sum(x.commission for x in stored.lines)


async def test_an_open_month_equals_the_live_computation(
    client: AsyncClient, db: AsyncSession, book: dict[str, int]
) -> None:
    """April is the first open month of this query, so it is spec 025's live month as it is. A
    later open month of a query shows only the in-order remainder (the tests below)."""
    body = await _query(
        client, 'commission', 'commission_base',
        dimensions=['commission_status', 'salesperson', 'commission_participation'],
        filters=[{'dimension': 'date', 'values': ['2026-04-01']}], grain='month',
    )  # fmt: skip

    computed = await commission_service.compute(db, 2026, 4)
    expected: dict[tuple, Decimal] = {}
    for x in computed:
        key = ('provisional', str(x.salesperson), str(x.participation))
        expected[key] = expected.get(key, D(0)) + x.commission
    assert _rows(body) == expected
    assert expected == {
        ('provisional', str(AGENT_A), '1'): D('-50.00'),
        ('provisional', str(AGENT_A), '3'): D('25.00'),
        ('provisional', str(AGENT_B), '2'): D('25.00'),
    }
    assert D(body['totals']['values']['commission_base']) == sum(x.total for x in computed)


# ── Several open months: counted as if closed in order ───────────────────────


async def _by_month_and_detail(client: AsyncClient, period: dict) -> tuple[dict, dict]:
    response = await client.post(
        f'{URL}/query', json={'measures': ['commission'], 'dimensions': ['date'], **period}
    )
    assert response.status_code == 200, response.text
    detail = await client.post(
        f'{URL}/detail', json={'kind': 'commission_lines', **period, 'limit': 100}
    )
    assert detail.status_code == 200, detail.text
    return response.json(), detail.json()


async def test_a_later_open_month_does_not_count_an_earlier_one_again(
    client: AsyncClient, db: AsyncSession
) -> None:
    """February is closed, so March and April are both open. Spec 025's live April picks up
    March's sale too, as if March were closed with nothing; in one query April adds only what
    closing March first would leave, here nothing."""
    await setup_commissions(db)
    with patch.object(commission_service, 'local_now', return_value=datetime(2026, 3, 2)):
        await commission_service.close(db, 2026, 2, current=ADMIN)
    await order(db, paid=True, zeroed=datetime(2026, 3, 9), when=datetime(2026, 3, 2))
    live_april = await commission_service.compute(db, 2026, 4)

    body, detail = await _by_month_and_detail(client, {'start': '2026-03-01', 'end': '2026-04-30'})

    assert sum(x.commission for x in live_april) == D('100.00')
    assert _rows(body) == {('2026-03-01',): D('100.00')}
    assert D(body['totals']['values']['commission']) == D('100.00')
    assert D(detail['sums']['amount']) == D('100.00') and detail['total'] == 1


async def test_partial_payments_over_open_months_add_up_to_the_commission(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Partial-payment scheme, nothing closed: 40% paid in March, the rest in April. Each live
    month gives the commission on everything paid so far; in one query April adds the 60%."""
    await setup_commissions(db, scheme=2)
    order_id, _ = await order(db, when=datetime(2026, 3, 2))
    await pay(db, order_id, '4000', datetime(2026, 3, 10))
    await pay(db, order_id, '6000', datetime(2026, 4, 10))
    live = {m: await commission_service.compute(db, 2026, m) for m in (3, 4)}

    body, detail = await _by_month_and_detail(client, {'start': '2026-03-01', 'end': '2026-04-30'})

    assert [sum(x.commission for x in live[m]) for m in (3, 4)] == [D('40.00'), D('100.00')]
    assert _rows(body) == {('2026-03-01',): D('40.00'), ('2026-04-01',): D('60.00')}
    assert D(detail['sums']['amount']) == D(body['totals']['values']['commission']) == D('100.00')
    assert [i['amount'] for i in detail['items']] == ['60.00', '40.00']
    assert [i['figures']['values']['amount'] for i in detail['items']] == ['60.00', '40.00']


async def test_by_agent_label_participation_and_month(
    client: AsyncClient, book: dict[str, int]
) -> None:
    """Legacy question 37: what each agent earned, from which label and participation, by month;
    the rows add up to the total."""
    by_source = await _query(
        client, 'commission', dimensions=['salesperson', 'label', 'commission_participation']
    )
    by_month = await _query(client, 'commission', dimensions=['date', 'label'], grain='month')

    total = D(by_source['totals']['values']['commission'])
    assert sum(_rows(by_source).values()) == total == sum(_rows(by_month).values())
    assert {keys[1] for keys in _rows(by_source)} == {'1'}  # one label, three participations
    assert {keys[2] for keys in _rows(by_source)} == {'1', '2', '3'}
    assert {keys[0] for keys in _rows(by_month)} == {'2026-03-01', '2026-04-01'}


async def test_by_date_commissions_use_month_grain(
    client: AsyncClient, book: dict[str, int]
) -> None:
    body = await _query(client, 'commission', dimensions=['date'], grain='day')

    assert body['definition']['grain'] == 'month'
    assert _rows(body) == {('2026-03-01',): D('100.00'), ('2026-04-01',): D('0.00')}
    assert [row['keys'][0]['label'] for row in body['rows']] == ['2026-03', '2026-04']


async def test_the_lines_detail_sums_to_the_query(
    client: AsyncClient, book: dict[str, int]
) -> None:
    body = await _query(client, 'commission', 'commission_base', 'commission_lines')
    response = await client.post(
        f'{URL}/detail', json={'kind': 'commission_lines', **PERIOD, 'limit': 100}
    )

    assert response.status_code == 200, response.text
    detail = response.json()
    totals = body['totals']['values']
    assert D(detail['sums']['amount']) == D(totals['commission']) == D('100.00')
    assert D(detail['sums']['base']) == D(totals['commission_base'])
    assert detail['total'] == int(D(totals['commission_lines'])) == 4
    refunded = next(i for i in detail['items'] if i['figures']['values']['movement'] == 'refund')
    assert refunded['document_id'] == book['a']
    assert (
        refunded['figures']['values'].items()
        >= {
            'status': 'provisional',
            'sales_order': str(book['a']),
            'label': '1',
            'label_name': 'CONCRETOS',
            'participation': '1',
            'participation_name': 'CLIENTE VITALICIO',
            'label_rate': '0.010000',
            'participation_rate': '1.000000',
            'base': '-5000.00',
            'amount': '-50.00',
        }.items()
    )
    assert D(refunded['amount']) == D('-50.00')


async def test_the_commission_lines_export_has_the_rates(
    client: AsyncClient, book: dict[str, int]
) -> None:
    lines = await assert_export_matches(client, {'kind': 'commission_lines', **PERIOD})

    refund = next(line for line in lines if line['movement'] == 'refund')
    assert (refund['label_rate'], refund['base'], refund['amount']) == (
        '0.010000', '-5000.00', '-50.00',
    )  # fmt: skip


# ── Who sees what ─────────────────────────────────────────────────────────────


async def test_an_agent_without_112_sees_only_their_own(
    client: AsyncClient, book: dict[str, int]
) -> None:
    _as(_caller(AGENT_A))

    body = await _query(
        client, 'commission', dimensions=['salesperson'],
        filters=[{'dimension': 'salesperson', 'values': [str(AGENT_B)]}],
    )  # fmt: skip
    detail = await client.post(
        f'{URL}/detail',
        json={
            'kind': 'commission_lines', **PERIOD, 'limit': 100,
            'filters': [{'dimension': 'salesperson', 'values': [str(AGENT_B)]}],
        },
    )  # fmt: skip

    assert _rows(body) == {(str(AGENT_A),): D('75.00')}
    assert body['definition']['filters'] == [{'dimension': 'salesperson', 'values': [str(AGENT_A)]}]
    assert detail.status_code == 200, detail.text
    assert {item['salesperson'] for item in detail.json()['items']} == {AGENT_A}
    assert D(detail.json()['sums']['amount']) == D('75.00')


async def test_a_holder_of_112_sees_every_agent(
    client: AsyncClient, db: AsyncSession, book: dict[str, int]
) -> None:
    """With 101 too, so that agent 2, who has no user, is in scope."""
    await _grant(
        db, SystemObject.COMMISSIONS_BY_SALES_PERSON, SystemObject.SEARCH_CREDITS_FROM_ALL_STORES
    )
    _as(_caller(AGENT_A))

    body = await _query(client, 'commission', dimensions=['salesperson'])

    assert _rows(body) == {(str(AGENT_A),): D('75.00'), (str(AGENT_B),): D('25.00')}


async def test_a_non_agent_without_112_is_refused(
    client: AsyncClient, db: AsyncSession, book: dict[str, int]
) -> None:
    _as(_caller(99))
    bare = await client.post(f'{URL}/query', json={'measures': ['commission'], **PERIOD})
    await _grant(db, SystemObject.SALES_BY_CUSTOMER)
    with_sales = await client.post(f'{URL}/query', json={'measures': ['commission'], **PERIOD})
    detail = await client.post(f'{URL}/detail', json={'kind': 'commission_lines', **PERIOD})

    assert_problem(bare, 403, 'auth.forbidden')
    assert_problem(with_sales, 403, 'auth.forbidden')
    assert_problem(detail, 403, 'auth.forbidden')


async def test_facility_scope_is_the_agents_user_facility(
    client: AsyncClient, db: AsyncSession, book: dict[str, int]
) -> None:
    """Agent 2 has no user: only an unscoped caller sees their lines, until a user places them."""
    facility = await seed_facility(db)

    unscoped = await _query(client, 'commission', dimensions=['salesperson', 'facility'])
    first = await _query(client, 'commission', dimensions=['salesperson'], facility=1)
    db.add(
        User(
            user_id='beto', password='x', email='beto@example.com', employee_id=AGENT_B,
            administrator=False, status=EntityStatus.ACTIVE, session_version=1,
        )
    )  # fmt: skip
    await db.flush()
    db.add(UserSettings(user_id='beto', facility_id=facility))
    await db.commit()
    cache.responses.clear()
    second = await _query(client, 'commission', dimensions=['salesperson'], facility=facility)

    assert _rows(unscoped) == {
        (str(AGENT_A), '1'): D('75.00'),
        (str(AGENT_B), None): D('25.00'),
    }
    assert _rows(first) == {(str(AGENT_A),): D('75.00')}
    assert _rows(second) == {(str(AGENT_B),): D('25.00')}


# ── Cost ──────────────────────────────────────────────────────────────────────


async def test_each_month_is_read_once_per_request(
    client: AsyncClient, book: dict[str, int]
) -> None:
    """A 12-month query with a comparison reads 24 distinct months: rows, totals and the
    comparison's slices share each month's lines, and the figures are those of every month."""
    real = commission_service.read_period
    calls: list[tuple[int, int]] = []

    async def counted(db: AsyncSession, year: int, month: int, **kw: object) -> object:
        calls.append((year, month))
        return await real(db, year, month, **kw)

    with patch.object(commission_service, 'read_period', counted):
        body = await _query(
            client, 'commission', dimensions=['salesperson'], start='2025-05-01',
            end='2026-04-30', comparison='previous_period',
        )  # fmt: skip

    assert len(calls) == len(set(calls)) == 24
    assert _rows(body) == {(str(AGENT_A),): D('75.00'), (str(AGENT_B),): D('25.00')}
    assert D(body['totals']['values']['commission']) == D('100.00')
    assert D(body['totals']['comparison']['commission']) == D('0.00')
