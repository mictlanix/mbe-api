"""A supplier's sub-panels, written and read back through the API against a real database (#13).

Junction column names, foreign keys and the delete guard are what a mocked session cannot check.
"""

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.supplier import Supplier


async def _bank_account(client: AsyncClient) -> int:
    created = await client.post(
        '/api/v1/bank-accounts', json={'bank_name': 'BBVA', 'account_number': '0123456789'}
    )
    assert created.status_code == 201, created.text
    return created.json()['bank_account_id']




async def test_a_supplier_is_created_with_its_links_and_read_back(
    client: AsyncClient, seeded: None
) -> None:
    account = await _bank_account(client)

    created = await client.post(
        '/api/v1/suppliers',
        json={
            'code': 'S-LINK',
            'name': 'Con Enlaces',
            'addresses': [1],
            'contacts': [1],
            'bank_accounts': [account],
        },
    )
    assert created.status_code == 201, created.text
    assert [a['address_id'] for a in created.json()['addresses']] == [1]

    read = await client.get(f"/api/v1/suppliers/{created.json()['supplier_id']}")
    assert read.status_code == 200, read.text
    body = read.json()
    assert [c['contact_id'] for c in body['contacts']] == [1]
    assert [b['bank_account_id'] for b in body['bank_accounts']] == [account]
    assert body['agreements'] == []


async def test_replacing_the_contacts_leaves_the_address_links_alone(
    client: AsyncClient, seeded: None
) -> None:
    created = await client.post(
        '/api/v1/suppliers',
        json={'code': 'S-REP', 'name': 'Reemplazo', 'addresses': [1], 'contacts': [1]},
    )
    supplier_id = created.json()['supplier_id']

    updated = await client.put(f'/api/v1/suppliers/{supplier_id}', json={'contacts': []})

    assert updated.status_code == 200, updated.text
    assert updated.json()['contacts'] == []
    assert [a['address_id'] for a in updated.json()['addresses']] == [1]


async def test_an_unknown_address_is_refused_and_the_links_are_unchanged(
    client: AsyncClient, seeded: None
) -> None:
    created = await client.post(
        '/api/v1/suppliers', json={'code': 'S-BAD', 'name': 'Mal', 'addresses': [1]}
    )
    supplier_id = created.json()['supplier_id']

    refused = await client.put(f'/api/v1/suppliers/{supplier_id}', json={'addresses': [999999]})

    assert refused.status_code == 409, refused.text
    read = await client.get(f'/api/v1/suppliers/{supplier_id}')
    assert [a['address_id'] for a in read.json()['addresses']] == [1]


async def test_a_supplier_is_deletable_only_once_its_links_and_agreements_are_cleared(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    created = await client.post(
        '/api/v1/suppliers', json={'code': 'S-DEL', 'name': 'Borrar', 'addresses': [1]}
    )
    supplier_id = created.json()['supplier_id']
    agreement = await client.post(
        f'/api/v1/suppliers/{supplier_id}/agreements',
        json={'start': '2026-01-01', 'end': '2026-12-31'},
    )
    assert agreement.status_code == 201, agreement.text

    assert (await client.delete(f'/api/v1/suppliers/{supplier_id}')).status_code == 409

    await client.put(f'/api/v1/suppliers/{supplier_id}', json={'addresses': []})
    assert (await client.delete(f'/api/v1/suppliers/{supplier_id}')).status_code == 409

    agreement_id = agreement.json()['supplier_agreement_id']
    cleared = await client.delete(f'/api/v1/suppliers/{supplier_id}/agreements/{agreement_id}')
    assert cleared.status_code == 204

    assert (await client.delete(f'/api/v1/suppliers/{supplier_id}')).status_code == 204
    found = (await db.execute(select(Supplier).where(Supplier.code == 'S-DEL'))).scalars().all()
    assert not found


async def test_an_agreement_of_another_supplier_is_not_found(
    client: AsyncClient, seeded: None
) -> None:
    first = (
        await client.post('/api/v1/suppliers', json={'code': 'S-A', 'name': 'A'})
    ).json()['supplier_id']
    second = (
        await client.post('/api/v1/suppliers', json={'code': 'S-B', 'name': 'B'})
    ).json()['supplier_id']
    agreement = await client.post(
        f'/api/v1/suppliers/{first}/agreements', json={'start': '2026-01-01', 'end': '2026-01-31'}
    )
    agreement_id = agreement.json()['supplier_agreement_id']

    path = f'/api/v1/suppliers/{second}/agreements/{agreement_id}'

    assert (await client.put(path, json={'comment': 'x'})).status_code == 404
    assert (await client.delete(path)).status_code == 404


async def test_a_bank_account_is_not_deletable_while_it_is_linked(
    client: AsyncClient, seeded: None
) -> None:
    account = await _bank_account(client)
    created = await client.post(
        '/api/v1/suppliers', json={'code': 'S-BNK', 'name': 'Banco', 'bank_accounts': [account]}
    )
    supplier_id = created.json()['supplier_id']

    assert (await client.delete(f'/api/v1/bank-accounts/{account}')).status_code == 409

    await client.put(f'/api/v1/suppliers/{supplier_id}', json={'bank_accounts': []})
    assert (await client.delete(f'/api/v1/bank-accounts/{account}')).status_code == 204
