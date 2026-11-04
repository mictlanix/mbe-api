"""The commission settings routes (spec 025): the HTTP contract, with the service patched."""

from collections.abc import Generator
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, status
from httpx import ASGITransport, AsyncClient

from app.core.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.enums import SourceType
from app.main import app
from app.schemas.commission import (
    CommissionAgentCreate,
    CommissionAgentRateCreate,
    CommissionAgentRateUpdate,
    CommissionAgentUpdate,
    CommissionLabelCreate,
    CommissionLabelUpdate,
    CommissionParticipationBody,
)
from app.services import commission_service

BASE = '/api/v1/commissions'
CURRENT = CurrentUser(
    user_id='tester', session_version=1, administrator=True, facility_id=1, employee_id=7
)


@pytest.fixture(autouse=True)
def _clear_overrides() -> Generator[None, None, None]:
    yield
    app.dependency_overrides.clear()


def _auth() -> None:
    app.dependency_overrides[get_current_user] = lambda: CURRENT

    async def _noop_db():
        yield None

    app.dependency_overrides[get_db] = _noop_db


def _deny_privileges() -> None:
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session = SimpleNamespace(execute=AsyncMock(return_value=result))

    async def _db():
        yield session

    app.dependency_overrides[get_db] = _db


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


_LABEL = SimpleNamespace(
    commission_id=1, name='CONCRETOS', commission_rate=Decimal('0.025'), comment='0'
)
_PARTICIPATION = SimpleNamespace(commission_participation_id=1, name='CLIENTE VITALICIO')
_AGENT = SimpleNamespace(employee=17, name='Ana Ruiz', scheme=1)
_RATE = SimpleNamespace(
    commission_salesperson_id=5,
    salesperson=17,
    commission=1,
    commission_participation=1,
    participation_rate=Decimal('0.5'),
)
_PRODUCT_LABEL = SimpleNamespace(
    product=1, product_code='C-1', product_name='Concreto', commission=1
)


def _missing(what: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f'{what} not found')


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


