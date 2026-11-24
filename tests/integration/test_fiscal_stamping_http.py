"""Fiscal states and cancellation history through HTTP, on real tables (spec 039, US4 and US5).

The PAC is the simulator, writing to the test database; `get_pac_provider` is overridden to return
it. Each test seeds the SAT cancellation reasons before it cancels. The scenario walk (US5) drives
the simulator through its own routes only, one test per row of contracts/endpoints.md's "Driving
each scenario".
"""

from collections.abc import AsyncIterator
from datetime import datetime, timedelta

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.main import app
from app.models.fiscal import FiscalDocument
from app.services.pac import get_pac_provider, get_pac_simulator
from app.services.pac.adapter import CfdiPacProvider
from tests.fakes.simulator import set_cancellation, simulated_provider
from tests.integration.seed import seed_cancellation_reasons, seed_fiscal_document
from tests.integration.test_invoice_flow import Invoicing, _ready, invoicing  # noqa: F401

pytestmark = pytest.mark.usefixtures('seeded')

_URL = '/api/v1/fiscal-documents'
_PAC_CODE, _PAC_MESSAGE = 'CANC205', 'UUID no encontrado'


@pytest.fixture
async def pac(client: AsyncClient, sessions: async_sessionmaker) -> AsyncIterator[CfdiPacProvider]:
    """The simulator behind every route that takes a provider. `client` clears the override."""
    provider = simulated_provider(sessions)
    app.dependency_overrides[get_pac_provider] = lambda: provider
    app.dependency_overrides[get_pac_simulator] = lambda: provider.simulator
    yield provider


async def _sealed(invoicing: Invoicing) -> int:  # noqa: F811
    """A new invoice, sealed with the next serial and ready to stamp."""
    return inspect(await invoicing.confirm(await _ready(invoicing))).identity[0]


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
    pac: CfdiPacProvider,
) -> None:
    await seed_cancellation_reasons(invoicing.db)
    document_id = (await _issued(client, invoicing))['fiscal_document_id']
    await set_cancellation(pac, invoicing.db, document_id, 'pending')

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
    pac: CfdiPacProvider,
) -> None:
    db = invoicing.db
    await seed_cancellation_reasons(db)
    document_id = (await _issued(client, invoicing))['fiscal_document_id']
    substitute = (await _issued(client, invoicing))['stamp_uuid']

    await set_cancellation(pac, db, document_id, 'rejected', code=_PAC_CODE, message=_PAC_MESSAGE)
    rejected = await client.post(f'{_URL}/{document_id}/cancel', json={'reason': '02'})
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()['code'] == 'pac.rejected'

    await set_cancellation(pac, db, document_id, 'accepted')
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


# ── Driving each scenario through HTTP (US5, SC-001), T043 ────────────────────

_SIMULATOR = '/api/v1/pac-simulator/fiscal-documents'


@pytest.fixture
def simulated(pac: CfdiPacProvider, monkeypatch: pytest.MonkeyPatch) -> CfdiPacProvider:
    """`PAC_PROVIDER=simulated`, which opens the simulator's routes."""
    monkeypatch.setattr(settings, 'pac_provider', 'simulated')
    return pac


async def _choose(client: AsyncClient, document_id: int, **outcome: str) -> None:
    _ok(await client.put(f'{_SIMULATOR}/{document_id}', json=outcome))


async def _post(client: AsyncClient, document_id: int, action: str, **body: object) -> Response:
    return await client.post(f'{_URL}/{document_id}/{action}', json=body or None)


