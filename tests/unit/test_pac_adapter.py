"""The one PAC adapter over the CFDI library (spec 040, research R2, R3).

`CfdiPacProvider` is tested over the library's own simulator and simulated SAT status, and over a
scripted library client for what the simulators cannot produce (the SAT's errors, a captured
request, every status value).
"""

import logging
from datetime import datetime
from decimal import Decimal

import pytest
from lxml import etree
from mictlanix.cfdi import pac as lib
from mictlanix.cfdi import sat as libsat

from app.services import cfdi_invoice as cfdi
from app.services.pac.adapter import CfdiPacProvider
from app.services.pac.provider import (
    SIMULATED_PAC_RFC,
    CancellationAnswer,
    CancellationRequest,
    Csd,
    PacRejected,
    PacTimeout,
    PacUnavailable,
    SatStatus,
)
from tests.unit.test_cfdi_invoice import _fixture_data
from tests.unit.test_csd_service import _CERTIFICATE_NUMBER, _PASSWORD, _make_csd

_SENTINEL = 'sentinel-secret-0451'
_CFDI = 'http://www.sat.gob.mx/cfd/4'


def _sealed() -> str:
    certificate_data, key_data = _make_csd()
    return cfdi.seal(
        _fixture_data(),
        certificate_number=_CERTIFICATE_NUMBER,
        certificate_data=certificate_data,
        key_data=key_data,
        key_password=_PASSWORD,
    )


def _identity(sealed_xml: str) -> tuple[str, str, Decimal]:
    root = etree.fromstring(sealed_xml.encode('utf-8'))
    return (
        root.find(f'{{{_CFDI}}}Emisor').get('Rfc'),
        root.find(f'{{{_CFDI}}}Receptor').get('Rfc'),
        Decimal(root.get('Total')),
    )


def _simulated() -> tuple[CfdiPacProvider, lib.SimulatedPac]:
    pac = lib.SimulatedPac()
    provider = CfdiPacProvider(
        pac, libsat.SimulatedSatStatusClient(pac), name='simulator', resolves_by_resubmitting=False
    )
    return provider, pac


def _request(
    uuid: str,
    sealed_xml: str,
    *,
    reason: str = '02',
    substitution: str | None = None,
    password: bytes = b'csd-password',
) -> CancellationRequest:
    issuer, recipient, total = _identity(sealed_xml)
    return CancellationRequest(
        issuer_rfc=issuer,
        recipient_rfc=recipient,
        uuid=uuid,
        total=total,
        reason=reason,
        substitution=substitution,
        csd=Csd(certificate=b'cer-bytes', key=b'key-bytes', password=password),
    )


class ScriptedClient:
    """A library `PacClient` and `SatStatusClient` that records its input and answers as told."""

    def __init__(self, *, stamp=None, cancel=None, status=None, error=None) -> None:  # noqa: ANN001
        self.stamp_result, self.cancel_result, self.status_result = stamp, cancel, status
        self.error = error
        self.received: list[object] = []

    async def stamp(self, sealed_xml: bytes) -> lib.Stamp:
        self.received.append(sealed_xml)
        if self.error is not None:
            raise self.error
        return self.stamp_result

    async def find_stamp(self, sealed_xml: bytes) -> lib.Stamp | None:
        self.received.append(sealed_xml)
        return None

    async def request_cancellation(
        self, request: lib.CancellationRequest
    ) -> lib.CancellationAnswer:
        self.received.append(request)
        if self.error is not None:
            raise self.error
        return self.cancel_result

    async def query_status(
        self, issuer_rfc: str, recipient_rfc: str, uuid: str, total: Decimal
    ) -> libsat.SatStatus:
        self.received.append((issuer_rfc, recipient_rfc, uuid, total))
        if self.error is not None:
            raise self.error
        return self.status_result


def _scripted(client: ScriptedClient) -> CfdiPacProvider:
    return CfdiPacProvider(client, client, name='multifacturas', resolves_by_resubmitting=True)


# ── Stamp ─────────────────────────────────────────────────────────────────────


