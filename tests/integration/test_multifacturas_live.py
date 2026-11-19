"""Live checks against Multifacturas' test mode and SAT's CFDI status service (spec 040, R8).

Opt-in: each part is skipped unless its variables are set, so the default suite stays offline.
- `MULTIFACTURAS_USER`, `MULTIFACTURAS_PASSWORD` (the library's own names), with
  `SAT_TEST_CSD_DIR` (a folder holding SAT's public test CSD, `eku9003173c9.cer` and `.key`, as
  mictlanix/cfdi's `tests/fixtures/csd` does) and `SAT_TEST_CSD_PASSWORD` (the one SAT publishes
  with it): an invoice issued an hour ago is sealed with that CSD, stamped twice in test mode, and
  a cancellation's answer is recorded. No CSD material is kept in this repository (FR-007).
- `SAT_LIVE_VALID`, `SAT_LIVE_CANCELLED`, each `issuer|recipient|uuid|total`: two CFDIs the current
  system issued, read from SAT's status service.

The variables' values are never printed.
"""

import os
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from mictlanix.cfdi.pac import MultifacturasPac, MultifacturasSettings
from mictlanix.cfdi.sat import SatConsultaClient

from app.core.clock import local_now
from app.services import cfdi_invoice as cfdi
from app.services.pac.adapter import CfdiPacProvider
from app.services.pac.provider import CancellationRequest, Csd, PacRejected
from tests.unit.test_cfdi_invoice import _fixture_data
from tests.unit.test_pac_adapter import _identity

_PAC = (
    'MULTIFACTURAS_USER',
    'MULTIFACTURAS_PASSWORD',
    'SAT_TEST_CSD_DIR',
    'SAT_TEST_CSD_PASSWORD',
)
# SAT's public test CSD for EKU9003173C9 (valid 2023-05-18 to 2027-05-18).
_CERTIFICATE_NUMBER = '30001000000500003416'
_SAT = ('SAT_LIVE_VALID', 'SAT_LIVE_CANCELLED')


def _missing(names: tuple[str, ...]) -> bool:
    return not all(os.environ.get(name) for name in names)


def _csd() -> Csd:
    folder = Path(os.environ['SAT_TEST_CSD_DIR'])
    return Csd(
        certificate=(folder / 'eku9003173c9.cer').read_bytes(),
        key=(folder / 'eku9003173c9.key').read_bytes(),
        password=os.environ['SAT_TEST_CSD_PASSWORD'].encode('utf-8'),
    )


def _sealed_now() -> str:
    """The fixture invoice of EKU9003173C9, issued an hour ago with a folio of its own."""
    issued = local_now().replace(microsecond=0) - timedelta(hours=1)
    csd = _csd()
    data = replace(_fixture_data(), issued=issued, batch='LIVE', serial=int(f'{issued:%H%M%S}'))
    return cfdi.seal(
        data,
        certificate_number=_CERTIFICATE_NUMBER,
        certificate_data=csd.certificate,
        key_data=csd.key,
        key_password=csd.password,
    )


def _provider() -> CfdiPacProvider:
    return CfdiPacProvider(
        MultifacturasPac(
            MultifacturasSettings(
                user=os.environ['MULTIFACTURAS_USER'],
                password=os.environ['MULTIFACTURAS_PASSWORD'],
                production=False,
            )
        ),
        SatConsultaClient(),
        name='multifacturas',
        resolves_by_resubmitting=True,
    )


_PAC_REASON = 'set ' + ', '.join(_PAC)


@pytest.mark.skipif(_missing(_PAC), reason=_PAC_REASON)
async def test_test_mode_answers_a_resubmission_with_the_same_stamp() -> None:
    """Assumption A1: the PAC deduplicates by sealed document."""
    provider = _provider()
    sealed = _sealed_now()

    first = await provider.stamp(sealed)
    again = await provider.stamp(sealed)

    assert first.uuid == again.uuid
    assert first.provider_rfc and first.provider_rfc != 'SIM010101SI0'


@pytest.mark.skipif(_missing(_PAC), reason=_PAC_REASON)
async def test_test_mode_answers_a_cancellation(capsys: pytest.CaptureFixture) -> None:
    """Test stamps never reach SAT, so the expected answer is a rejection with SAT's 205. It is
    recorded rather than asserted: record what arrives in quickstart.md §2."""
    provider = _provider()
    sealed = _sealed_now()
    stamp = await provider.stamp(sealed)
    issuer, recipient, total = _identity(sealed)
    request = CancellationRequest(
        issuer_rfc=issuer,
        recipient_rfc=recipient,
        uuid=stamp.uuid,
        total=total,
        reason='02',
        substitution=None,
        csd=_csd(),
    )

    try:
        answer = await provider.request_cancellation(request)
        outcome = f'answered {answer.value}'
    except PacRejected as error:
        outcome = f'rejected {error.code}'

    with capsys.disabled():
        print(f'\nMultifacturas test mode, cancellation with reason 02: {outcome}')


def _parties(name: str) -> tuple[str, str, str, Decimal]:
    issuer, recipient, uuid, total = os.environ[name].split('|')
    return issuer, recipient, uuid, Decimal(total)


@pytest.mark.skipif(_missing(_SAT), reason='set SAT_LIVE_VALID and SAT_LIVE_CANCELLED')
@pytest.mark.parametrize(('name', 'state'), [(_SAT[0], 'valid'), (_SAT[1], 'cancelled')])
async def test_sats_status_service_is_read(name: str, state: str) -> None:
    provider = CfdiPacProvider(
        None,  # type: ignore[arg-type]
        SatConsultaClient(),
        name='sat',
        resolves_by_resubmitting=True,
    )

    status = await provider.query_status(*_parties(name))

    assert status.state == state
