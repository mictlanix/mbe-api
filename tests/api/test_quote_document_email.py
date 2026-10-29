"""The quote document and quote email routes (spec 024).

The HTTP contract only: the quote, its print context and the renderer are patched, so these assert
status codes, headers and the published schema. What the document says is covered by real renders
in tests/integration/test_quote_document_render.py.
"""

from collections.abc import Generator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import status
from httpx import ASGITransport, AsyncClient

from app.core.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.enums import SourceType
from app.main import app
from app.models.core import Employee, Facility
from app.services import mail
from app.services.mail import MailError
from tests.problem import assert_problem

_PDF = b'%PDF-1.7 fake'


@pytest.fixture(autouse=True)
def _clear_overrides() -> Generator[None, None, None]:
    yield
    app.dependency_overrides.clear()


def _auth(*, administrator: bool = True) -> None:
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id='tester',
        session_version=1,
        administrator=administrator,
        facility_id=1,
        employee_id=7,
    )

    async def _noop_db():
        yield None

    app.dependency_overrides[get_db] = _noop_db


def _deny_privileges() -> None:
    """`require_privilege` reads `scalar_one_or_none()`; answering None denies every right."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session = SimpleNamespace(execute=AsyncMock(return_value=result))

    async def _db():
        yield session

    app.dependency_overrides[get_db] = _db


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


# ── Document ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_document_is_the_pdf_inline() -> None:
    _auth()
    quote = SimpleNamespace(sales_quote_id=7)
    context = ('sales_quote.html', {'header': {}})
    with (
        patch('app.services.sales_quote_service.get_quote', AsyncMock(return_value=quote)),
        patch('app.services.sales_quote_service.attach_derived', AsyncMock(return_value=quote)),
        patch(
            'app.services.print_contexts.sales_quote_context', AsyncMock(return_value=context)
        ) as build,
        patch('app.rendering.render_pdf', AsyncMock(return_value=_PDF)) as render,
    ):
        async with _client() as client:
            response = await client.get('/api/v1/sales-quotes/7/document')

    assert response.status_code == status.HTTP_200_OK
    assert response.headers['content-type'] == 'application/pdf'
    assert response.headers['content-disposition'] == 'inline; filename="cotizacion-00000007.pdf"'
    assert response.content == _PDF
    assert build.await_args.args[1] is quote
    render.assert_awaited_once_with(*context)


@pytest.mark.asyncio
async def test_the_document_of_a_missing_quote_is_404() -> None:
    _auth()
    with patch('app.services.sales_quote_service.get_quote', AsyncMock(return_value=None)):
        async with _client() as client:
            response = await client.get('/api/v1/sales-quotes/7/document')

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()['detail'] == 'Sales quote not found'


@pytest.mark.asyncio
async def test_the_document_requires_authentication() -> None:
    async with _client() as client:
        response = await client.get('/api/v1/sales-quotes/7/document')

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_the_document_requires_the_sales_quotes_read_privilege() -> None:
    _auth(administrator=False)
    _deny_privileges()
    async with _client() as client:
        response = await client.get('/api/v1/sales-quotes/7/document')

    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_the_document_publishes_a_binary_pdf_response() -> None:
    """mbe-ui's generated client reads bytes only when this is the sole 200 content (#230)."""
    responses = app.openapi()['paths']['/api/v1/sales-quotes/{sales_quote_id}/document']['get'][
        'responses'
    ]

    assert responses['200']['content'] == {
        'application/pdf': {'schema': {'type': 'string', 'format': 'binary'}}
    }


# ── Email ─────────────────────────────────────────────────────────────────────


class RecordingMailer:
    """The test double for `mail.get_mailer`: keeps messages instead of sending them."""

    def __init__(self, error: Exception | None = None) -> None:
        self.sent: list = []
        self.error = error

    async def send(self, message) -> None:  # noqa: ANN001
        if self.error is not None:
            raise self.error
        self.sent.append(message)


def _quote(*, completed: bool = True, cancelled: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        sales_quote_id=7, completed=completed, cancelled=cancelled, facility=1, salesperson=3
    )


def _email_db() -> SimpleNamespace:
    """`get` answers the facility and the salesperson by model; `commit` is recorded."""
    rows = {
        Facility: SimpleNamespace(name='Matriz'),
        Employee: SimpleNamespace(first_name='Ana', last_name='Ruiz'),
    }
    return SimpleNamespace(
        get=AsyncMock(side_effect=lambda model, _id: rows[model]), commit=AsyncMock()
    )


