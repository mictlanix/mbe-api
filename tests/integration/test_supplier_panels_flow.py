"""A supplier's sub-panels follow the caller's own read privileges (spec 035, US1).

Bank accounts answer to BankAccounts (13), contacts to Contacts (12), addresses to Addresses (11)
and agreements to Suppliers (3); administrators read all four. A link list for a panel the caller
cannot read is treated as not sent, so a write never changes what the caller could not see.
"""

from datetime import date

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_current_user
from app.enums import AccessRight, SystemObject
from app.main import app as fastapi_app
from app.models.product import Product
from app.models.supplier import SupplierAgreement, supplier_bank_account
from app.models.user import AccessPrivilege

READ = int(AccessRight.READ)
ALL = int(AccessRight.CREATE | AccessRight.READ | AccessRight.UPDATE | AccessRight.DELETE)
PANELS = ('addresses', 'contacts', 'bank_accounts', 'agreements')
CLERK = CurrentUser(
    user_id='tester', session_version=1, administrator=False, facility_id=1, employee_id=1
)


async def _grant(db: AsyncSession, *grants: tuple[SystemObject, int]) -> None:
    for obj, mask in grants:
        db.add(AccessPrivilege(user_id='tester', system_object=int(obj), privileges=mask))
    await db.commit()


async def _bank_account(client: AsyncClient, number: str) -> int:
    created = await client.post(
        '/api/v1/bank-accounts', json={'bank_name': 'BBVA', 'account_number': number}
    )
    assert created.status_code == 201, created.text
    return created.json()['bank_account_id']


@pytest.fixture
async def supplier(client: AsyncClient, db: AsyncSession, seeded: None) -> dict[str, int]:
    """A supplier with one of each panel, written as an administrator."""
    account = await _bank_account(client, '0123456789')
    created = await client.post(
        '/api/v1/suppliers',
        json={
            'code': 'S-PANELS',
            'name': 'Con Paneles',
            'addresses': [1],
            'contacts': [1],
            'bank_accounts': [account],
        },
    )
    assert created.status_code == 201, created.text
    supplier_id = created.json()['supplier_id']
    db.add(SupplierAgreement(supplier=supplier_id, start=date(2026, 1, 1), end=date(2026, 12, 31)))
    await db.commit()
    return {
        'id': supplier_id,
        'account': account,
        'other_account': await _bank_account(client, '9876543210'),
    }


def _as_clerk() -> None:
    fastapi_app.dependency_overrides[get_current_user] = lambda: CLERK


async def _bank_links(db: AsyncSession, supplier_id: int) -> list[int]:
    db.expire_all()
    rows = await db.execute(
        select(supplier_bank_account.c['bank_account']).where(
            supplier_bank_account.c['supplier'] == supplier_id
        )
    )
    return sorted(rows.scalars().all())


def _filled(body: dict) -> set[str]:
    return {panel for panel in PANELS if body[panel]}


class TestRead:
    async def test_a_products_reader_gets_the_supplier_and_no_panels(
        self, client: AsyncClient, db: AsyncSession, supplier: dict
    ) -> None:
        """SC-001."""
        await _grant(db, (SystemObject.PRODUCTS, READ))
        _as_clerk()

        r = await client.get(f'/api/v1/suppliers/{supplier["id"]}')

        assert r.status_code == 200, r.text
        assert (r.json()['code'], r.json()['name']) == ('S-PANELS', 'Con Paneles')
        assert all(r.json()[panel] == [] for panel in PANELS)

    async def test_each_panel_follows_its_own_privilege(
        self, client: AsyncClient, db: AsyncSession, supplier: dict
    ) -> None:
        await _grant(
            db,
            (SystemObject.SUPPLIERS, READ),
            (SystemObject.BANK_ACCOUNTS, READ),
            (SystemObject.ADDRESSES, READ),
        )
        _as_clerk()

        r = await client.get(f'/api/v1/suppliers/{supplier["id"]}')

        assert r.status_code == 200, r.text
        assert _filled(r.json()) == {'bank_accounts', 'addresses', 'agreements'}

    async def test_an_administrator_reads_all_four(
        self, client: AsyncClient, supplier: dict
    ) -> None:
        r = await client.get(f'/api/v1/suppliers/{supplier["id"]}')

        assert r.status_code == 200, r.text
        assert _filled(r.json()) == set(PANELS)

    async def test_the_list_and_a_products_supplier_carry_no_panels(
        self, client: AsyncClient, db: AsyncSession, supplier: dict
    ) -> None:
        """FR-004: the response shape is unchanged."""
        product = (await db.execute(select(Product).limit(1))).scalars().one()
        product.supplier = supplier['id']
        await db.commit()

        listed = await client.get('/api/v1/suppliers')
        embedded = await client.get(f'/api/v1/products/{product.product_id}')

        assert listed.status_code == 200, listed.text
        assert embedded.status_code == 200, embedded.text
        assert embedded.json()['supplier']['code'] == 'S-PANELS'
        for row in (*listed.json()['items'], embedded.json()['supplier']):
            assert not set(PANELS) & set(row)


class TestWrite:
    async def test_an_update_ignores_a_list_for_a_panel_the_caller_cannot_read(
        self, client: AsyncClient, db: AsyncSession, supplier: dict
    ) -> None:
        await _grant(db, (SystemObject.SUPPLIERS, ALL))
        _as_clerk()
        url = f'/api/v1/suppliers/{supplier["id"]}'

        cleared = await client.put(url, json={'bank_accounts': []})
        replaced = await client.put(url, json={'bank_accounts': [supplier['other_account']]})

        assert cleared.status_code == 200, cleared.text
        assert replaced.status_code == 200, replaced.text
        assert await _bank_links(db, supplier['id']) == [supplier['account']]

    async def test_a_create_writes_no_links_for_an_unreadable_panel(
        self, client: AsyncClient, db: AsyncSession, supplier: dict
    ) -> None:
        await _grant(db, (SystemObject.SUPPLIERS, ALL))
        _as_clerk()

        created = await client.post(
            '/api/v1/suppliers',
            json={'code': 'S-NEW', 'name': 'Nuevo', 'bank_accounts': [supplier['account']]},
        )

        assert created.status_code == 201, created.text
        assert await _bank_links(db, created.json()['supplier_id']) == []

    async def test_a_readable_panel_is_still_written(
        self, client: AsyncClient, db: AsyncSession, supplier: dict
    ) -> None:
        await _grant(db, (SystemObject.SUPPLIERS, ALL), (SystemObject.BANK_ACCOUNTS, READ))
        _as_clerk()

        r = await client.put(
            f'/api/v1/suppliers/{supplier["id"]}',
            json={'bank_accounts': [supplier['other_account']]},
        )

        assert r.status_code == 200, r.text
        assert await _bank_links(db, supplier['id']) == [supplier['other_account']]

    async def test_post_and_put_responses_are_filtered_like_the_get(
        self, client: AsyncClient, db: AsyncSession, supplier: dict
    ) -> None:
        await _grant(db, (SystemObject.SUPPLIERS, ALL), (SystemObject.CONTACTS, READ))
        _as_clerk()

        created = await client.post(
            '/api/v1/suppliers', json={'code': 'S-RESP', 'name': 'Respuesta', 'contacts': [1]}
        )
        updated = await client.put(f'/api/v1/suppliers/{supplier["id"]}', json={'name': 'Otro'})

        assert created.status_code == 201, created.text
        assert updated.status_code == 200, updated.text
        assert _filled(created.json()) == {'contacts'}
        assert _filled(updated.json()) == {'contacts', 'agreements'}
