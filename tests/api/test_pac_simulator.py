"""Tests for the simulated PAC's controls (spec 039 R7, spec 040 R12, contracts/simulator-controls).

The routes over the CFDI library's simulator, kept in memory: `get_pac_simulator` is overridden,
and the document read is patched. The routes over the database store, driving the stamp and
cancel flows, are covered in tests/integration/test_fiscal_stamping_http.py.
"""

from collections.abc import Generator
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status
from lxml import etree
from mictlanix.cfdi import pac as lib

from app.core.config import settings
from app.enums import AccessRight
from app.main import app
from app.services.pac import get_pac_simulator
from tests.api.test_fiscal_documents import _auth, _client, _deny_privileges, _row
from tests.problem import assert_problem
from tests.unit.test_pac_adapter import _sealed

_SERVICE = 'app.services.fiscal_document_service'
_URL = '/api/v1/pac-simulator/fiscal-documents/9'
_CALLS = [
    ('get', _URL, None),
    ('put', _URL, {'stamp': 'rejected'}),
    ('post', f'{_URL}/receiver', {'answer': 'accept'}),
]
_SEALED = _sealed()


@pytest.fixture(autouse=True)
def _clear_overrides() -> Generator[None, None, None]:
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def simulator(monkeypatch: pytest.MonkeyPatch) -> lib.SimulatedPac:
    """The simulator on: the library's, in memory, behind `get_pac_simulator`."""
    monkeypatch.setattr(settings, 'pac_provider', 'simulated')
    pac = lib.SimulatedPac()
    app.dependency_overrides[get_pac_simulator] = lambda: pac
    return pac


def _document_of(xml: str, **overrides: object):  # noqa: ANN202
    """A row for `xml`'s parties and total, as `get_document` returns it."""
    root = etree.fromstring(xml.encode('utf-8'))
    cfdi = {'c': 'http://www.sat.gob.mx/cfd/4'}
    values = dict(
        issuer=root.find('c:Emisor', cfdi).get('Rfc'),
        recipient=root.find('c:Receptor', cfdi).get('Rfc'),
        total=Decimal(root.get('Total')),
    )
    values.update(overrides)
    return _row(**values)


async def _issued(pac: lib.SimulatedPac, **overrides: object):  # noqa: ANN202
    """A document the simulator stamped, and its stamped XML."""
    stamp = await pac.stamp(_SEALED.encode('utf-8'))
    xml = stamp.xml.decode('utf-8')
    return _document_of(xml, stamp_uuid=stamp.timbre.UUID, status='issued', **overrides), xml


async def _call(method: str, url: str, body: dict | None, document, xml: str | None):  # noqa: ANN001, ANN202
    with (
        patch(f'{_SERVICE}.get_document', AsyncMock(return_value=document)),
        patch(f'{_SERVICE}.get_xml', AsyncMock(return_value=xml)),
    ):
        async with await _client() as client:
            if body is None:
                return await client.request(method, url)
            return await client.request(method, url, json=body)


def _sealed_document(**overrides: object):  # noqa: ANN202
    values: dict = {'status': 'sealed', 'stamp_uuid': None}
    values.update(overrides)
    return _document_of(_SEALED, **values)


# ── GET ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_of_an_unstamped_document_is_not_found_at_sat(
    simulator: lib.SimulatedPac,
) -> None:
    _auth()

    response = await _call('get', _URL, None, _sealed_document(), _SEALED)

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        'uuid': None,
        'sat_status': 'not_found',
        'cancellation_status': 'none',
    }


@pytest.mark.asyncio
async def test_get_of_a_stamped_document_reads_the_simulated_sat(
    simulator: lib.SimulatedPac,
) -> None:
    _auth()
    document, xml = await _issued(simulator)

    response = await _call('get', _URL, None, document, xml)

    assert response.json() == {
        'uuid': document.stamp_uuid,
        'sat_status': 'valid',
        'cancellation_status': 'none',
    }


# ── PUT {stamp} ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('outcome', 'error'),
    [
        ('rejected', lib.PacRejected),
        ('unavailable', lib.PacUnavailable),
        ('timeout_not_stamped', lib.PacTimeout),
    ],
)
async def test_a_stamp_outcome_applies_to_the_next_stamp_only(
    simulator: lib.SimulatedPac, outcome: str, error: type[Exception]
) -> None:
    _auth()

    response = await _call('put', _URL, {'stamp': outcome}, _sealed_document(), _SEALED)

    assert response.status_code == status.HTTP_200_OK
    with pytest.raises(error):
        await simulator.stamp(_SEALED.encode('utf-8'))
    assert (await simulator.stamp(_SEALED.encode('utf-8'))).timbre.UUID


