"""Stamping on real tables, with the simulator and with a second provider (spec 039, US1, SC-006).

`invoicing`, `receipts` and `notes` each seed the same certificate and issuer, so each test uses
one of them.
"""

import asyncio
import logging
from datetime import timedelta

import pytest
from fastapi import HTTPException
from mictlanix.cfdi import pac as lib
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.deps import CurrentUser
from app.models.fiscal import FiscalDocument, PacSimulation
from app.models.incidence import Incidence
from app.schemas.fiscal import FiscalDocumentResponse, InvoiceLineUpdate
from app.services import fiscal_document_service as fds
from app.services import fiscal_stamping
from app.services import invoice_service as svc
from app.services.pac.provider import SIMULATED_PAC_RFC, PacProvider, PacRejected, PacTimeout
from app.services.pac.simulated import SimulatedPac
from tests.fakes.pac import (
    MULTIFACTURAS_RFC,
    SCRIPTED_PAC_RFC,
    ScriptedPac,
    scripted_multifacturas,
)
from tests.integration.test_credit_note_flow import (  # noqa: F401
    CreditNotes,
    _invoice,
    _lines,
    notes,
)
from tests.integration.test_invoice_flow import Invoicing, _ready, invoicing  # noqa: F401
from tests.integration.test_receipt_flow import Receipts, _ppd_invoice, receipts  # noqa: F401

pytestmark = pytest.mark.usefixtures('seeded')

_STAMP_CALL = 'pac simulator: stamp call'


@pytest.fixture(params=['simulated', 'scripted'])
def pac(request: pytest.FixtureRequest, sessions: async_sessionmaker) -> PacProvider:
    if request.param == 'simulated':
        return SimulatedPac(session_factory=sessions)
    return ScriptedPac()


def _stamp_calls(pac: PacProvider, caplog: pytest.LogCaptureFixture) -> int:
    """How often the PAC was asked to stamp: the simulator logs each call, the fake counts them."""
    if isinstance(pac, ScriptedPac):
        return pac.calls['stamp']
    return sum(_STAMP_CALL in record.getMessage() for record in caplog.records)


async def _stamp(db: AsyncSession, current: CurrentUser, document_id: int, pac: PacProvider):  # noqa: ANN202
    return await fiscal_stamping.stamp(db, document_id, pac, current.employee_id)


async def _code(call, code: int, error_code: str) -> None:  # noqa: ANN001
    with pytest.raises(HTTPException) as caught:
        await call
    assert caught.value.status_code == code, caught.value.detail
    assert caught.value.code == error_code


async def _assert_issued(
    db: AsyncSession,
    current: CurrentUser,
    document_id: int,
    pac: PacProvider,
    sealed_xml: str,
    caplog: pytest.LogCaptureFixture,
) -> FiscalDocument:
    db.expire_all()
    document = await fds.get_document(db, document_id)
    found = await pac.find_stamp(sealed_xml)
    assert found is not None
    assert document.status == 'issued'
    assert document.stamp_uuid == found.uuid
    assert document.stamped == found.stamped_at
    assert document.authority_digital_seal == found.sat_seal
    assert document.authority_certificate_number == found.sat_certificate
    simulated = isinstance(pac, SimulatedPac)
    assert document.rfc_pac == (SIMULATED_PAC_RFC if simulated else SCRIPTED_PAC_RFC)
    assert await fds.get_xml(db, document_id) == found.xml
    assert document.stamping_since is None

    detail = FiscalDocumentResponse.model_validate(document)
    assert detail.stamp is not None
    assert (detail.stamp.uuid, detail.stamp.simulated) == (found.uuid, simulated)
    assert detail.stamping_since is None

    reasons = (
        await db.execute(select(Incidence.comment).where(Incidence.instance_id == document_id))
    ).scalars()
    assert f'CFDI stamped {found.uuid}' in list(reasons)

    # A repeat returns the document unchanged, without calling the PAC again.
    calls = _stamp_calls(pac, caplog)
    await _stamp(db, current, document_id, pac)
    assert _stamp_calls(pac, caplog) == calls == 1
    db.expire_all()
    assert (await fds.get_document(db, document_id)).stamp_uuid == found.uuid
    return document


