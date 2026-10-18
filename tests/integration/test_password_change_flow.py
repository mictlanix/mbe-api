"""#240 — an administrator-issued password has to be replaced before the account can do anything.

Driven through a real login and real tokens: the `client` fixture overrides `get_current_user` with
an administrator, which is what issues the password here, and is lifted for the requests the new
user makes so that the flag check actually runs.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from httpx import AsyncClient

from app.core.deps import get_current_user
from app.main import app as fastapi_app


@contextmanager
def _as_the_token_holder() -> Iterator[None]:
    admin = fastapi_app.dependency_overrides.pop(get_current_user)
    try:
        yield
    finally:
        fastapi_app.dependency_overrides[get_current_user] = admin


async def _login(client: AsyncClient, password: str) -> tuple[dict, dict]:
    response = await client.post(
        '/api/v1/auth/login', data={'username': 'newbie', 'password': password}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    return body, {'Authorization': f'Bearer {body["access_token"]}'}


async def _me(client: AsyncClient, headers: dict) -> dict:
    response = await client.get('/api/v1/auth/me', headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def test_a_new_user_changes_the_issued_password_before_anything_else(
    client: AsyncClient, seeded: None
) -> None:
    created = await client.post(
        '/api/v1/users',
        json={
            'user_id': 'newbie',
            'password': 'Issued123',
            'email': 'newbie@example.com',
            'employee_id': 1,
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()['must_change_password'] is True

    token, headers = await _login(client, 'Issued123')
    # On the token response too, so the client needs no second request to know.
    assert token['must_change_password'] is True
    assert (await _me(client, headers))['must_change_password'] is True

    with _as_the_token_holder():
        refused = await client.get('/api/v1/sales-orders', headers=headers)
        assert refused.status_code == 403
        assert refused.json() == {'detail': 'password_change_required'}

        changed = await client.post(
            '/api/v1/auth/change-password',
            json={'old_password': 'Issued123', 'new_password': 'Chosen456'},
            headers=headers,
        )
        assert changed.status_code == 204, changed.text

        assert (await _me(client, headers))['must_change_password'] is False
        # Past the gate: whatever answers now is about privileges, not the password.
        after = await client.get('/api/v1/sales-orders', headers=headers)
        assert after.json() != {'detail': 'password_change_required'}

    token, _ = await _login(client, 'Chosen456')
    assert token['must_change_password'] is False


async def test_an_administrator_can_force_and_lift_it(client: AsyncClient, seeded: None) -> None:
    await client.post(
        '/api/v1/users',
        json={
            'user_id': 'newbie',
            'password': 'Issued123',
            'email': 'newbie@example.com',
            'employee_id': 1,
        },
    )

    lifted = await client.put('/api/v1/users/newbie', json={'must_change_password': False})
    assert lifted.status_code == 200, lifted.text
    assert lifted.json()['must_change_password'] is False

    forced = await client.put('/api/v1/users/newbie', json={'must_change_password': True})
    assert forced.json()['must_change_password'] is True
    listed = await client.get('/api/v1/users')
    row = next(u for u in listed.json()['items'] if u['user_id'] == 'newbie')
    assert row['must_change_password'] is True