def _send_setup(mailer: RecordingMailer | None, session: SimpleNamespace) -> None:
    _auth()

    async def _db():
        yield session

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[mail.get_mailer] = lambda: mailer


async def _post_email(quote: SimpleNamespace | None, body: dict) -> tuple:
    context = ('sales_quote.html', {'header': {}})
    with (
        patch('app.services.sales_quote_service.get_quote', AsyncMock(return_value=quote)),
        patch('app.services.sales_quote_service.attach_derived', AsyncMock(return_value=quote)),
        patch('app.services.print_contexts.sales_quote_context', AsyncMock(return_value=context)),
        patch('app.rendering.render_pdf', AsyncMock(return_value=_PDF)),
        patch('app.services.incidences.record') as record,
    ):
        async with _client() as client:
            response = await client.post('/api/v1/sales-quotes/7/email', json=body)
    return response, record


@pytest.mark.asyncio
async def test_a_confirmed_quote_is_sent_and_recorded_once() -> None:
    mailer, session = RecordingMailer(), _email_db()
    _send_setup(mailer, session)

    response, record = await _post_email(_quote(), {'to': ['a@x.mx', 'b@y.mx']})

    assert response.status_code == status.HTTP_200_OK, response.text
    assert response.json()['recipients'] == ['a@x.mx', 'b@y.mx']
    assert response.json()['sent_at']
    [message] = mailer.sent
    assert list(message.to) == ['a@x.mx', 'b@y.mx']
    assert message.subject == 'Matriz Cotización 7'
    assert message.attachments[0].data == _PDF
    record.assert_called_once()
    kwargs = record.call_args.kwargs
    assert kwargs['source'] == SourceType.SALES_QUOTE
    assert kwargs['instance_id'] == 7
    assert kwargs['updater'] == 7
    assert kwargs['reason'] == 'Quote emailed'
    assert kwargs['context'] == 'To: a@x.mx, b@y.mx'
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'quote', [_quote(completed=False), _quote(cancelled=True)], ids=['draft', 'cancelled']
)
async def test_only_a_confirmed_quote_is_sent(quote: SimpleNamespace) -> None:
    mailer = RecordingMailer()
    _send_setup(mailer, _email_db())

    response, record = await _post_email(quote, {'to': ['a@x.mx']})

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()['detail'] == 'Only a confirmed quote can be emailed'
    assert mailer.sent == []
    record.assert_not_called()


@pytest.mark.asyncio
async def test_an_invalid_address_is_refused_before_sending() -> None:
    mailer = RecordingMailer()
    _send_setup(mailer, _email_db())

    response, _ = await _post_email(_quote(), {'to': ['not-an-address']})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert mailer.sent == []


@pytest.mark.asyncio
async def test_a_refused_send_is_reported_and_not_recorded() -> None:
    session = _email_db()
    _send_setup(RecordingMailer(error=MailError('550 mailbox unavailable')), session)

    response, record = await _post_email(_quote(), {'to': ['a@x.mx']})

    body = assert_problem(response, status.HTTP_502_BAD_GATEWAY, 'email.send_failed')
    assert 'params' not in body or '550' not in str(body['params'])
    assert response.json()['detail'].startswith('The email could not be sent')
    assert '550' in response.json()['detail']
    record.assert_not_called()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_without_mail_settings_the_route_is_unavailable() -> None:
    _send_setup(None, _email_db())

    response, record = await _post_email(_quote(), {'to': ['a@x.mx']})

    assert_problem(response, status.HTTP_503_SERVICE_UNAVAILABLE, 'email.not_configured')
    assert response.json()['detail'] == 'Email is not configured'
    record.assert_not_called()


@pytest.mark.asyncio
async def test_emailing_a_missing_quote_is_404() -> None:
    _send_setup(RecordingMailer(), _email_db())

    response, _ = await _post_email(None, {'to': ['a@x.mx']})

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()['detail'] == 'Sales quote not found'


@pytest.mark.asyncio
async def test_emailing_requires_the_sales_quotes_read_privilege() -> None:
    _auth(administrator=False)
    _deny_privileges()
    async with _client() as client:
        response = await client.post('/api/v1/sales-quotes/7/email', json={'to': ['a@x.mx']})

    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_emailing_requires_authentication() -> None:
    async with _client() as client:
        response = await client.post('/api/v1/sales-quotes/7/email', json={'to': ['a@x.mx']})

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
