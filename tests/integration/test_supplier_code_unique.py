"""A supplier's code is unique, refused with 409 on create and update (#256)."""

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.supplier import Supplier


async def _supplier(client: AsyncClient, code: str) -> int:
    created = await client.post('/api/v1/suppliers', json={'code': code, 'name': code})
    assert created.status_code == 201, created.text
    return created.json()['supplier_id']


async def test_a_second_supplier_with_a_taken_code_is_refused(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    await _supplier(client, 'S-DUP')

    refused = await client.post('/api/v1/suppliers', json={'code': 'S-DUP', 'name': 'Otro'})

    assert refused.status_code == 409, refused.text
    assert refused.json()['detail'] == 'Supplier code already exists'
    count = select(func.count()).select_from(Supplier).where(Supplier.code == 'S-DUP')
    assert (await db.execute(count)).scalar_one() == 1


async def test_changing_a_code_to_another_suppliers_is_refused(
    client: AsyncClient, seeded: None
) -> None:
    await _supplier(client, 'S-TAKEN')
    supplier_id = await _supplier(client, 'S-MINE')

    refused = await client.put(f'/api/v1/suppliers/{supplier_id}', json={'code': 'S-TAKEN'})

    assert refused.status_code == 409, refused.text
    assert (await client.get(f'/api/v1/suppliers/{supplier_id}')).json()['code'] == 'S-MINE'


async def test_saving_a_supplier_with_its_own_code_is_not_a_conflict(
    client: AsyncClient, seeded: None
) -> None:
    supplier_id = await _supplier(client, 'S-SELF')

    saved = await client.put(f'/api/v1/suppliers/{supplier_id}', json={'code': 'S-SELF'})

    assert saved.status_code == 200, saved.text
