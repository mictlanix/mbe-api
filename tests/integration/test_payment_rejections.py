"""Rejections read back on the payment and the verification queue (#310).

A rejection is an incidence keyed to the payment; a reversed application is too, so only entries
that start with the rejection prefix count.
"""

from datetime import datetime

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import PaymentMethod, SourceType
from app.models.incidence import Incidence

UNVERIFIED = '/api/v1/customer-payments/unverified'


async def _payment(client: AsyncClient) -> int:
    created = await client.post(
        '/api/v1/customer-payments',
        json={'customer': 1, 'amount': '100', 'method': PaymentMethod.CASH},
    )
    assert created.status_code == 201, created.text
    assert created.json()['last_rejection'] is None
    return created.json()['customer_payment_id']


async def _reject(client: AsyncClient, payment_id: int, reason: str) -> dict:
    rejected = await client.post(
        f'/api/v1/customer-payments/{payment_id}/reject', json={'reason': reason}
    )
    assert rejected.status_code == 200, rejected.text
    return rejected.json()


def _rows(response) -> dict[int, dict]:  # noqa: ANN001
    assert response.status_code == 200, response.text
    return {row['customer_payment_id']: row for row in response.json()['items']}


async def test_a_rejection_shows_its_reason_employee_and_time(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    payment_id = await _payment(client)

    body = await _reject(client, payment_id, 'Not on the statement')
    read = await client.get(f'/api/v1/customer-payments/{payment_id}')
    listed = _rows(await client.get(UNVERIFIED, params={'limit': 100}))

    rejection = body['last_rejection']
    assert rejection['reason'] == 'Not on the statement'
    assert rejection['rejected_by'] == 1
    assert rejection['rejected_at'] is not None
    assert read.json()['last_rejection'] == rejection
    # Still in the queue by default, still unverified.
    assert listed[payment_id]['last_rejection'] == rejection
    assert listed[payment_id]['verifier'] is None


async def test_the_latest_of_two_rejections_wins(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    payment_id = await _payment(client)

    await _reject(client, payment_id, 'First look')
    body = await _reject(client, payment_id, 'Second look')
    listed = _rows(await client.get(UNVERIFIED, params={'limit': 100}))

    assert body['last_rejection']['reason'] == 'Second look'
    assert listed[payment_id]['last_rejection']['reason'] == 'Second look'


async def test_a_reversal_incidence_is_not_a_rejection(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    payment_id = await _payment(client)
    db.add(
        Incidence(
            source=int(SourceType.CUSTOMER_PAYMENT),
            instance_id=payment_id,
            modification_time=datetime(2026, 9, 1, 10, 0),
            updater=1,
            content='Reversed application 1 of 100 against sales order 1',
            comment='Wrong order',
        )
    )
    await db.commit()

    read = await client.get(f'/api/v1/customer-payments/{payment_id}')
    only_rejected = _rows(await client.get(UNVERIFIED, params={'rejected': True}))

    assert read.json()['last_rejection'] is None
    assert payment_id not in only_rejected


async def test_the_rejected_filter_splits_the_queue(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    rejected_ids = {await _payment(client), await _payment(client)}
    clean_id = await _payment(client)
    for payment_id in rejected_ids:
        await _reject(client, payment_id, 'Missing')
    # A second rejection must not list or count the payment twice.
    await _reject(client, min(rejected_ids), 'Still missing')

    everything = await client.get(UNVERIFIED, params={'limit': 100})
    only_rejected = await client.get(UNVERIFIED, params={'rejected': True, 'limit': 100})
    never_rejected = await client.get(UNVERIFIED, params={'rejected': False, 'limit': 100})
    first_page = await client.get(UNVERIFIED, params={'rejected': True, 'limit': 1})

    assert set(_rows(only_rejected)) == rejected_ids
    assert only_rejected.json()['total'] == 2
    assert clean_id in _rows(never_rejected)
    assert not set(_rows(never_rejected)) & rejected_ids
    assert never_rejected.json()['total'] == everything.json()['total'] - 2
    assert len(first_page.json()['items']) == 1
    assert first_page.json()['total'] == 2
