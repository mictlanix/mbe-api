"""The /accounts-receivable routes (spec 033, US2): the HTTP contract, with the services patched.

Figures against a real database are in tests/integration/test_accounts_receivable_flow.py.
"""

from collections.abc import Generator
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, status
from httpx import ASGITransport, AsyncClient

from app.core.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.enums import AccessRight, SystemObject
from app.main import app
from app.services import mail

BASE = '/api/v1/accounts-receivable'
ROUTE = 'app.api.v1.endpoints.accounts_receivable'
WHEN = datetime(2026, 10, 27, 12, 0)
ZERO = Decimal('0.00')


@pytest.fixture(autouse=True)
def _clear_overrides() -> Generator[None, None, None]:
    yield
    app.dependency_overrides.clear()


def _auth(*, administrator: bool = False, rights: int | None = AccessRight.READ) -> None:
    """`rights` is the mask a non-administrator holds on 18; None holds nothing."""
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id='tester',
        session_version=1,
        administrator=administrator,
        facility_id=1,
        employee_id=7,
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = (
        None if rights is None else SimpleNamespace(privileges=rights)
    )
    session = SimpleNamespace(execute=AsyncMock(return_value=result))

    async def _db():
        yield session

    app.dependency_overrides[get_db] = _db


def _holds(*objects: SystemObject) -> AsyncMock:
    """`has_any_privilege` answering for the objects the caller holds read on."""

    async def answer(db, user_id, grants):  # noqa: ANN001, ANN202
        return any(obj in objects for obj, _ in grants)

    return AsyncMock(side_effect=answer)


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


def _summary(**overrides: object) -> dict:
    base = dict(
        party=11202, name='Cliente', sales_agent=31, sales_agent_name='Ana López',
        owed=Decimal('40000.00'), overdue=Decimal('12000.00'),
        buckets={'current': Decimal('28000.00'), '1-30': Decimal('12000.00'), '31-60': ZERO,
                 '61-90': ZERO, '90+': ZERO},
        oldest=WHEN, credit=Decimal('500.00'), net=Decimal('39500.00'),
        credit_limit=Decimal('50000'), credit_days=30, owed_company=Decimal('40000.00'),
        utilization=Decimal('0.8000'), available_credit=Decimal('10000'),
    )  # fmt: skip
    base.update(overrides)
    return base


def _listing(**overrides: object) -> dict:
    base = dict(
        items=[_summary()],
        total=1,
        totals=dict(owed=Decimal('40000.00'), overdue=Decimal('12000.00'),
                    buckets=_summary()['buckets']),
    )  # fmt: skip
    base.update(overrides)
    return base


def _document() -> dict:
    return dict(
        id=912345, serial=4410, date=WHEN, due_date=WHEN, days_past_due=12, bucket='1-30',
        facility=1, facility_code='F1', salesperson=77, linked=[{'id': 30111, 'label': 'A1234'}],
        currency=0, exchange_rate=Decimal('1'), total=Decimal('15000.00'), deductions=ZERO,
        applied=Decimal('3000.00'), balance=Decimal('12000.00'),
    )  # fmt: skip


def _payment() -> SimpleNamespace:
    return SimpleNamespace(
        customer_payment_id=551, serial=88, date=WHEN, method=3, currency=0,
        amount=Decimal('500.00'), unapplied=Decimal('500.00'),
    )  # fmt: skip