# method, path, json body, service function, success status, service return value
_WRITES: list[tuple[str, str, dict | None, str, int, Any]] = [
    ('POST', '/labels', {'name': 'X', 'commission_rate': '0.1'}, 'create_label', 201, _LABEL),
    ('PUT', '/labels/1', {'commission_rate': '0.03'}, 'update_label', 200, _LABEL),
    ('DELETE', '/labels/1', None, 'delete_label', 204, None),
    ('POST', '/participations', {'name': 'X'}, 'create_participation', 201, _PARTICIPATION),
    ('PUT', '/participations/1', {'name': 'X'}, 'update_participation', 200, _PARTICIPATION),
    ('DELETE', '/participations/1', None, 'delete_participation', 204, None),
    ('POST', '/agents', {'employee': 17}, 'create_agent', 201, _AGENT),
    ('PUT', '/agents/17', {'scheme': 2}, 'update_agent', 200, _AGENT),
    ('DELETE', '/agents/17', None, 'delete_agent', 204, None),
    (
        'POST',
        '/agent-rates',
        {
            'salesperson': 17,
            'commission': 1,
            'commission_participation': 1,
            'participation_rate': '0.5',
        },
        'create_agent_rate',
        201,
        _RATE,
    ),
    ('PUT', '/agent-rates/5', {'participation_rate': '0.4'}, 'update_agent_rate', 200, _RATE),
    ('DELETE', '/agent-rates/5', None, 'delete_agent_rate', 204, None),
    ('PUT', '/product-labels/1', {'commission': 1}, 'set_product_label', 200, _PRODUCT_LABEL),
    ('DELETE', '/product-labels/1', None, 'delete_product_label', 204, None),
]
_IDS = [f'{m} {p}' for m, p, *_ in _WRITES]
_READS = [
    ('/labels', 'list_labels', [_LABEL]),
    ('/participations', 'list_participations', [_PARTICIPATION]),
    ('/agents', 'list_agents', [_AGENT]),
    ('/agent-rates', 'list_agent_rates', [_RATE]),
    ('/product-labels', 'list_product_labels', ([_PRODUCT_LABEL], 1)),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(('path', 'function', 'value'), _READS)
async def test_each_list_is_200(path: str, function: str, value: Any) -> None:
    _auth()
    with patch.object(commission_service, function, AsyncMock(return_value=value)):
        async with _client() as client:
            response = await client.get(f'{BASE}{path}')

    assert response.status_code == status.HTTP_200_OK, response.text
    body = response.json()
    if path == '/product-labels':
        assert body == {
            'items': [
                {'product': 1, 'product_code': 'C-1', 'product_name': 'Concreto', 'commission': 1}
            ],
            'total': 1,
        }
    elif path == '/agents':
        assert body == [{'employee': 17, 'name': 'Ana Ruiz', 'scheme': 1}]
    elif path == '/labels':
        assert body[0]['commission_id'] == 1 and body[0]['commission_rate'] == '0.025'
    else:
        assert len(body) == 1


@pytest.mark.asyncio
async def test_agent_rates_are_filtered_and_product_labels_paged() -> None:
    _auth()
    rates = AsyncMock(return_value=[])
    labels = AsyncMock(return_value=([], 0))
    with (
        patch.object(commission_service, 'list_agent_rates', rates),
        patch.object(commission_service, 'list_product_labels', labels),
    ):
        async with _client() as client:
            await client.get(f'{BASE}/agent-rates', params={'salesperson': 17, 'commission': 2})
            await client.get(
                f'{BASE}/product-labels',
                params={'commission': 2, 'product': 3, 'skip': 5, 'limit': 10},
            )
            too_many = await client.get(f'{BASE}/product-labels', params={'limit': 101})

    assert rates.await_args.kwargs == {'salesperson': 17, 'commission': 2}
    assert labels.await_args.kwargs == {'commission': 2, 'product': 3, 'skip': 5, 'limit': 10}
    assert too_many.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


@pytest.mark.asyncio
@pytest.mark.parametrize(('method', 'path', 'body', 'function', 'code', 'value'), _WRITES, ids=_IDS)
async def test_each_write_succeeds(
    method: str, path: str, body: dict | None, function: str, code: int, value: Any
) -> None:
    _auth()
    with patch.object(commission_service, function, AsyncMock(return_value=value)) as service:
        async with _client() as client:
            response = await client.request(method, f'{BASE}{path}', json=body)

    assert response.status_code == code, response.text
    assert service.await_args.kwargs['current'].employee_id == 7


@pytest.mark.asyncio
@pytest.mark.parametrize(('method', 'path', 'body', 'function', 'code', 'value'), _WRITES, ids=_IDS)
async def test_an_unknown_id_is_404(
    method: str, path: str, body: dict | None, function: str, code: int, value: Any
) -> None:
    _auth()
    with patch.object(commission_service, function, AsyncMock(side_effect=_missing('Thing'))):
        async with _client() as client:
            response = await client.request(method, f'{BASE}{path}', json=body)

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()['detail'] == 'Thing not found'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('method', 'path', 'body'),
    [
        ('POST', '/labels', {'name': 'X', 'commission_rate': '1.5'}),
        ('POST', '/labels', {'name': 'X', 'commission_rate': '-0.1'}),
        ('POST', '/labels', {'name': '', 'commission_rate': '0.1'}),
        ('POST', '/labels', {'name': 'X' * 51, 'commission_rate': '0.1'}),
        ('POST', '/labels', {'name': 'X', 'commission_rate': '0.1', 'comment': 'c' * 51}),
        ('PUT', '/labels/1', {'commission_rate': '2'}),
        ('PUT', '/labels/1', {'name': ''}),
        ('POST', '/participations', {'name': ''}),
        ('PUT', '/participations/1', {'name': ''}),
        ('POST', '/agents', {'employee': 17, 'scheme': 3}),
        ('PUT', '/agents/17', {'scheme': 3}),
        ('PUT', '/agents/17', {'scheme': 0}),
        (
            'POST',
            '/agent-rates',
            {
                'salesperson': 17,
                'commission': 1,
                'commission_participation': 1,
                'participation_rate': '1.01',
            },
        ),
        ('PUT', '/agent-rates/5', {'participation_rate': '-1'}),
    ],
)
async def test_an_invalid_body_is_422(method: str, path: str, body: dict) -> None:
    _auth()
    async with _client() as client:
        response = await client.request(method, f'{BASE}{path}', json=body)

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('method', 'path', 'body', 'function', 'detail'),
    [
        ('POST', '/agents', {'employee': 17}, 'create_agent', 'Employee is already an agent'),
        (
            'POST',
            '/agent-rates',
            {
                'salesperson': 17,
                'commission': 1,
                'commission_participation': 1,
                'participation_rate': '0.5',
            },
            'create_agent_rate',
            'Rate already exists for this agent, label and participation',
        ),
        ('DELETE', '/labels/1', None, 'delete_label', 'Still referenced by commission_product'),
        ('DELETE', '/participations/1', None, 'delete_participation', 'Still referenced'),
        ('DELETE', '/agents/17', None, 'delete_agent', 'Still referenced'),
    ],
)
async def test_a_conflict_is_409(
    method: str, path: str, body: dict | None, function: str, detail: str
) -> None:
    _auth()
    with patch.object(commission_service, function, AsyncMock(side_effect=_conflict(detail))):
        async with _client() as client:
            response = await client.request(method, f'{BASE}{path}', json=body)

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()['detail'] == detail


