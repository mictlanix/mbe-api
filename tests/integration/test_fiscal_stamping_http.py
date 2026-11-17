"""Fiscal states and cancellation history through HTTP, on real tables (spec 039, US4 and US5).

The PAC is the simulator, writing to the test database; `get_pac_provider` is overridden to return
it. Each test seeds the SAT cancellation reasons before it cancels.
"""

from collections.abc import AsyncIterator
from datetime import datetime

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.main import app
from app.models.fiscal import FiscalDocument
from app.services.pac import get_pac_provider
from app.services.pac.simulated import Keys, SimulatedPac
from tests.integration.seed import seed_cancellation_reasons, seed_fiscal_document
from tests.integration.test_invoice_flow import Invoicing, _ready, invoicing  # noqa: F401

pytestmark = pytest.mark.usefixtures('seeded')

_URL = '/api/v1/fiscal-documents'
_PAC_CODE, _PAC_MESSAGE = 'CANC205', 'UUID no encontrado'


@pytest.fixture
async def pac(client: AsyncClient, sessions: async_sessionmaker) -> AsyncIterator[SimulatedPac]:
    """The simulator behind every route that takes a provider. `client` clears the override."""
    simulator = SimulatedPac(session_factory=sessions)
    app.dependency_overrides[get_pac_provider] = lambda: simulator
    yield simulator


async def _sealed(invoicing: Invoicing) -> int:  # noqa: F811
    """A new invoice, sealed with the next serial and ready to stamp."""
    return inspect(await invoicing.confirm(await _ready(invoicing))).identity[0]


async def _keys(db: AsyncSession, document_id: int) -> Keys:
    db.expire_all()
    document = await db.get(FiscalDocument, document_id)
    return (document.stamp_uuid, document.issuer, document.batch or '', document.serial)


def _ok(response: Response) -> dict:
    assert response.status_code == 200, response.text
    return response.json()


async def _issued(client: AsyncClient, invoicing: Invoicing) -> dict:  # noqa: F811
    """A sealed invoice stamped through HTTP."""
    document_id = await _sealed(invoicing)
    body = _ok(await client.post(f'{_URL}/{document_id}/stamp'))
    assert body['status'] == 'issued'
    return body


# ── States and history (US4), T039 ────────────────────────────────────────────


async def test_the_list_filters_by_each_of_the_six_states(
    client: AsyncClient, db: AsyncSession
) -> None:
    since = datetime(2026, 11, 1, 10, 0)
    sealed_xml = '<Comprobante Total="1.00"/>'
    unstamped = {'completed': False, 'stamp': False}
    expected = {
        'draft': [await seed_fiscal_document(db, 'invoice', serial=101, xml=None, **unstamped)],
        'sealed': [
            await seed_fiscal_document(db, 'invoice', serial=102, xml=sealed_xml, **unstamped)
        ],
        'stamping': [
            await seed_fiscal_document(
                db, 'invoice', serial=103, xml=sealed_xml, stamping_since=since, **unstamped
            )
        ],
        'issued': [
            await seed_fiscal_document(db, 'invoice', serial=serial, stamp_uuid=uuid)
            for serial, uuid in (
                (104, '00000000-0000-4000-8000-000000000104'),
                (105, '00000000-0000-4000-8000-000000000105'),
            )
        ],
        'cancellation_pending': [
            await seed_fiscal_document(
                db,
                'invoice',
                serial=106,
                stamp_uuid='00000000-0000-4000-8000-000000000106',
                cancelling_since=since,
            )
        ],
        'cancelled': [
            await seed_fiscal_document(
                db,
                'invoice',
                serial=107,
                stamp_uuid='00000000-0000-4000-8000-000000000107',
                cancelled=True,
            )
        ],
    }

    for state, ids in expected.items():
        body = _ok(await client.get(_URL, params={'status': state}))
        assert sorted(item['fiscal_document_id'] for item in body['items']) == sorted(ids), state
        assert {item['status'] for item in body['items']} == {state}
        assert body['total'] == len(ids)

    # Paging works as it does without the filter.
    pages = [
        _ok(await client.get(_URL, params={'status': 'issued', 'limit': 1, 'skip': skip}))
        for skip in (0, 1)
    ]
    assert [page['total'] for page in pages] == [2, 2]
    assert sorted(page['items'][0]['fiscal_document_id'] for page in pages) == sorted(
        expected['issued']
    )


async def test_the_cancel_response_lists_the_request_it_created(
    client: AsyncClient,
    invoicing: Invoicing,  # noqa: F811
    pac: SimulatedPac,
) -> None:
    await seed_cancellation_reasons(invoicing.db)
    document_id = (await _issued(client, invoicing))['fiscal_document_id']
    await pac.set_next(await _keys(invoicing.db, document_id), cancellation='pending')

    body = _ok(await client.post(f'{_URL}/{document_id}/cancel', json={'reason': '02'}))

    assert body['status'] == 'cancellation_pending'
    [request] = body['cancellations']
    assert (request['reason'], request['substitution']) == ('02', None)
    assert request['status'] == 'pending'
    assert request['requested_by'] == invoicing.current.employee_id
    assert request['requested_at'] is not None
    assert request['resolved_at'] is None


async def test_a_document_rejected_once_and_then_cancelled_lists_both_requests(
    client: AsyncClient,
    invoicing: Invoicing,  # noqa: F811
    pac: SimulatedPac,
) -> None:
    db = invoicing.db
    await seed_cancellation_reasons(db)
    document_id = (await _issued(client, invoicing))['fiscal_document_id']
    substitute = (await _issued(client, invoicing))['stamp_uuid']
    keys = await _keys(db, document_id)

    await pac.set_next(keys, cancellation='rejected', pac_code=_PAC_CODE, pac_message=_PAC_MESSAGE)
    rejected = await client.post(f'{_URL}/{document_id}/cancel', json={'reason': '02'})
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()['code'] == 'pac.rejected'

    await pac.set_next(keys, cancellation='accepted')
    body = _ok(
        await client.post(
            f'{_URL}/{document_id}/cancel', json={'reason': '01', 'substitution': substitute}
        )
    )

    assert body['status'] == 'cancelled'
    assert body['cancellation_substitution'] == substitute
    newest, oldest = body['cancellations']
    assert newest['fiscal_document_cancellation_id'] > oldest['fiscal_document_cancellation_id']
    assert (newest['reason'], newest['substitution'], newest['status']) == (
        '01',
        substitute,
        'accepted',
    )
    assert newest['answered_at'] is not None
    assert newest['resolved_at'] is not None
    assert (oldest['reason'], oldest['substitution'], oldest['status']) == ('02', None, 'rejected')
    assert (oldest['pac_code'], oldest['pac_message']) == (_PAC_CODE, _PAC_MESSAGE)
    assert oldest['resolved_at'] is not None
    for request in (newest, oldest):
        assert request['requested_by'] == invoicing.current.employee_id
        assert request['requested_at'] is not None
    assert _ok(await client.get(f'{_URL}/{document_id}'))['cancellations'] == body['cancellations']


async def test_a_document_stamped_by_the_current_system_reads_issued(
    client: AsyncClient, db: AsyncSession
) -> None:
    document_id = await seed_fiscal_document(db, 'invoice')

    body = _ok(await client.get(f'{_URL}/{document_id}'))

    assert body['status'] == 'issued'
    assert body['stamp']['uuid'] == body['stamp_uuid']
    assert body['stamp']['simulated'] is False
    assert body['cancellations'] == []
    assert body['cancellation_substitution'] is None