async def test_stamp_issues_an_invoice(
    invoicing: Invoicing,  # noqa: F811
    pac: PacProvider,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    db, current = invoicing.db, invoicing.current
    sealed = await invoicing.confirm(await _ready(invoicing))
    sealed_xml = await fds.get_xml(db, sealed.fiscal_document_id)

    await _stamp(db, current, sealed.fiscal_document_id, pac)

    document = await _assert_issued(db, current, sealed.fiscal_document_id, pac, sealed_xml, caplog)
    # Issued: it can be neither reopened nor edited.
    await _code(svc.reopen(db, current, document), 409, 'fiscal_document.not_sealed')
    document = await invoicing.load(document)
    await _code(
        svc.update_line(
            db,
            current,
            document,
            document.lines[0].fiscal_document_detail_id,
            InvoiceLineUpdate(quantity=1),
        ),
        409,
        'fiscal_document.not_draft',
    )


async def test_stamp_issues_a_payment_receipt(
    receipts: Receipts,  # noqa: F811
    pac: PacProvider,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    db, current = receipts.db, receipts.current
    invoice = await _ppd_invoice(db)
    sealed = await receipts.confirm(await receipts.ready(invoice, '1000.00'))
    sealed_xml = await fds.get_xml(db, sealed.fiscal_document_id)

    await _stamp(db, current, sealed.fiscal_document_id, pac)

    await _assert_issued(db, current, sealed.fiscal_document_id, pac, sealed_xml, caplog)


async def test_stamp_issues_a_credit_note(
    notes: CreditNotes,  # noqa: F811
    pac: PacProvider,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    db, current = notes.db, notes.current
    invoice = await _invoice(db)
    source = (await _lines(db, invoice))[0]
    draft = await notes.copy(await notes.create(invoice), source.fiscal_document_detail_id)
    sealed = await notes.confirm(draft)
    sealed_xml = await fds.get_xml(db, sealed.fiscal_document_id)

    await _stamp(db, current, sealed.fiscal_document_id, pac)

    await _assert_issued(db, current, sealed.fiscal_document_id, pac, sealed_xml, caplog)


async def test_after_a_timeout_the_document_is_stamping_and_never_stamped_twice(
    invoicing: Invoicing,  # noqa: F811
    pac: PacProvider,
    sessions: async_sessionmaker,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    db, current = invoicing.db, invoicing.current
    sealed = await invoicing.confirm(await _ready(invoicing))
    document_id = sealed.fiscal_document_id
    if isinstance(pac, SimulatedPac):
        keys = (None, sealed.issuer, sealed.batch or '', sealed.serial)
        await pac.set_next(keys, stamp='timeout_stamped')
    else:
        pac.queue('stamp', PacTimeout())

    with pytest.raises(HTTPException) as caught:
        await _stamp(db, current, document_id, pac)
    assert (caught.value.code, caught.value.params) == ('pac.timeout', {'state': 'stamping'})

    document = await invoicing.load(document_id)
    assert document.status == 'stamping'
    assert document.stamping_since is not None
    await _code(_stamp(db, current, document_id, pac), 409, 'fiscal_document.stamping')
    assert _stamp_calls(pac, caplog) == 1
    if isinstance(pac, SimulatedPac):
        async with sessions() as other:
            stamped = select(func.count()).where(PacSimulation.uuid.is_not(None))
            assert (await other.execute(stamped)).scalar_one() == 1

    # Stamping: it can be neither reopened nor edited.
    document = await invoicing.load(document_id)
    await _code(svc.reopen(db, current, document), 409, 'fiscal_document.not_sealed')
    document = await invoicing.load(document_id)
    await _code(
        svc.update_line(
            db,
            current,
            document,
            document.lines[0].fiscal_document_detail_id,
            InvoiceLineUpdate(quantity=1),
        ),
        409,
        'fiscal_document.not_draft',
    )


async def test_a_rejected_document_stays_sealed_and_can_be_fixed_and_stamped(
    invoicing: Invoicing,  # noqa: F811
    pac: PacProvider,
) -> None:
    """US1 scenario 4: the PAC's code and message come back, the document stays sealed, and the
    clerk reopens it, fixes it, seals it again and stamps it (T051)."""
    db, current = invoicing.db, invoicing.current
    sealed = await invoicing.confirm(await _ready(invoicing))
    document_id = sealed.fiscal_document_id
    if isinstance(pac, SimulatedPac):
        keys = (None, sealed.issuer, sealed.batch or '', sealed.serial)
        await pac.set_next(keys, stamp='rejected', pac_code='CFDI40102', pac_message='Sello')
    else:
        pac.queue('stamp', PacRejected('CFDI40102', 'Sello'))

    with pytest.raises(HTTPException) as caught:
        await _stamp(db, current, document_id, pac)
    assert (caught.value.status_code, caught.value.code) == (422, 'pac.rejected')
    assert caught.value.params == {'pac_code': 'CFDI40102', 'pac_message': 'Sello'}

    document = await invoicing.load(document_id)
    assert (document.status, document.stamping_since) == ('sealed', None)
    await svc.reopen(db, current, document)
    document = await invoicing.load(document_id)
    assert document.status == 'draft'
    await svc.update_line(
        db,
        current,
        document,
        document.lines[0].fiscal_document_detail_id,
        InvoiceLineUpdate(quantity=1),
    )
    await invoicing.confirm(await invoicing.load(document_id))
    await _stamp(db, current, document_id, pac)

    document = await invoicing.load(document_id)
    assert document.status == 'issued'
    assert document.lines[0].quantity == 1


# ── Refresh (US3, SC-004), T033 ───────────────────────────────────────────────


async def _timed_out(invoicing: Invoicing, pac: PacProvider, outcome: str) -> tuple[int, str]:  # noqa: F811
    """A sealed invoice whose stamp timed out; `outcome` says whether the PAC stamped it."""
    db, current = invoicing.db, invoicing.current
    sealed = await invoicing.confirm(await _ready(invoicing))
    document_id = sealed.fiscal_document_id
    sealed_xml = await fds.get_xml(db, document_id)
    if isinstance(pac, SimulatedPac):
        keys = (None, sealed.issuer, sealed.batch or '', sealed.serial)
        await pac.set_next(keys, stamp=outcome)
    else:
        pac.queue('stamp', PacTimeout())
        if outcome == 'timeout_stamped':
            pac.queue('find_stamp', pac._make_stamp(sealed_xml))
    with pytest.raises(HTTPException) as caught:
        await _stamp(db, current, document_id, pac)
    assert caught.value.code == 'pac.timeout'
    return document_id, sealed_xml


async def _set_marker_back(db: AsyncSession, document_id: int, minutes: int) -> None:
    db.expire_all()
    document = await db.get(FiscalDocument, document_id)
    document.stamping_since -= timedelta(minutes=minutes)
    await db.commit()


async def _comments(db: AsyncSession, document_id: int) -> list[str]:
    query = (
        select(Incidence.comment)
        .where(Incidence.instance_id == document_id)
        .order_by(Incidence.incidence_id)
    )
    return list((await db.execute(query)).scalars())


async def test_refresh_finds_a_stamp_that_timed_out(
    invoicing: Invoicing,  # noqa: F811
    pac: PacProvider,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    db, current = invoicing.db, invoicing.current
    document_id, sealed_xml = await _timed_out(invoicing, pac, 'timeout_stamped')
    if isinstance(pac, SimulatedPac):
        found = await pac.find_stamp(sealed_xml)
    else:
        found = pac.queues['find_stamp'][0]

    await fiscal_stamping.refresh(db, document_id, pac, current.employee_id)

    db.expire_all()
    document = await fds.get_document(db, document_id)
    assert document.status == 'issued'
    assert document.stamping_since is None
    assert document.stamp_uuid == found.uuid
    assert await fds.get_xml(db, document_id) == found.xml
    assert f'CFDI stamp found {document.stamp_uuid}' in await _comments(db, document_id)
    assert _stamp_calls(pac, caplog) == 1


async def test_refresh_seals_again_a_stamp_not_found_past_the_grace(
    invoicing: Invoicing,  # noqa: F811
    pac: PacProvider,
) -> None:
    db, current = invoicing.db, invoicing.current
    document_id, _sealed_xml = await _timed_out(invoicing, pac, 'timeout_not_stamped')

    # Within the grace it stays stamping.
    await fiscal_stamping.refresh(db, document_id, pac, current.employee_id)
    db.expire_all()
    assert (await fds.get_document(db, document_id)).status == 'stamping'

    await _set_marker_back(db, document_id, 6)
    await fiscal_stamping.refresh(db, document_id, pac, current.employee_id)

    db.expire_all()
    assert (await fds.get_document(db, document_id)).status == 'sealed'
    assert 'CFDI stamp not found, sealed again' in await _comments(db, document_id)
    await _stamp(db, current, document_id, pac)
    db.expire_all()
    assert (await fds.get_document(db, document_id)).status == 'issued'


async def test_a_stamp_answer_after_the_reset_is_still_stored(
    invoicing: Invoicing,  # noqa: F811
    pac: PacProvider,
) -> None:
    db, current = invoicing.db, invoicing.current
    document_id, sealed_xml = await _timed_out(invoicing, pac, 'timeout_not_stamped')
    await _set_marker_back(db, document_id, 6)
    await fiscal_stamping.refresh(db, document_id, pac, current.employee_id)
    late = ScriptedPac()._make_stamp(sealed_xml)

    await fiscal_stamping._store_stamp(
        db, document_id, sealed_xml, late, current.employee_id, reason=f'CFDI stamped {late.uuid}'
    )

    db.expire_all()
    document = await fds.get_document(db, document_id)
    assert (document.status, document.stamp_uuid) == ('issued', late.uuid)
    assert await fds.get_xml(db, document_id) == late.xml


# ── Multifacturas through the adapter (spec 040, US1), T010 ───────────────────


async def _assert_issued_by_multifacturas(db: AsyncSession, document_id: int, client) -> None:  # noqa: ANN001
    db.expire_all()
    document = await fds.get_document(db, document_id)
    assert document.status == 'issued'
    assert document.stamping_since is None
    assert document.rfc_pac == MULTIFACTURAS_RFC
    detail = FiscalDocumentResponse.model_validate(document)
    assert (detail.stamp.uuid, detail.stamp.simulated) == (document.stamp_uuid, False)
    stored = await fds.get_xml(db, document_id)
    assert f'UUID="{document.stamp_uuid}"' in stored
    assert f'RfcProvCertif="{MULTIFACTURAS_RFC}"' in stored
    assert client.calls['stamp'] == 1


async def test_multifacturas_issues_an_invoice(invoicing: Invoicing) -> None:  # noqa: F811
    provider, client = scripted_multifacturas()
    db, current = invoicing.db, invoicing.current
    sealed = await invoicing.confirm(await _ready(invoicing))

    await _stamp(db, current, sealed.fiscal_document_id, provider)

    await _assert_issued_by_multifacturas(db, sealed.fiscal_document_id, client)


async def test_multifacturas_issues_a_payment_receipt(receipts: Receipts) -> None:  # noqa: F811
    provider, client = scripted_multifacturas()
    db, current = receipts.db, receipts.current
    invoice = await _ppd_invoice(db)
    sealed = await receipts.confirm(await receipts.ready(invoice, '1000.00'))

    await _stamp(db, current, sealed.fiscal_document_id, provider)

    await _assert_issued_by_multifacturas(db, sealed.fiscal_document_id, client)


async def test_multifacturas_issues_a_credit_note(notes: CreditNotes) -> None:  # noqa: F811
    provider, client = scripted_multifacturas()
    db, current = notes.db, notes.current
    invoice = await _invoice(db)
    source = (await _lines(db, invoice))[0]
    draft = await notes.copy(await notes.create(invoice), source.fiscal_document_detail_id)
    sealed = await notes.confirm(draft)

    await _stamp(db, current, sealed.fiscal_document_id, provider)

    await _assert_issued_by_multifacturas(db, sealed.fiscal_document_id, client)


@pytest.mark.parametrize(
    ('error', 'status', 'code', 'params', 'state'),
    [
        (
            lib.PacRejected('CFDI40102', 'Sello'),
            422,
            'pac.rejected',
            {'pac_code': 'CFDI40102', 'pac_message': 'Sello'},
            'sealed',
        ),
        (lib.PacUnavailable(), 503, 'pac.unavailable', None, 'sealed'),
        (lib.PacTimeout(), 504, 'pac.timeout', {'state': 'stamping'}, 'stamping'),
    ],
    ids=['rejected', 'unavailable', 'timeout'],
)
async def test_multifacturas_failures_answer_with_their_codes(
    invoicing: Invoicing,  # noqa: F811
    error: Exception,
    status: int,
    code: str,
    params: dict | None,
    state: str,
) -> None:
    """US1 scenarios 2 to 4."""
    provider, client = scripted_multifacturas()
    client.queue('stamp', error)
    db, current = invoicing.db, invoicing.current
    sealed = await invoicing.confirm(await _ready(invoicing))

    with pytest.raises(HTTPException) as caught:
        await _stamp(db, current, sealed.fiscal_document_id, provider)

    assert (caught.value.status_code, caught.value.code) == (status, code)
    if params is not None:
        assert caught.value.params == params
    assert (await invoicing.load(sealed.fiscal_document_id)).status == state


# ── Resolving a timed-out stamp by resubmission (spec 040, US2, SC-003), T013 ─


async def _timed_out_with_multifacturas(invoicing: Invoicing, outcome: object):  # noqa: ANN202, F811
    provider, client = scripted_multifacturas()
    client.queue('stamp', outcome)
    db, current = invoicing.db, invoicing.current
    sealed = await invoicing.confirm(await _ready(invoicing))
    document_id = sealed.fiscal_document_id
    with pytest.raises(HTTPException) as caught:
        await _stamp(db, current, document_id, provider)
    assert caught.value.code == 'pac.timeout'
    return provider, client, document_id


@pytest.mark.parametrize('outcome', ['timeout_stamped', lib.PacTimeout()], ids=['did', 'did_not'])
async def test_a_timed_out_stamp_is_resolved_by_resubmission_with_one_uuid(
    invoicing: Invoicing,  # noqa: F811
    outcome: object,
) -> None:
    db, current = invoicing.db, invoicing.current
    provider, client, document_id = await _timed_out_with_multifacturas(invoicing, outcome)
    sealed_xml = await fds.get_xml(db, document_id)

    # Within the grace nothing is sent.
    await fiscal_stamping.refresh(db, document_id, provider, current.employee_id)
    assert client.calls['stamp'] == 1
    await _set_marker_back(db, document_id, 6)
    await fiscal_stamping.refresh(db, document_id, provider, current.employee_id)

    db.expire_all()
    document = await fds.get_document(db, document_id)
    assert (document.status, document.stamping_since) == ('issued', None)
    assert client.calls['stamp'] == 2 and client.calls['find_stamp'] == 0
    again = await client.stamp(sealed_xml.encode('utf-8'))
    assert document.stamp_uuid == again.timbre.UUID
    assert f'CFDI stamp resolved {document.stamp_uuid}' in await _comments(db, document_id)


async def test_two_resolutions_at_once_store_one_uuid(
    invoicing: Invoicing,  # noqa: F811
    sessions: async_sessionmaker,
) -> None:
    db, current = invoicing.db, invoicing.current
    provider, client, document_id = await _timed_out_with_multifacturas(
        invoicing, 'timeout_stamped'
    )
    await _set_marker_back(db, document_id, 6)

    async def resolve() -> None:
        async with sessions() as session:
            await fiscal_stamping.refresh(session, document_id, provider, current.employee_id)

    await asyncio.gather(resolve(), resolve(), return_exceptions=True)

    db.expire_all()
    document = await fds.get_document(db, document_id)
    assert document.status == 'issued'
    uuids = {
        line.split()[-1]
        for line in await _comments(db, document_id)
        if line.startswith('CFDI stamp resolved ')
    }
    assert uuids == {document.stamp_uuid}


async def test_a_rejected_resubmission_seals_the_document_again(
    invoicing: Invoicing,  # noqa: F811
) -> None:
    db, current = invoicing.db, invoicing.current
    provider, client, document_id = await _timed_out_with_multifacturas(invoicing, lib.PacTimeout())
    await _set_marker_back(db, document_id, 6)
    client.queue('stamp', lib.PacRejected('CFDI40102', 'Sello'))

    await _code(
        fiscal_stamping.refresh(db, document_id, provider, current.employee_id),
        422,
        'pac.rejected',
    )

    document = await invoicing.load(document_id)
    assert (document.status, document.stamping_since) == ('sealed', None)