@pytest.mark.asyncio
async def test_an_agent_scheme_defaults_to_1() -> None:
    _auth()
    with patch.object(commission_service, 'create_agent', AsyncMock(return_value=_AGENT)) as svc:
        async with _client() as client:
            await client.post(f'{BASE}/agents', json={'employee': 17})

    assert svc.await_args.args[1].scheme == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(('method', 'path', 'body', 'function', 'code', 'value'), _WRITES, ids=_IDS)
async def test_without_a_token_is_401(
    method: str, path: str, body: dict | None, function: str, code: int, value: Any
) -> None:
    async with _client() as client:
        response = await client.request(method, f'{BASE}{path}', json=body)

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_a_list_without_a_token_is_401() -> None:
    async with _client() as client:
        response = await client.get(f'{BASE}/labels')

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
@pytest.mark.parametrize(('method', 'path', 'body', 'function', 'code', 'value'), _WRITES, ids=_IDS)
async def test_without_the_privilege_is_403(
    method: str, path: str, body: dict | None, function: str, code: int, value: Any
) -> None:
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id='tester', session_version=1, administrator=False, facility_id=1, employee_id=7
    )
    _deny_privileges()
    with patch.object(commission_service, function, AsyncMock()) as service:
        async with _client() as client:
            response = await client.request(method, f'{BASE}{path}', json=body)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    service.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(('path', 'function', 'value'), _READS)
async def test_a_list_without_the_privilege_is_403(path: str, function: str, value: Any) -> None:
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id='tester', session_version=1, administrator=False, facility_id=1, employee_id=7
    )
    _deny_privileges()
    with patch.object(commission_service, function, AsyncMock()) as service:
        async with _client() as client:
            response = await client.get(f'{BASE}{path}')

    assert response.status_code == status.HTTP_403_FORBIDDEN
    service.assert_not_awaited()


# ── The service: one incidence per write, in the write's own transaction ───────


def _session(get: list[Any], *, first: Any = None) -> MagicMock:
    result = MagicMock()
    result.first.return_value = None
    result.one.return_value = SimpleNamespace(employee=17)
    result.scalars.return_value.first.return_value = first
    db = MagicMock()
    db.get = AsyncMock(side_effect=get)
    db.execute = AsyncMock(return_value=result)
    for name in ('flush', 'commit', 'delete'):
        setattr(db, name, AsyncMock())
    return db


def _label() -> SimpleNamespace:
    return SimpleNamespace(
        commission_id=1, name='CONCRETOS', commission_rate=Decimal('0.025'), comment='0'
    )