@pytest.mark.asyncio
async def test_a_rejected_stamp_carries_the_code_and_message(simulator: lib.SimulatedPac) -> None:
    _auth()
    body = {'stamp': 'rejected', 'pac_code': 'CFDI40102', 'pac_message': 'Sello inválido'}

    await _call('put', _URL, body, _sealed_document(), _SEALED)

    with pytest.raises(lib.PacRejected) as raised:
        await simulator.stamp(_SEALED.encode('utf-8'))
    assert (raised.value.code, raised.value.message) == ('CFDI40102', 'Sello inválido')


@pytest.mark.asyncio
async def test_a_stamp_outcome_while_stamping_is_refused(simulator: lib.SimulatedPac) -> None:
    _auth()

    response = await _call(
        'put', _URL, {'stamp': 'rejected'}, _sealed_document(status='stamping'), _SEALED
    )

    assert_problem(response, 409, 'fiscal_document.stamping')
    assert (await simulator.stamp(_SEALED.encode('utf-8'))).timbre.UUID


@pytest.mark.asyncio
async def test_a_stamp_outcome_on_a_document_the_old_simulator_stamped_is_a_rejection(
    simulator: lib.SimulatedPac,
) -> None:
    """The library has no record of that stamp: SAT's 307 (contracts/simulator-controls.md)."""
    _auth()
    document, xml = await _issued(lib.SimulatedPac())

    response = await _call('put', _URL, {'stamp': 'rejected'}, document, xml)

    problem = assert_problem(response, 422, 'pac.rejected')
    assert problem['params']['pac_code'] == '307'


# ── PUT {cancellation} ────────────────────────────────────────────────────────


def _cancellation(document) -> lib.CancellationRequest:  # noqa: ANN001
    return lib.CancellationRequest(
        issuer_rfc=document.issuer,
        recipient_rfc=document.recipient,
        uuid=document.stamp_uuid,
        total=document.total,
        reason='02',
        csd=lib.CsdCredentials(certificate=b'c', private_key=b'k', password='p'),
    )


@pytest.mark.asyncio
async def test_a_cancellation_outcome_needs_the_stamp(simulator: lib.SimulatedPac) -> None:
    _auth()

    response = await _call('put', _URL, {'cancellation': 'accepted'}, _sealed_document(), _SEALED)

    assert_problem(response, 409, 'fiscal_document.not_stamped')


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('outcome', 'answer'),
    [
        ('accepted', lib.CancellationAnswer.ACCEPTED),
        ('pending', lib.CancellationAnswer.PENDING),
        ('not_cancellable', lib.CancellationAnswer.NOT_CANCELLABLE),
    ],
)
async def test_a_cancellation_outcome_applies_to_the_next_request(
    simulator: lib.SimulatedPac, outcome: str, answer: lib.CancellationAnswer
) -> None:
    _auth()
    document, xml = await _issued(simulator)

    response = await _call('put', _URL, {'cancellation': outcome}, document, xml)

    assert response.status_code == status.HTTP_200_OK
    assert await simulator.request_cancellation(_cancellation(document)) == answer


@pytest.mark.asyncio
async def test_not_cancellable_is_permanent(simulator: lib.SimulatedPac) -> None:
    _auth()
    document, xml = await _issued(simulator)

    await _call('put', _URL, {'cancellation': 'not_cancellable'}, document, xml)

    for _ in range(2):
        answer = await simulator.request_cancellation(_cancellation(document))
        assert answer == lib.CancellationAnswer.NOT_CANCELLABLE


@pytest.mark.asyncio
async def test_a_rejected_cancellation_carries_the_code_and_message(
    simulator: lib.SimulatedPac,
) -> None:
    _auth()
    document, xml = await _issued(simulator)
    body = {'cancellation': 'rejected', 'pac_code': 'CANC205', 'pac_message': 'No encontrado'}

    await _call('put', _URL, body, document, xml)

    with pytest.raises(lib.PacRejected) as raised:
        await simulator.request_cancellation(_cancellation(document))
    assert (raised.value.code, raised.value.message) == ('CANC205', 'No encontrado')