async def test_a_stamp_is_translated_from_the_library() -> None:
    provider, _ = _simulated()
    sealed = _sealed()

    stamp = await provider.stamp(sealed)

    root = etree.fromstring(stamp.xml.encode('utf-8'))
    timbre = root.find('.//{*}TimbreFiscalDigital')
    assert isinstance(stamp.xml, str)
    assert stamp.uuid == timbre.get('UUID') == stamp.uuid.upper()
    assert stamp.sat_seal == timbre.get('SelloSAT')
    assert stamp.sat_certificate == timbre.get('NoCertificadoSAT')
    assert stamp.provider_rfc == timbre.get('RfcProvCertif') == SIMULATED_PAC_RFC == 'SIM010101SI0'
    assert stamp.stamped_at.tzinfo is None
    assert stamp.stamped_at == datetime.fromisoformat(timbre.get('FechaTimbrado'))


async def test_the_library_receives_the_sealed_xml_encoded_as_utf8() -> None:
    _, pac = _simulated()
    sealed = _sealed()
    client = ScriptedClient(stamp=await pac.stamp(sealed.encode('utf-8')))

    await _scripted(client).stamp(sealed)

    assert client.received == [sealed.encode('utf-8')]


async def test_find_stamp_returns_the_simulators_stamp_or_none() -> None:
    provider, _ = _simulated()
    sealed = _sealed()

    assert await provider.find_stamp(sealed) is None
    stamp = await provider.stamp(sealed)
    found = await provider.find_stamp(sealed)

    assert found == stamp


async def test_each_stamp_call_logs_one_line_without_the_xml(
    caplog: pytest.LogCaptureFixture,
) -> None:
    provider, _ = _simulated()
    sealed = _sealed()

    with caplog.at_level(logging.WARNING, logger='app.services.pac.adapter'):
        await provider.stamp(sealed)

    lines = [r.getMessage() for r in caplog.records if r.name == 'app.services.pac.adapter']
    assert lines == ['pac simulator: stamp call EKU9003173C9 RH11405']


def test_the_flag_follows_the_constructor() -> None:
    provider, _ = _simulated()

    assert provider.resolves_by_resubmitting is False
    assert _scripted(ScriptedClient()).resolves_by_resubmitting is True


# ── Cancellation ──────────────────────────────────────────────────────────────


async def test_a_cancellation_request_is_translated_for_the_library() -> None:
    sealed = _sealed()
    client = ScriptedClient(cancel=lib.CancellationAnswer.PENDING)
    uuid = '11111111-2222-3333-4444-555555555555'
    substitute = 'AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE'

    answer = await _scripted(client).request_cancellation(
        _request(uuid, sealed, reason='01', substitution=substitute)
    )

    [sent] = client.received
    assert answer == CancellationAnswer.PENDING
    assert (sent.issuer_rfc, sent.recipient_rfc, sent.total) == _identity(sealed)
    assert (sent.uuid, sent.reason, sent.substitute_uuid) == (uuid, '01', substitute)
    assert sent.csd.certificate == b'cer-bytes'
    assert sent.csd.private_key == b'key-bytes'
    assert sent.csd.password == 'csd-password'


@pytest.mark.parametrize(
    ('outcome', 'answer'),
    [
        (lib.CancellationOutcome.ACCEPTED, CancellationAnswer.ACCEPTED),
        (lib.CancellationOutcome.PENDING, CancellationAnswer.PENDING),
        (lib.CancellationOutcome.NOT_CANCELLABLE, CancellationAnswer.NOT_CANCELLABLE),
    ],
)
async def test_cancellation_answers_come_back_by_name(
    outcome: lib.CancellationOutcome, answer: CancellationAnswer
) -> None:
    provider, pac = _simulated()
    sealed = _sealed()
    stamp = await provider.stamp(sealed)
    await pac.configure_cancellation(stamp.uuid, outcome)

    assert await provider.request_cancellation(_request(stamp.uuid, sealed)) == answer


async def test_a_request_the_library_refuses_to_build_is_a_rejection() -> None:
    sealed = _sealed()
    client = ScriptedClient(cancel=lib.CancellationAnswer.ACCEPTED)
    request = _request(
        '11111111-2222-3333-4444-555555555555',
        sealed,
        reason='01',
        password=_SENTINEL.encode(),
    )

    with pytest.raises(PacRejected) as raised:
        await _scripted(client).request_cancellation(request)

    assert raised.value.code
    assert _SENTINEL not in f'{raised.value} {raised.value.code} {raised.value.message}'
    assert client.received == []


