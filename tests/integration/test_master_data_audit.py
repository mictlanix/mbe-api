"""Spec 021 US3: product and user changes leave one incidence each, against real tables.

The unit tests pin the wording and the mocked API tests pin that the route passes the acting
employee. Only a real session can say that the row is committed with the change, that
`initiate_recovery` now commits something it never wrote before, and that a refused create leaves
nothing behind.
"""

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import SourceType
from app.models.incidence import Incidence

PRODUCT = {'code': 'AUD-1', 'name': 'Audited', 'unit_of_measurement': 'H87'}


async def _rows(db: AsyncSession, source: SourceType) -> list[Incidence]:
    db.expire_all()
    query = select(Incidence).where(Incidence.source == int(source))
    query = query.order_by(Incidence.incidence_id)
    return list((await db.execute(query)).scalars())


def _shape(row: Incidence) -> tuple:
    return (row.instance_id, row.updater, row.comment, row.content)


async def test_a_product_is_audited_on_create_update_and_delete(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    created = await client.post('/api/v1/products', json=PRODUCT)
    assert created.status_code == 201, created.text
    pid = created.json()['product_id']

    updated = await client.put(f'/api/v1/products/{pid}', json={'name': 'Renamed', 'sku': 'S1'})
    assert updated.status_code == 200, updated.text

    deleted = await client.delete(f'/api/v1/products/{pid}')
    assert deleted.status_code == 204, deleted.text

    rows = await _rows(db, SourceType.PRODUCT)
    assert [_shape(r) for r in rows] == [
        (pid, 1, 'Product AUD-1 created', 'Audited'),
        (pid, 1, 'Product AUD-1 updated', 'Changed: name, sku'),
        (pid, 1, 'Product AUD-1 deleted', 'AUD-1 Renamed'),
    ]


async def test_a_refused_create_leaves_no_incidence(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    assert (await client.post('/api/v1/products', json=PRODUCT)).status_code == 201
    before = len(await _rows(db, SourceType.PRODUCT))

    duplicate = await client.post('/api/v1/products', json=PRODUCT)
    assert duplicate.status_code == 409, duplicate.text
    assert len(await _rows(db, SourceType.PRODUCT)) == before


async def test_user_actions_are_audited_by_the_affected_employee(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    profile = await client.post(
        '/api/v1/user-profiles',
        json={'name': 'Cashier', 'privileges': [{'system_object': 0, 'privileges': 2}]},
    )
    assert profile.status_code == 201, profile.text
    profile_id = profile.json()['user_profile_id']
    made = await client.post(
        '/api/v1/users',
        json={
            'user_id': 'audited',
            'password': 'Secret123',
            'email': 'audited@example.com',
            'employee_id': 1,
        },
    )
    assert made.status_code == 201, made.text

    edited = await client.put('/api/v1/users/audited', json={'email': 'other@example.com'})
    assert edited.status_code == 200, edited.text
    applied = await client.post(f'/api/v1/user-profiles/{profile_id}/apply/audited')
    assert applied.status_code == 200, applied.text
    recovered = await client.post('/api/v1/users/audited/recover-password')
    assert recovered.status_code == 200, recovered.text
    deleted = await client.delete('/api/v1/users/audited')
    assert deleted.status_code == 204, deleted.text

    rows = await _rows(db, SourceType.USER_SETTINGS)
    assert [_shape(r) for r in rows] == [
        (1, 1, 'User audited updated', 'audited updated; changed: email'),
        (1, 1, 'User audited profile applied', f'audited profile {profile_id} applied'),
        (1, 1, 'User audited recovery token issued', 'audited recovery token issued'),
        (1, 1, 'User audited deleted', 'audited deleted'),
    ]
    assert 'other@example.com' not in (rows[0].content or '')