class TestTheSummary:
    async def test_lists_the_rows_with_scope_and_totals(self) -> None:
        _auth()
        with (
            patch('app.core.deps.has_any_privilege', _holds()),
            patch(f'{ROUTE}.accounts_service.summary', AsyncMock(return_value=_listing())),
        ):
            async with _client() as client:
                response = await client.get(BASE)

        assert response.status_code == 200, response.text
        body = response.json()
        row = body['items'][0]
        assert (row['party'], row['sales_agent_name'], row['owed']) == (
            11202,
            'Ana López',
            '40000.00',
        )
        assert row['buckets']['1-30'] == '12000.00'
        assert row['utilization'] == '0.8000'
        assert body['total'] == 1
        assert body['totals']['owed'] == '40000.00'
        assert body['scope'] == {'facility': 1, 'all_facilities': False}

    async def test_without_a_login_is_401(self) -> None:
        async with _client() as client:
            response = await client.get(BASE)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    async def test_without_read_on_18_is_403(self) -> None:
        _auth(rights=None)
        async with _client() as client:
            response = await client.get(BASE)

        assert response.status_code == status.HTTP_403_FORBIDDEN

    async def test_a_caller_without_101_sees_their_own_facility(self) -> None:
        _auth()
        summary = AsyncMock(return_value=_listing())
        with (
            patch('app.core.deps.has_any_privilege', _holds()),
            patch(f'{ROUTE}.accounts_service.summary', summary),
        ):
            async with _client() as client:
                await client.get(BASE)
                refused = await client.get(BASE, params={'facility': 2})
                same = await client.get(BASE, params={'facility': 1})

        assert summary.await_args_list[0].kwargs['facility'] == 1
        assert refused.status_code == status.HTTP_403_FORBIDDEN
        assert same.status_code == 200

    @pytest.mark.parametrize('administrator', [True, False], ids=['administrator', 'holds-101'])
    async def test_101_or_an_administrator_sees_every_facility(self, administrator: bool) -> None:
        _auth(administrator=administrator)
        summary = AsyncMock(return_value=_listing())
        with (
            patch(
                'app.core.deps.has_any_privilege',
                _holds(SystemObject.SEARCH_CREDITS_FROM_ALL_STORES),
            ),
            patch(f'{ROUTE}.accounts_service.summary', summary),
        ):
            async with _client() as client:
                every = await client.get(BASE)
                narrowed = await client.get(BASE, params={'facility': 2})

        assert summary.await_args_list[0].kwargs['facility'] is None
        assert every.json()['scope'] == {'facility': None, 'all_facilities': True}
        assert summary.await_args_list[1].kwargs['facility'] == 2
        assert narrowed.json()['scope'] == {'facility': 2, 'all_facilities': False}

    async def test_filters_sort_and_paging_reach_the_service(self) -> None:
        _auth()
        summary = AsyncMock(return_value=_listing())
        with (
            patch('app.core.deps.has_any_privilege', _holds()),
            patch(f'{ROUTE}.accounts_service.summary', summary),
        ):
            async with _client() as client:
                await client.get(
                    BASE,
                    params={'party': 5, 'overdue': 'true', 'sales_agent': 31, 'sort': 'oldest',
                            'skip': 50, 'limit': 200},
                )  # fmt: skip
                too_many = await client.get(BASE, params={'limit': 201})
                unknown_sort = await client.get(BASE, params={'sort': 'name'})

        kwargs = summary.await_args.kwargs
        assert (kwargs['party'], kwargs['overdue'], kwargs['sales_agent']) == (5, True, 31)
        assert (kwargs['sort'], kwargs['skip'], kwargs['limit']) == ('oldest', 50, 200)
        assert too_many.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
        assert unknown_sort.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


class TestTheDrillDown:
    async def test_the_documents_and_the_payments_that_can_be_applied(self) -> None:
        _auth()
        detail = AsyncMock(return_value={'summary': _summary(), 'documents': [_document()]})
        with (
            patch('app.core.deps.has_any_privilege', _holds()),
            patch(f'{ROUTE}.accounts_service.detail', detail),
            patch(
                f'{ROUTE}.customer_payment_service.payments_with_credit',
                AsyncMock(return_value=[_payment()]),
            ),
        ):
            async with _client() as client:
                response = await client.get(f'{BASE}/11202')

        assert response.status_code == 200, response.text
        body = response.json()
        assert body['summary']['party'] == 11202
        document = body['documents'][0]
        assert (document['serial'], document['facility_code'], document['balance']) == (
            4410, 'F1', '12000.00',
        )  # fmt: skip
        assert document['linked'] == [{'id': 30111, 'label': 'A1234'}]
        assert body['payments'] == [
            {'id': 551, 'serial': 88, 'date': '2026-10-27T12:00:00', 'method': 3, 'currency': 0,
             'amount': '500.00', 'unapplied': '500.00'}
        ]  # fmt: skip
        assert detail.await_args.kwargs['facility'] == 1

    async def test_an_unknown_customer_is_404(self) -> None:
        _auth()
        missing = HTTPException(status_code=404, detail='Customer not found')
        with (
            patch('app.core.deps.has_any_privilege', _holds()),
            patch(f'{ROUTE}.accounts_service.detail', AsyncMock(side_effect=missing)),
        ):
            async with _client() as client:
                response = await client.get(f'{BASE}/999')

        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize(
        ('administrator', 'held', 'facility'),
        [
            (False, (), 1),
            (False, (SystemObject.SEARCH_CREDITS_FROM_ALL_STORES,), 1),
            (False, (SystemObject.PAYMENTS_EDITOR,), None),
            (True, (), None),
        ],
        ids=['scoped', 'holds-101-only', 'payments-editor', 'administrator'],
    )
    async def test_other_facilities_payments_follow_the_payments_rule(
        self, administrator: bool, held: tuple, facility: int | None
    ) -> None:
        """#271: another facility's payments are PaymentsEditor (100) territory, 101 or not."""
        _auth(administrator=administrator)
        payments = AsyncMock(return_value=[])
        with (
            patch('app.core.deps.has_any_privilege', _holds(*held)),
            patch(
                f'{ROUTE}.accounts_service.detail',
                AsyncMock(return_value={'summary': _summary(), 'documents': []}),
            ),
            patch(f'{ROUTE}.customer_payment_service.payments_with_credit', payments),
        ):
            async with _client() as client:
                await client.get(f'{BASE}/11202')

        assert payments.await_args.kwargs['facility'] == facility