# ── POST …/receiver ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('answer', 'sat_status', 'cancellation_status'),
    [
        ('accept', 'cancelled', 'accepted'),
        ('reject', 'valid', 'rejected'),
        ('expire', 'cancelled', 'expired'),
    ],
)
async def test_the_receiver_answers_a_pending_cancellation(
    simulator: lib.SimulatedPac, answer: str, sat_status: str, cancellation_status: str
) -> None:
    _auth()
    document, xml = await _issued(simulator)
    await simulator.configure_cancellation(document.stamp_uuid, lib.CancellationOutcome.PENDING)
    await simulator.request_cancellation(_cancellation(document))

    response = await _call('post', f'{_URL}/receiver', {'answer': answer}, document, xml)

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        'uuid': document.stamp_uuid,
        'sat_status': sat_status,
        'cancellation_status': cancellation_status,
    }


@pytest.mark.asyncio
async def test_the_receiver_with_nothing_pending_is_409(simulator: lib.SimulatedPac) -> None:
    _auth()
    document, xml = await _issued(simulator)

    response = await _call('post', f'{_URL}/receiver', {'answer': 'accept'}, document, xml)

    assert_problem(response, 409, 'fiscal_document.nothing_to_refresh')


@pytest.mark.asyncio
async def test_the_receiver_without_a_stamp_is_409(simulator: lib.SimulatedPac) -> None:
    _auth()

    response = await _call(
        'post', f'{_URL}/receiver', {'answer': 'accept'}, _sealed_document(), _SEALED
    )

    assert_problem(response, 409, 'fiscal_document.nothing_to_refresh')


# ── Every route ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(('method', 'url', 'body'), _CALLS)
async def test_routes_require_authentication(
    simulator: lib.SimulatedPac, method: str, url: str, body: dict | None
) -> None:
    response = await _call(method, url, body, _sealed_document(), _SEALED)

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
@pytest.mark.parametrize(('method', 'url', 'body'), _CALLS)
async def test_routes_require_update_on_fiscal_documents(
    simulator: lib.SimulatedPac, method: str, url: str, body: dict | None
) -> None:
    _auth(administrator=False)
    _deny_privileges()

    response = await _call(method, url, body, _sealed_document(), _SEALED)

    problem = assert_problem(response, 403, 'auth.forbidden')
    assert problem['params']['required'] == [
        {'system_object': 23, 'right': int(AccessRight.UPDATE)}
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(('method', 'url', 'body'), _CALLS)
async def test_an_unknown_document_is_404(
    simulator: lib.SimulatedPac, method: str, url: str, body: dict | None
) -> None:
    _auth()

    response = await _call(method, url, body, None, None)

    problem = assert_problem(response, 404, 'resource.not_found')
    assert problem['params'] == {'resource': 'fiscal_document', 'id': 9}


@pytest.mark.asyncio
@pytest.mark.parametrize(('method', 'url', 'body'), _CALLS)
async def test_a_draft_is_not_sealed(
    simulator: lib.SimulatedPac, method: str, url: str, body: dict | None
) -> None:
    _auth()

    response = await _call(method, url, body, _sealed_document(status='draft'), _SEALED)

    assert_problem(response, 409, 'fiscal_document.not_sealed')


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('method', 'url', 'body'),
    [
        ('put', _URL, {'stamp': 'accepted'}),
        ('put', _URL, {'cancellation': 'stamped'}),
        ('put', _URL, {'pac_code': 'C' * 21}),
        ('put', _URL, {'pac_message': 'm' * 501}),
        ('post', f'{_URL}/receiver', {'answer': 'accepted'}),
        ('post', f'{_URL}/receiver', {}),
    ],
)
async def test_an_invalid_body_is_422(
    simulator: lib.SimulatedPac, method: str, url: str, body: dict
) -> None:
    _auth()

    response = await _call(method, url, body, _sealed_document(), _SEALED)

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


@pytest.mark.asyncio
@pytest.mark.parametrize('authenticated', [True, False])
@pytest.mark.parametrize(('method', 'url', 'body'), _CALLS)
async def test_every_route_is_not_found_unless_the_simulator_is_on(
    monkeypatch: pytest.MonkeyPatch, authenticated: bool, method: str, url: str, body: dict | None
) -> None:
    monkeypatch.setattr(settings, 'pac_provider', 'none')
    if authenticated:
        _auth()

    response = await _call(method, url, body, _sealed_document(), _SEALED)

    problem = assert_problem(response, 404, 'request.route_not_found')
    assert not problem.get('params')