_SVC = commission_service
_AUDITED = [
    (
        'create_label',
        lambda: (CommissionLabelCreate(name='X', commission_rate=Decimal('0.1')),),
        lambda: _session([]),
    ),
    (
        'update_label',
        lambda: (1, CommissionLabelUpdate(commission_rate=Decimal('0.03'))),
        lambda: _session([_label()]),
    ),
    ('delete_label', lambda: (1,), lambda: _session([_label()])),
    (
        'create_participation',
        lambda: (CommissionParticipationBody(name='X'),),
        lambda: _session([]),
    ),
    (
        'update_participation',
        lambda: (1, CommissionParticipationBody(name='Y')),
        lambda: _session([SimpleNamespace(name='X')]),
    ),
    ('delete_participation', lambda: (1,), lambda: _session([SimpleNamespace(name='X')])),
    (
        'create_agent',
        lambda: (CommissionAgentCreate(employee=17),),
        lambda: _session([object(), None]),
    ),
    (
        'update_agent',
        lambda: (17, CommissionAgentUpdate(scheme=2)),
        lambda: _session([SimpleNamespace(scheme=1)]),
    ),
    ('delete_agent', lambda: (17,), lambda: _session([SimpleNamespace(scheme=1)])),
    (
        'create_agent_rate',
        lambda: (
            CommissionAgentRateCreate(
                salesperson=17,
                commission=1,
                commission_participation=1,
                participation_rate=Decimal('0.5'),
            ),
        ),
        lambda: _session([object(), object(), object()]),
    ),
    (
        'update_agent_rate',
        lambda: (5, CommissionAgentRateUpdate(participation_rate=Decimal('0.4'))),
        lambda: _session([SimpleNamespace(participation_rate=Decimal('0.5'))]),
    ),
    (
        'delete_agent_rate',
        lambda: (5,),
        lambda: _session(
            [SimpleNamespace(salesperson=17, commission=1, participation_rate=Decimal('0.5'))]
        ),
    ),
    ('set_product_label', lambda: (1, 1), lambda: _session([object(), object()])),
    (
        'set_product_label',
        lambda: (1, 2),
        lambda: _session([object(), object()], first=SimpleNamespace(commission=1)),
    ),
    (
        'delete_product_label',
        lambda: (1,),
        lambda: _session([], first=SimpleNamespace(commission=1)),
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(('function', 'args', 'session'), _AUDITED)
async def test_each_write_records_one_incidence_before_commit(
    function: str, args: Any, session: Any
) -> None:
    db = session()
    order: list[str] = []
    db.commit.side_effect = lambda: order.append('commit')
    with (
        patch.object(
            _SVC.incidences, 'record', side_effect=lambda *a, **k: order.append('record')
        ) as record,
        patch.object(_SVC, 'assert_not_referenced', AsyncMock()),
    ):
        await getattr(_SVC, function)(db, *args(), current=CURRENT)

    record.assert_called_once()
    kwargs = record.call_args.kwargs
    assert kwargs['source'] == SourceType.COMMISSION
    assert kwargs['updater'] == 7
    assert kwargs['reason'] and kwargs['context']
    assert order == ['record', 'commit']


@pytest.mark.asyncio
async def test_a_label_is_created_with_a_default_comment() -> None:
    db = _session([])
    with patch.object(_SVC.incidences, 'record'):
        await _SVC.create_label(
            db, CommissionLabelCreate(name='X', commission_rate=Decimal('0.1')), current=CURRENT
        )

    assert db.add.call_args.args[0].comment == '0'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('function', 'args', 'get', 'status_code', 'detail'),
    [
        (
            'update_label',
            (9, CommissionLabelUpdate(name='X')),
            [None],
            404,
            'Commission label not found',
        ),
        ('delete_agent', (9,), [None], 404, 'Commission agent not found'),
        (
            'create_agent',
            (CommissionAgentCreate(employee=9),),
            [None],
            404,
            'Employee not found',
        ),
        (
            'create_agent',
            (CommissionAgentCreate(employee=17),),
            [object(), object()],
            409,
            'Employee is already an agent',
        ),
        (
            'create_agent_rate',
            (
                CommissionAgentRateCreate(
                    salesperson=9,
                    commission=1,
                    commission_participation=1,
                    participation_rate=Decimal('0.5'),
                ),
            ),
            [None],
            422,
            'Salesperson is not an agent',
        ),
        ('set_product_label', (9, 1), [None], 404, 'Product not found'),
        ('set_product_label', (1, 9), [object(), None], 404, 'Commission label not found'),
    ],
)
async def test_the_service_refuses_with_a_clear_message(
    function: str, args: tuple, get: list, status_code: int, detail: str
) -> None:
    db = _session(get)
    with patch.object(_SVC.incidences, 'record') as record, pytest.raises(HTTPException) as caught:
        await getattr(_SVC, function)(db, *args, current=CURRENT)

    assert caught.value.status_code == status_code
    assert caught.value.detail == detail
    if detail == 'Salesperson is not an agent':
        assert caught.value.code == 'commission_agent.salesperson_not_agent'
        assert caught.value.params == {'salesperson_id': 9}
    record.assert_not_called()


@pytest.mark.asyncio
async def test_a_duplicate_rate_triple_is_409() -> None:
    db = _session([object(), object(), object()])
    db.execute.return_value.first.return_value = (5,)
    data = CommissionAgentRateCreate(
        salesperson=17, commission=1, commission_participation=1, participation_rate=Decimal('0.5')
    )
    with patch.object(_SVC.incidences, 'record') as record, pytest.raises(HTTPException) as caught:
        await _SVC.create_agent_rate(db, data, current=CURRENT)

    assert caught.value.status_code == status.HTTP_409_CONFLICT
    assert caught.value.detail == 'Rate already exists for this agent, label and participation'
    record.assert_not_called()


@pytest.mark.asyncio
async def test_deleting_a_row_in_use_is_refused_and_leaves_no_incidence() -> None:
    db = _session([_label()])
    refused = AsyncMock(side_effect=_conflict('Still referenced by commission_product (3)'))
    with (
        patch.object(_SVC, 'assert_not_referenced', refused),
        patch.object(_SVC.incidences, 'record') as record,
        pytest.raises(HTTPException) as caught,
    ):
        await _SVC.delete_label(db, 1, current=CURRENT)

    assert caught.value.status_code == status.HTTP_409_CONFLICT
    record.assert_not_called()
    db.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_product_without_a_label_cannot_be_unlabelled() -> None:
    db = _session([])
    with patch.object(_SVC.incidences, 'record'), pytest.raises(HTTPException) as caught:
        await _SVC.delete_product_label(db, 1, current=CURRENT)

    assert caught.value.status_code == status.HTTP_404_NOT_FOUND
