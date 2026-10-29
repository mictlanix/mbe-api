"""Tests for the fiscal document email route (#50, spec 028).

The HTTP contract only: `fiscal_email` and the document load are patched. What the service does
is covered in its own tests.
"""

from collections.abc import Generator
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException, status
from httpx import AsyncClient

from app.main import app
from app.services import mail
from tests.api.test_fiscal_documents import _auth, _client, _deny_privileges

_SERVICE = 'app.services.fiscal_document_service'
_EMAIL = 'app.services.fiscal_email.email_document'
_URL = '/api/v1/fiscal-documents/9/email'
_SENT_AT = datetime(2026, 7, 30, 12, 0, 0)


@pytest.fixture(autouse=True)
def _clear_overrides() -> Generator[None, None, None]:
    yield
    app.dependency_overrides.clear()


def _mailer(mailer: object | None = ...) -> None:
    value = SimpleNamespace() if mailer is ... else mailer
    app.dependency_overrides[mail.get_mailer] = lambda: value


async def _post(client: AsyncClient, body: dict | None = None):
    return await client.post(_URL, json=body if body is not None else {'to': ['a@x.mx']})


@pytest.mark.asyncio
async def test_a_document_is_sent_to_the_deduplicated_addresses() -> None:
    _auth()
    _mailer()
    document = SimpleNamespace(fiscal_document_id=9)
    with (
        patch(f'{_SERVICE}.get_document', AsyncMock(return_value=document)),
        patch(_EMAIL, AsyncMock(return_value=_SENT_AT)) as send,
    ):
        async with await _client() as client:
            response = await _post(client, {'to': ['a@x.mx', 'b@y.mx', 'a@x.mx']})

    assert response.status_code == status.HTTP_200_OK, response.text
    body = response.json()
    assert body['recipients'] == ['a@x.mx', 'b@y.mx']
    assert body['sent_at'].startswith('2026-07-30T12:00:00')
    args = send.await_args
    assert args.args[1] is document
    assert args.args[2] == ['a@x.mx', 'b@y.mx']
    assert args.kwargs['mailer'] is app.dependency_overrides[mail.get_mailer]()
    assert args.kwargs['current'].user_id == 'tester'


@pytest.mark.asyncio
async def test_email_requires_authentication() -> None:
    _mailer()
    async with await _client() as client:
        response = await _post(client)

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_email_requires_the_read_privilege() -> None:
    _auth(administrator=False)
    _deny_privileges()
    _mailer()
    with patch(_EMAIL, AsyncMock()) as send:
        async with await _client() as client:
            response = await _post(client)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_missing_document_is_404() -> None:
    _auth()
    _mailer()
    with (
        patch(f'{_SERVICE}.get_document', AsyncMock(return_value=None)),
        patch(_EMAIL, AsyncMock()) as send,
    ):
        async with await _client() as client:
            response = await _post(client)

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()['detail'] == 'Fiscal document not found'
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_email_is_503_when_the_mailer_is_not_configured() -> None:
    _auth()
    _mailer(None)
    with (
        patch(f'{_SERVICE}.get_document', AsyncMock(return_value=SimpleNamespace())),
        patch(_EMAIL, AsyncMock()) as send,
    ):
        async with await _client() as client:
            response = await _post(client)

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response.json()['detail'] == 'Email is not configured'
    send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('code', 'detail'),
    [
        (status.HTTP_409_CONFLICT, 'Only a stamped CFDI 4.0 can be emailed'),
        (status.HTTP_502_BAD_GATEWAY, 'The email could not be sent: boom'),
    ],
)
async def test_the_services_refusal_passes_through(code: int, detail: str) -> None:
    _auth()
    _mailer()
    with (
        patch(f'{_SERVICE}.get_document', AsyncMock(return_value=SimpleNamespace())),
        patch(_EMAIL, AsyncMock(side_effect=HTTPException(code, detail))),
    ):
        async with await _client() as client:
            response = await _post(client)

    assert response.status_code == code
    assert response.json()['detail'] == detail


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'to',
    [[], [f'u{i}@x.mx' for i in range(11)], ['not-an-address']],
    ids=['empty', 'eleven', 'invalid'],
)
async def test_the_addresses_are_validated(to: list[str]) -> None:
    _auth()
    _mailer()
    with (
        patch(f'{_SERVICE}.get_document', AsyncMock(return_value=SimpleNamespace())),
        patch(_EMAIL, AsyncMock()) as send,
    ):
        async with await _client() as client:
            response = await _post(client, {'to': to})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    send.assert_not_awaited()