class TestTheStatement:
    """FR-023, FR-025: a statement goes to the customer, so it covers every facility, and read on
    18 is enough to print or email it."""

    async def test_prints_the_pdf_for_a_scoped_caller(self) -> None:
        _auth()
        context = AsyncMock(return_value=('account_statement.html', {}))
        with (
            patch('app.core.deps.has_any_privilege', _holds()),
            patch(f'{ROUTE}.print_contexts.account_statement_context', context),
            patch(f'{ROUTE}.rendering.render_pdf', AsyncMock(return_value=b'%PDF-1.7')),
        ):
            async with _client() as client:
                response = await client.get(f'{BASE}/21/statement')

        assert response.status_code == 200, response.text
        assert response.headers['content-type'] == 'application/pdf'
        assert 'estado-de-cuenta-21.pdf' in response.headers['content-disposition']
        side, party = context.await_args.args[1:3]
        assert (side.party_label, party) == ('Customer', 21)

    async def test_without_read_on_18_is_403(self) -> None:
        _auth(rights=None)
        async with _client() as client:
            printed = await client.get(f'{BASE}/21/statement')
            emailed = await client.post(f'{BASE}/21/statement/email', json={'to': ['a@x.mx']})

        assert printed.status_code == emailed.status_code == status.HTTP_403_FORBIDDEN

    async def test_an_unknown_customer_is_404(self) -> None:
        _auth()
        missing = HTTPException(status_code=404, detail='Customer not found')
        with patch(
            f'{ROUTE}.print_contexts.account_statement_context', AsyncMock(side_effect=missing)
        ):
            async with _client() as client:
                response = await client.get(f'{BASE}/999/statement')

        assert response.status_code == status.HTTP_404_NOT_FOUND

    async def test_email_without_a_mailer_is_503_and_sends_nothing(self) -> None:
        _auth()
        app.dependency_overrides[mail.get_mailer] = lambda: None
        send = AsyncMock()
        with patch(f'{ROUTE}.account_email.email_statement', send):
            async with _client() as client:
                response = await client.post(f'{BASE}/21/statement/email', json={'to': ['a@x.mx']})

        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        send.assert_not_awaited()

    async def test_email_answers_when_and_to_whom(self) -> None:
        _auth()
        app.dependency_overrides[mail.get_mailer] = lambda: object()
        send = AsyncMock(return_value=(WHEN, ['ana@cliente.mx', 'a@x.mx']))
        with patch(f'{ROUTE}.account_email.email_statement', send):
            async with _client() as client:
                response = await client.post(
                    f'{BASE}/21/statement/email', json={'contacts': [4], 'to': ['a@x.mx']}
                )

        assert response.status_code == 200, response.text
        assert response.json() == {
            'sent_at': '2026-10-27T12:00:00',
            'to': ['ana@cliente.mx', 'a@x.mx'],
        }
        assert send.await_args.kwargs['contact_ids'] == [4]
        assert send.await_args.kwargs['to'] == ['a@x.mx']

    async def test_email_with_no_recipient_is_422(self) -> None:
        _auth()
        app.dependency_overrides[mail.get_mailer] = lambda: object()
        async with _client() as client:
            response = await client.post(f'{BASE}/21/statement/email', json={})

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