def _refused(response: Response, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    assert response.json()['code'] == code


async def _status(client: AsyncClient, document_id: int) -> str:
    return _ok(await client.get(f'{_URL}/{document_id}'))['status']


async def _history(client: AsyncClient, document_id: int) -> list[str]:
    body = _ok(await client.get(f'{_URL}/{document_id}'))
    return [request['status'] for request in body['cancellations']]


async def _stamp_refused(
    client: AsyncClient,
    invoicing: Invoicing,  # noqa: F811
    outcome: str,
    status: int,
    code: str,
) -> int:
    document_id = await _sealed(invoicing)
    await _choose(client, document_id, stamp=outcome)
    _refused(await _post(client, document_id, 'stamp'), status, code)
    return document_id


async def _cancel_with(client: AsyncClient, invoicing: Invoicing, outcome: str) -> tuple:  # noqa: F811
    """An issued invoice whose cancellation the PAC answers with `outcome`: its id and the
    response to /cancel."""
    await seed_cancellation_reasons(invoicing.db)
    document_id = (await _issued(client, invoicing))['fiscal_document_id']
    await _choose(client, document_id, cancellation=outcome)
    return document_id, await _post(client, document_id, 'cancel', reason='02')


async def test_scenario_stamped(
    client: AsyncClient,
    invoicing: Invoicing,  # noqa: F811
    simulated: CfdiPacProvider,
) -> None:
    document_id = await _sealed(invoicing)

    body = _ok(await _post(client, document_id, 'stamp'))

    assert body['status'] == 'issued'
    assert body['stamp']['simulated'] is True
    record = _ok(await client.get(f'{_SIMULATOR}/{document_id}'))
    assert record['uuid'] == body['stamp_uuid']


@pytest.mark.parametrize(
    ('outcome', 'status', 'code'),
    [('rejected', 422, 'pac.rejected'), ('unavailable', 503, 'pac.unavailable')],
)
async def test_scenario_stamp_refused_leaves_it_sealed(
    client: AsyncClient,
    invoicing: Invoicing,  # noqa: F811
    simulated: CfdiPacProvider,
    outcome: str,
    status: int,
    code: str,
) -> None:
    document_id = await _stamp_refused(client, invoicing, outcome, status, code)

    assert await _status(client, document_id) == 'sealed'


async def test_scenario_timeout_did_stamp(
    client: AsyncClient,
    invoicing: Invoicing,  # noqa: F811
    simulated: CfdiPacProvider,
) -> None:
    document_id = await _stamp_refused(client, invoicing, 'timeout_stamped', 504, 'pac.timeout')
    assert await _status(client, document_id) == 'stamping'

    body = _ok(await _post(client, document_id, 'refresh'))

    assert body['status'] == 'issued'
    assert body['stamp']['simulated'] is True


async def test_scenario_timeout_did_not_stamp(
    client: AsyncClient,
    invoicing: Invoicing,  # noqa: F811
    simulated: CfdiPacProvider,
) -> None:
    db = invoicing.db
    document_id = await _stamp_refused(client, invoicing, 'timeout_not_stamped', 504, 'pac.timeout')
    assert await _status(client, document_id) == 'stamping'
    # Past the 5-minute grace, set back rather than waited for.
    db.expire_all()
    document = await db.get(FiscalDocument, document_id)
    document.stamping_since -= timedelta(minutes=6)
    await db.commit()

    body = _ok(await _post(client, document_id, 'refresh'))

    assert body['status'] == 'sealed'
    assert body['stamp'] is None


async def test_scenario_cancelled_at_once(
    client: AsyncClient,
    invoicing: Invoicing,  # noqa: F811
    simulated: CfdiPacProvider,
) -> None:
    document_id, response = await _cancel_with(client, invoicing, 'accepted')

    assert _ok(response)['status'] == 'cancelled'
    assert await _history(client, document_id) == ['accepted']


@pytest.mark.parametrize(
    ('answer', 'state', 'history'),
    [
        ('accept', 'cancelled', 'accepted'),
        ('reject', 'issued', 'rejected'),
        ('expire', 'cancelled', 'expired'),
    ],
)
async def test_scenario_pending_then_answered_by_the_receiver(
    client: AsyncClient,
    invoicing: Invoicing,  # noqa: F811
    simulated: CfdiPacProvider,
    answer: str,
    state: str,
    history: str,
) -> None:
    document_id, response = await _cancel_with(client, invoicing, 'pending')
    assert _ok(response)['status'] == 'cancellation_pending'

    record = _ok(await client.post(f'{_SIMULATOR}/{document_id}/receiver', json={'answer': answer}))
    assert record['cancellation_status'] == history
    body = _ok(await _post(client, document_id, 'refresh'))

    assert body['status'] == state
    assert [request['status'] for request in body['cancellations']] == [history]


async def test_scenario_not_cancellable(
    client: AsyncClient,
    invoicing: Invoicing,  # noqa: F811
    simulated: CfdiPacProvider,
) -> None:
    document_id, response = await _cancel_with(client, invoicing, 'not_cancellable')

    _refused(response, 409, 'fiscal_document.not_cancellable')
    assert await _status(client, document_id) == 'issued'


@pytest.mark.parametrize(
    ('outcome', 'status', 'code', 'history'),
    [
        ('rejected', 422, 'pac.rejected', 'rejected'),
        ('unavailable', 503, 'pac.unavailable', 'failed'),
    ],
)
async def test_scenario_cancellation_refused_leaves_it_issued(
    client: AsyncClient,
    invoicing: Invoicing,  # noqa: F811
    simulated: CfdiPacProvider,
    outcome: str,
    status: int,
    code: str,
    history: str,
) -> None:
    document_id, response = await _cancel_with(client, invoicing, outcome)

    _refused(response, status, code)
    assert await _status(client, document_id) == 'issued'
    assert await _history(client, document_id) == [history]


async def test_scenario_timeout_on_cancel(
    client: AsyncClient,
    invoicing: Invoicing,  # noqa: F811
    simulated: CfdiPacProvider,
) -> None:
    document_id, response = await _cancel_with(client, invoicing, 'timeout')
    _refused(response, 504, 'pac.timeout')
    assert await _status(client, document_id) == 'cancellation_pending'
    assert await _history(client, document_id) == ['requested']

    body = _ok(await _post(client, document_id, 'refresh'))

    # SAT has the request: it is pending, waiting for the receiver.
    assert body['status'] == 'cancellation_pending'
    assert [request['status'] for request in body['cancellations']] == ['pending']
