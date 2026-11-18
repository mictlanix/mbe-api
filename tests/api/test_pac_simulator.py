"""Tests for the simulated PAC's controls (spec 039, contracts/endpoints.md, research R7).

The HTTP contract only: the document read is patched and the simulator is a mock. What the
simulator does is covered in tests/unit/test_pac_simulated.py and, through these routes, in
tests/integration/test_fiscal_stamping_http.py.
"""

from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import status

from app.core.config import settings
from app.core.error_codes import ErrorCode
from app.core.errors import DomainError
from app.enums import AccessRight
from app.main import app
from app.services.pac import get_pac_provider
from app.services.pac.simulated import SimulatedPac
from tests.api.test_fiscal_documents import _auth, _client, _deny_privileges, _row
from tests.problem import assert_problem

_SERVICE = 'app.services.fiscal_document_service'
_URL = '/api/v1/pac-simulator/fiscal-documents/9'
_KEYS = ('BDCAD3DC-5428-5E2D-89AE-588872E59495', 'CAG190523ES5', 'GR', 4776)
_RECORD = {
    'uuid': 'BDCAD3DC-5428-5E2D-89AE-588872E59495',
    'sat_status': 'valid',
    'cancellation_status': 'pending',
    'next_stamp': None,
    'next_cancellation': 'accepted',
}
_CALLS = [
    ('get', _URL, None),
    ('put', _URL, {'cancellation': 'accepted'}),
    ('post', f'{_URL}/receiver', {'answer': 'accept'}),
]


@pytest.fixture(autouse=True)
def _clear_overrides() -> Generator[None, None, None]:
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def simulator(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """The simulator on, with a mock behind `get_pac_provider`."""
    monkeypatch.setattr(settings, 'pac_provider', 'simulated')
    pac = MagicMock(spec=SimulatedPac)
    pac.get_record = AsyncMock(return_value=_RECORD)
    pac.set_next = AsyncMock()
    pac.answer_as_receiver = AsyncMock()
    app.dependency_overrides[get_pac_provider] = lambda: pac
    return pac


def _document(**overrides: object) -> AsyncMock:
    return AsyncMock(return_value=_row(**overrides))


async def _call(method: str, url: str, body: dict | None):  # noqa: ANN202
    async with await _client() as client:
        if body is None:
            return await client.request(method, url)
        return await client.request(method, url, json=body)


@pytest.mark.asyncio
async def test_get_returns_the_simulators_record(simulator: MagicMock) -> None:
    _auth()
    with patch(f'{_SERVICE}.get_document', _document()):
        response = await _call('get', _URL, None)

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == _RECORD
    simulator.get_record.assert_awaited_once_with(_KEYS)


@pytest.mark.asyncio
async def test_put_sets_the_next_outcome(simulator: MagicMock) -> None:
    _auth()
    body = {'stamp': 'rejected', 'pac_code': 'CFDI40102', 'pac_message': 'Sello inválido'}
    with patch(f'{_SERVICE}.get_document', _document(status='sealed', stamp_uuid=None)):
        response = await _call('put', _URL, body)

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == _RECORD
    simulator.set_next.assert_awaited_once_with(
        (None, *_KEYS[1:]),
        stamp='rejected',
        cancellation=None,
        pac_code='CFDI40102',
        pac_message='Sello inválido',
    )


@pytest.mark.asyncio
async def test_receiver_answers_the_pending_cancellation(simulator: MagicMock) -> None:
    _auth()
    with patch(f'{_SERVICE}.get_document', _document(status='cancellation_pending', batch=None)):
        response = await _call('post', f'{_URL}/receiver', {'answer': 'reject'})

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == _RECORD
    simulator.answer_as_receiver.assert_awaited_once_with((_KEYS[0], _KEYS[1], '', 4776), 'reject')


@pytest.mark.asyncio
@pytest.mark.parametrize(('method', 'url', 'body'), _CALLS)
async def test_routes_require_authentication(
    simulator: MagicMock, method: str, url: str, body: dict | None
) -> None:
    response = await _call(method, url, body)

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
@pytest.mark.parametrize(('method', 'url', 'body'), _CALLS)
async def test_routes_require_update_on_fiscal_documents(
    simulator: MagicMock, method: str, url: str, body: dict | None
) -> None:
    _auth(administrator=False)
    _deny_privileges()
    response = await _call(method, url, body)

    body = assert_problem(response, 403, 'auth.forbidden')
    assert body['params']['required'] == [{'system_object': 23, 'right': int(AccessRight.UPDATE)}]
    simulator.set_next.assert_not_awaited()
    simulator.answer_as_receiver.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(('method', 'url', 'body'), _CALLS)
async def test_an_unknown_document_is_404(
    simulator: MagicMock, method: str, url: str, body: dict | None
) -> None:
    _auth()
    with patch(f'{_SERVICE}.get_document', AsyncMock(return_value=None)):
        response = await _call(method, url, body)

    problem = assert_problem(response, 404, 'resource.not_found')
    assert problem['params'] == {'resource': 'fiscal_document', 'id': 9}


@pytest.mark.asyncio
@pytest.mark.parametrize(('method', 'url', 'body'), _CALLS)
async def test_a_draft_is_not_sealed(
    simulator: MagicMock, method: str, url: str, body: dict | None
) -> None:
    _auth()
    with patch(f'{_SERVICE}.get_document', _document(status='draft', stamp_uuid=None)):
        response = await _call(method, url, body)

    assert_problem(response, 409, 'fiscal_document.not_sealed')
    simulator.get_record.assert_not_awaited()
    simulator.set_next.assert_not_awaited()


@pytest.mark.asyncio
async def test_receiver_with_nothing_pending_is_409(simulator: MagicMock) -> None:
    _auth()
    simulator.answer_as_receiver.side_effect = DomainError(
        ErrorCode.FISCAL_DOCUMENT_NOTHING_TO_REFRESH, 'No cancellation is pending'
    )
    with patch(f'{_SERVICE}.get_document', _document()):
        response = await _call('post', f'{_URL}/receiver', {'answer': 'accept'})

    assert_problem(response, 409, 'fiscal_document.nothing_to_refresh')


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('method', 'url', 'body'),
    [
        ('put', _URL, {'stamp': 'accepted'}),
        ('put', _URL, {'cancellation': 'stamped'}),
        ('put', _URL, {'pac_code': 'C' * 21}),
        ('put', _URL, {'pac_message': 'm' * 501}),
        ('post', f'{_URL}/receiver', {'answer': 'accepted'}),
        ('post', f'{_URL}/receiver', {}),
    ],
)
async def test_an_invalid_body_is_422(
    simulator: MagicMock, method: str, url: str, body: dict
) -> None:
    _auth()
    with patch(f'{_SERVICE}.get_document', _document()):
        response = await _call(method, url, body)

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    simulator.set_next.assert_not_awaited()
    simulator.answer_as_receiver.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('authenticated', [True, False])
@pytest.mark.parametrize(('method', 'url', 'body'), _CALLS)
async def test_every_route_is_not_found_unless_the_simulator_is_on(
    monkeypatch: pytest.MonkeyPatch, authenticated: bool, method: str, url: str, body: dict | None
) -> None:
    monkeypatch.setattr(settings, 'pac_provider', 'none')
    if authenticated:
        _auth()

    response = await _call(method, url, body)

    problem = assert_problem(response, 404, 'request.route_not_found')
    assert not problem.get('params')
