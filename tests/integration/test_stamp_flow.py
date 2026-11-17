"""Stamping on real tables, with the simulator and with a second provider (spec 039, US1, SC-006).

`invoicing`, `receipts` and `notes` each seed the same certificate and issuer, so each test uses
one of them.
"""

import logging

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.deps import CurrentUser
from app.models.fiscal import FiscalDocument, PacSimulation
from app.models.incidence import Incidence
from app.schemas.fiscal import FiscalDocumentResponse, InvoiceLineUpdate
from app.services import fiscal_document_service as fds
from app.services import fiscal_stamping
from app.services import invoice_service as svc
from app.services.pac.provider import SIMULATED_PAC_RFC, PacProvider, PacTimeout
from app.services.pac.simulated import SimulatedPac
from tests.fakes.pac import SCRIPTED_PAC_RFC, ScriptedPac
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
