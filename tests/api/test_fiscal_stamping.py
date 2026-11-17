"""Tests for the stamping routes (spec 039, contracts/endpoints.md).

The HTTP contract only: `fiscal_stamping` and the document reload are patched, and the provider is
overridden. What the service does is covered in tests/unit/test_fiscal_stamping.py and
tests/integration/test_stamp_flow.py.
"""

from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status

from app.core.error_codes import ErrorCode
from app.core.errors import DomainError
from app.enums import AccessRight
from app.main import app
from app.services.pac import get_pac_provider
from tests.api.test_fiscal_documents import _auth, _client, _deny_privileges, _row
from tests.fakes.pac import ScriptedPac
from tests.problem import assert_problem

_SERVICE = 'app.services.fiscal_document_service'
_STAMPING = 'app.services.fiscal_stamping'
_URL = '/api/v1/fiscal-documents/9/stamp'


@pytest.fixture(autouse=True)
def _clear_overrides() -> Generator[None, None, None]:
    yield
    app.dependency_overrides.clear()


def _provider(provider: object | None = None) -> object | None:
    app.dependency_overrides[get_pac_provider] = lambda: provider
    return provider


@pytest.mark.asyncio
async def test_stamp_returns_the_issued_document() -> None:
    _auth()
    pac = _provider(ScriptedPac())
    with (
        patch(f'{_STAMPING}.stamp', AsyncMock()) as stamp,
        patch(f'{_SERVICE}.get_document', AsyncMock(return_value=_row())),
    ):
        async with await _client() as client:
            response = await client.post(_URL)

    assert response.status_code == status.HTTP_200_OK
    assert (response.json()['fiscal_document_id'], response.json()['status']) == (9, 'issued')
    assert stamp.await_args.args[1:] == (9, pac, 7)


@pytest.mark.asyncio
async def test_stamp_requires_authentication() -> None:
    async with await _client() as client:
        response = await client.post(_URL)

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_stamp_requires_update_on_fiscal_documents() -> None:
    _auth(administrator=False)
    _deny_privileges()
    _provider(ScriptedPac())
    with patch(f'{_STAMPING}.stamp', AsyncMock()) as stamp:
        async with await _client() as client:
            response = await client.post(_URL)

    body = assert_problem(response, 403, 'auth.forbidden')
    assert body['params']['required'] == [{'system_object': 23, 'right': int(AccessRight.UPDATE)}]
    stamp.assert_not_awaited()


@pytest.mark.asyncio
async def test_stamp_of_a_missing_document_is_404() -> None:
    _auth()
    _provider(ScriptedPac())
    with (
        patch(f'{_SERVICE}.get_document', AsyncMock(return_value=None)),
        patch(f'{_STAMPING}.stamp', AsyncMock()) as stamp,
    ):
        async with await _client() as client:
            response = await client.post(_URL)

    assert_problem(response, 404, 'resource.not_found')
    stamp.assert_not_awaited()


@pytest.mark.asyncio
async def test_stamp_without_a_provider_is_503() -> None:
    _auth()
    _provider(None)
    with patch(f'{_STAMPING}.stamp', AsyncMock()) as stamp:
        async with await _client() as client:
            response = await client.post(_URL)

    assert_problem(response, 503, 'pac.not_configured')
    stamp.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('error', 'expected_status', 'code', 'params'),
    [
        (
            DomainError(ErrorCode.FISCAL_DOCUMENT_NOT_SEALED, 'Fiscal document is not sealed'),
            409,
            'fiscal_document.not_sealed',
            {},
        ),
        (
            DomainError(ErrorCode.FISCAL_DOCUMENT_STAMPING, 'A stamp is in process'),
            409,
            'fiscal_document.stamping',
            {},
        ),
        (
            DomainError(
                ErrorCode.LOCK_BUSY, 'The fiscal document is busy', {'resource': 'fiscal_document'}
            ),
            409,
            'lock.busy',
            {'resource': 'fiscal_document'},
        ),
        (
            DomainError(ErrorCode.FISCAL_DOCUMENT_ISSUE_DATE_OUT_OF_RANGE, 'Out of range'),
            422,
            'fiscal_document.issue_date_out_of_range',
            {},
        ),
        (
            DomainError(
                ErrorCode.PAC_REJECTED,
                'The PAC rejected the document',
                {'pac_code': 'CFDI40102', 'pac_message': 'Sello inválido'},
            ),
            422,
            'pac.rejected',
            {'pac_code': 'CFDI40102', 'pac_message': 'Sello inválido'},
        ),
        (
            DomainError(ErrorCode.PAC_UNAVAILABLE, 'The PAC could not be reached'),
            503,
            'pac.unavailable',
            {},
        ),
        (
            DomainError(ErrorCode.PAC_TIMEOUT, 'Refresh the document', {'state': 'stamping'}),
            504,
            'pac.timeout',
            {'state': 'stamping'},
        ),
    ],
    ids=['not_sealed', 'stamping', 'busy', 'issue_date', 'rejected', 'unavailable', 'timeout'],
)
async def test_a_service_refusal_is_returned_as_its_problem(
    error: DomainError, expected_status: int, code: str, params: dict
) -> None:
    _auth()
    _provider(ScriptedPac())
    with (
        patch(f'{_STAMPING}.stamp', AsyncMock(side_effect=error)),
        patch(f'{_SERVICE}.get_document', AsyncMock(return_value=_row(status='sealed'))),
    ):
        async with await _client() as client:
            response = await client.post(_URL)

    body = assert_problem(response, expected_status, code)
    assert body.get('params', {}) == params
