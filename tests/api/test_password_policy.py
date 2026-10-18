"""#241 — one password policy, enforced on every path that sets a password and published to clients.

The client localizes the error from `ctx.failed`, so the rule names are part of the contract, not
only the status code.
"""

from collections.abc import Iterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.core.deps import CurrentUser, get_signed_in_user, require_admin
from app.core.password_policy import failed_rules
from app.main import app


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


def _signed_in() -> None:
    caller = CurrentUser(
        user_id='admin', session_version=1, administrator=True, facility_id=None, employee_id=1
    )
    app.dependency_overrides[get_signed_in_user] = lambda: caller
    app.dependency_overrides[require_admin] = lambda: caller


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


def _failed(response_json: dict, field: str) -> list[str]:
    (error,) = response_json['detail']
    assert error['type'] == 'password_policy'
    assert error['loc'] == ['body', field]
    return error['ctx']['failed']


class TestTheRules:
    @pytest.mark.parametrize(
        ('password', 'failed'),
        [
            ('abc12345', ['require_upper']),
            ('Abc12345', []),
            ('ABC12345', ['require_lower']),
            ('Abcdefgh', ['require_digit']),
            ('Ab1', ['min_length']),
            ('', ['min_length', 'require_upper', 'require_lower', 'require_digit']),
        ],
    )
    def test_the_defaults(self, password: str, failed: list[str]) -> None:
        assert failed_rules(password) == failed

    def test_a_symbol_is_required_only_when_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, 'password_require_symbol', True)

        assert failed_rules('Abc12345') == ['require_symbol']
        assert failed_rules('Abc1234!') == []
        # A space is not a symbol: it would let "Abc 1234" pass a rule meant to add entropy.
        assert failed_rules('Abc 1234') == ['require_symbol']


@pytest.mark.asyncio
async def test_the_policy_is_published_without_signing_in() -> None:
    """Public, because `/auth/recover` is used before login."""
    async with _client() as client:
        response = await client.get('/api/v1/auth/password-policy')

    assert response.status_code == 200
    assert response.json() == {
        'min_length': 8,
        'require_upper': True,
        'require_lower': True,
        'require_digit': True,
        'require_symbol': False,
    }


@pytest.mark.asyncio
async def test_a_changed_setting_changes_enforcement_and_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, 'password_min_length', 12)

    async with _client() as client:
        published = await client.get('/api/v1/auth/password-policy')
        refused = await client.post(
            '/api/v1/auth/recover',
            json={'recovery_token': 'x', 'new_password': 'Abc12345'},
        )

    assert published.json()['min_length'] == 12
    assert refused.status_code == 422
    assert _failed(refused.json(), 'new_password') == ['min_length']


@pytest.mark.asyncio
async def test_recovery_enforces_the_policy() -> None:
    async with _client() as client:
        refused = await client.post(
            '/api/v1/auth/recover',
            json={'recovery_token': 'x', 'new_password': 'abc12345'},
        )
        # Past validation: refused for the token, which is what a strong password reaches.
        accepted = await client.post(
            '/api/v1/auth/recover',
            json={'recovery_token': 'x', 'new_password': 'Abc12345'},
        )

    assert refused.status_code == 422
    assert _failed(refused.json(), 'new_password') == ['require_upper']
    assert accepted.status_code == 400


@pytest.mark.asyncio
async def test_changing_a_password_enforces_the_policy() -> None:
    _signed_in()
    async with _client() as client:
        response = await client.post(
            '/api/v1/auth/change-password',
            json={'old_password': 'whatever', 'new_password': 'abc12345'},
        )

    assert response.status_code == 422
    assert _failed(response.json(), 'new_password') == ['require_upper']


@pytest.mark.asyncio
async def test_creating_a_user_enforces_the_policy() -> None:
    """Was `min_length=1`: an admin could issue a one-character password."""
    _signed_in()
    async with _client() as client:
        response = await client.post(
            '/api/v1/users',
            json={
                'user_id': 'newuser1',
                'password': 'x',
                'email': 'new@example.com',
                'employee_id': 7,
            },
        )

    assert response.status_code == 422
    assert _failed(response.json(), 'password') == [
        'min_length',
        'require_upper',
        'require_digit',
    ]