# ── SAT status ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize('state', list(libsat.SatState))
@pytest.mark.parametrize('cancellability', list(libsat.Cancellability))
async def test_every_state_and_cancellability_maps(
    state: libsat.SatState, cancellability: libsat.Cancellability
) -> None:
    status = libsat.SatStatus(
        state=state,
        cancellability=cancellability,
        cancellation=libsat.CancellationStatus.NONE,
    )
    client = ScriptedClient(status=status)

    result = await _scripted(client).query_status('AAA010101AAA', 'XAXX010101000', 'U', Decimal(1))

    assert result == SatStatus(
        state=state.value, cancellable=cancellability.value, cancellation='none'
    )
    assert client.received == [('AAA010101AAA', 'XAXX010101000', 'U', Decimal(1))]


@pytest.mark.parametrize('cancellation', list(libsat.CancellationStatus))
async def test_every_cancellation_status_maps(cancellation: libsat.CancellationStatus) -> None:
    status = libsat.SatStatus(
        state=libsat.SatState.VALID,
        cancellability=libsat.Cancellability.WITH_ACCEPTANCE,
        cancellation=cancellation,
    )

    result = await _scripted(ScriptedClient(status=status)).query_status(
        'AAA010101AAA', 'XAXX010101000', 'U', Decimal(1)
    )

    assert result.cancellation == cancellation.value


async def test_the_simulated_status_follows_the_simulator() -> None:
    provider, _ = _simulated()
    sealed = _sealed()
    stamp = await provider.stamp(sealed)
    issuer, recipient, total = _identity(sealed)

    before = await provider.query_status(issuer, recipient, stamp.uuid, total)
    await provider.request_cancellation(_request(stamp.uuid, sealed))
    after = await provider.query_status(issuer, recipient, stamp.uuid, total)

    assert before == SatStatus(state='valid', cancellable='without_acceptance', cancellation='none')
    assert (after.state, after.cancellation) == ('cancelled', 'accepted')


# ── Errors ────────────────────────────────────────────────────────────────────


def _assert_clean(error: BaseException) -> None:
    assert error.__cause__ is None
    assert error.__suppress_context__ is True


@pytest.mark.parametrize('operation', ['stamp', 'cancel', 'status'])
@pytest.mark.parametrize(
    ('library_error', 'expected'),
    [
        (lib.PacRejected('CFDI40999', 'Rechazo'), PacRejected),
        (lib.PacUnavailable(), PacUnavailable),
        (lib.PacTimeout(), PacTimeout),
        (libsat.SatRejected('SAT-ANSWER', 'Respuesta desconocida'), PacRejected),
        (libsat.SatUnavailable(), PacUnavailable),
        (libsat.SatTimeout(), PacTimeout),
    ],
)
async def test_library_errors_map_to_the_contracts(
    operation: str, library_error: Exception, expected: type[Exception]
) -> None:
    sealed = _sealed()
    provider = _scripted(ScriptedClient(error=library_error))
    calls = {
        'stamp': lambda: provider.stamp(sealed),
        'cancel': lambda: provider.request_cancellation(
            _request('11111111-2222-3333-4444-555555555555', sealed)
        ),
        'status': lambda: provider.query_status('AAA010101AAA', 'XAXX010101000', 'U', Decimal(1)),
    }

    with pytest.raises(expected) as raised:
        await calls[operation]()

    _assert_clean(raised.value)
    if expected is PacRejected:
        assert (raised.value.code, raised.value.message) == (
            library_error.code,
            library_error.message,
        )


def test_the_repr_hides_the_pac_password() -> None:
    settings = lib.MultifacturasSettings(user='user', password=_SENTINEL, production=False)
    provider = CfdiPacProvider(
        lib.MultifacturasPac(settings),
        libsat.SatConsultaClient(),
        name='multifacturas',
        resolves_by_resubmitting=True,
    )

    assert _SENTINEL not in repr(provider)
