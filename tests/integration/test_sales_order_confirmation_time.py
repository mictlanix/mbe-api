"""Spec 037, T003: confirming a sales order records the moment, and nothing else ever changes it.

Analytics count a sale on the moment it was confirmed (FR-002). Before this spec nothing recorded
it: `date` is the creation time. The one confirm path now stamps `confirmation_time`, a draft has
none, and a cancellation after confirmation leaves it as it was.
"""

from datetime import datetime

import pytest
from httpx import AsyncClient

from app.services import sales_order_service

pytestmark = pytest.mark.usefixtures('seeded')

FROZEN = datetime(2026, 3, 14, 10, 30, 15)


async def _draft(client: AsyncClient) -> int:
    created = await client.post('/api/v1/sales-orders', json={'customer': 1, 'payment_terms': 0})
    assert created.status_code == 201, created.text
    order_id = created.json()['sales_order_id']
    lined = await client.post(
        f'/api/v1/sales-orders/{order_id}/lines',
        json={'product': 1, 'quantity': '1', 'warehouse': 1, 'price': '100'},
    )
    assert lined.status_code == 200, lined.text
    return order_id


async def test_a_draft_has_no_confirmation_time(client: AsyncClient) -> None:
    order_id = await _draft(client)

    read = await client.get(f'/api/v1/sales-orders/{order_id}')

    assert read.status_code == 200, read.text
    assert read.json()['confirmation_time'] is None


async def test_confirming_records_the_moment(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    order_id = await _draft(client)
    monkeypatch.setattr(sales_order_service, 'local_now', lambda: FROZEN)

    confirmed = await client.post(f'/api/v1/sales-orders/{order_id}/confirm')
    read = await client.get(f'/api/v1/sales-orders/{order_id}')

    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()['confirmation_time'] == FROZEN.isoformat()
    assert read.json()['confirmation_time'] == FROZEN.isoformat()


async def test_cancelling_afterwards_keeps_the_confirmation_time(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    order_id = await _draft(client)
    monkeypatch.setattr(sales_order_service, 'local_now', lambda: FROZEN)
    confirmed = await client.post(f'/api/v1/sales-orders/{order_id}/confirm')
    assert confirmed.status_code == 200, confirmed.text
    monkeypatch.setattr(sales_order_service, 'local_now', lambda: datetime(2026, 3, 20, 9, 0))

    cancelled = await client.post(f'/api/v1/sales-orders/{order_id}/cancel')

    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()['confirmation_time'] == FROZEN.isoformat()
