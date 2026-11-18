"""The resolve job on real tables (spec 039, R12, SC-003, T035).

`run(provider, session_factory=sessions)` refreshes every stamping document and every open
cancellation in its own session, as the system employee.
"""

import datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.constants import SYSTEM_EMPLOYEE_ID
from app.core.deps import CurrentUser
from app.jobs import resolve_fiscal_documents as job
from app.models.fiscal import FiscalDocument
from app.models.incidence import Incidence
from app.models.product import Product
from app.services import employee_service, fiscal_stamping
from app.services import fiscal_document_service as fds
from app.services.pac.provider import (
    CancellationAnswer,
    PacProvider,
    PacTimeout,
    PacUnavailable,
    SatStatus,
)
from app.services.pac.simulated import SimulatedPac
from tests.fakes.pac import ScriptedPac
from tests.integration.seed import seed_cancellation_reasons
from tests.integration.test_receipt_flow import Receipts, _ppd_invoice, receipts  # noqa: F401

pytestmark = pytest.mark.usefixtures('seeded')


async def _ready(receipts: Receipts) -> None:  # noqa: F811
    await seed_cancellation_reasons(receipts.db)
    await receipts.db.execute(update(Product).where(Product.product_id == 1).values(key='01010101'))
    await receipts.db.commit()


async def _stamping(receipts: Receipts, pac: PacProvider) -> int:  # noqa: F811
    """A receipt whose stamp timed out after the PAC stamped it."""
    db, current = receipts.db, receipts.current
    invoice = await _ppd_invoice(db)
    sealed = await receipts.confirm(await receipts.ready(invoice, '1000.00'))
    document_id = sealed.fiscal_document_id
    if isinstance(pac, SimulatedPac):
        keys = (None, sealed.issuer, sealed.batch or '', sealed.serial)
        await pac.set_next(keys, stamp='timeout_stamped')
    else:
        pac.queue('stamp', PacTimeout())
        pac.queue('find_stamp', pac._make_stamp(await fds.get_xml(db, document_id)))
    with pytest.raises(HTTPException):
        await fiscal_stamping.stamp(db, document_id, pac, current.employee_id)
    return document_id


async def _pending(receipts: Receipts, pac: PacProvider) -> int:  # noqa: F811
    """A second invoice whose cancellation waits for the receiver."""
    db, current = receipts.db, receipts.current
    uuid = '0A1B2C3D-0000-4000-8000-000000000039'
    invoice = await _ppd_invoice(db, stamp_uuid=uuid, serial=11460)
    if isinstance(pac, SimulatedPac):
        document = await db.get(FiscalDocument, invoice)
        keys = (document.stamp_uuid, document.issuer, document.batch or '', document.serial)
        await pac.set_next(keys, cancellation='pending')
    else:
        pac.queue('request_cancellation', CancellationAnswer.PENDING)
    await fiscal_stamping.cancel(db, invoice, '02', None, pac, current.employee_id)
    return invoice


async def _status(db: AsyncSession, document_id: int) -> str:
    db.expire_all()
    return (await fds.get_document(db, document_id)).status


_LONG_AGO = datetime.datetime(2020, 1, 1)


async def _backdate(db: AsyncSession, updater: int, *document_ids: int) -> None:
    """Set the audit columns to values no flow writes, so a touch shows."""
    await db.execute(
        update(FiscalDocument)
        .where(FiscalDocument.fiscal_document_id.in_(document_ids))
        .values(updater=updater, modification_time=_LONG_AGO)
    )
    await db.commit()


async def _audit(db: AsyncSession, document_id: int) -> tuple[int, datetime.datetime]:
    db.expire_all()
    document = await db.get(FiscalDocument, document_id)
    return document.updater, document.modification_time


async def _updaters(db: AsyncSession, document_id: int, comment: str) -> list[int]:
    query = select(Incidence.updater).where(
        Incidence.instance_id == document_id, Incidence.comment.startswith(comment)
    )
    return list((await db.execute(query)).scalars())


@pytest.mark.parametrize('kind', ['simulated', 'scripted'])
async def test_one_run_resolves_a_stamping_and_a_pending_document(
    receipts: Receipts,  # noqa: F811
    sessions: async_sessionmaker,
    current_user: CurrentUser,
    kind: str,
) -> None:
    await _ready(receipts)
    db = receipts.db
    pac = SimulatedPac(session_factory=sessions) if kind == 'simulated' else ScriptedPac()
    stamping = await _stamping(receipts, pac)
    pending = await _pending(receipts, pac)
    if isinstance(pac, SimulatedPac):
        document = await db.get(FiscalDocument, pending)
        keys = (document.stamp_uuid, document.issuer, document.batch or '', document.serial)
        await pac.answer_as_receiver(keys, 'accept')
    else:
        pac.queue(
            'query_status',
            SatStatus(state='cancelled', cancellable='with_acceptance', cancellation='accepted'),
        )
    assert (await _status(db, stamping), await _status(db, pending)) == (
        'stamping',
        'cancellation_pending',
    )
    await _backdate(db, current_user.employee_id, stamping, pending)

    assert await job.run(pac, session_factory=sessions) == 0

    assert (await _status(db, stamping), await _status(db, pending)) == ('issued', 'cancelled')
    # The job is no one's edit: the documents keep their last updater and modification time.
    for document_id in (stamping, pending):
        assert await _audit(db, document_id) == (current_user.employee_id, _LONG_AGO)
    assert await _updaters(db, stamping, 'CFDI stamp found') == [SYSTEM_EMPLOYEE_ID]
    assert await _updaters(db, pending, 'CFDI cancelled') == [SYSTEM_EMPLOYEE_ID]
    # The requests the user made stay theirs.
    assert await _updaters(db, pending, 'Cancellation requested') == [current_user.employee_id]


async def test_a_failing_document_does_not_stop_the_others(
    receipts: Receipts,  # noqa: F811
    sessions: async_sessionmaker,
    capsys: pytest.CaptureFixture,
) -> None:
    await _ready(receipts)
    db = receipts.db
    pac = ScriptedPac()
    stamping = await _stamping(receipts, pac)
    pending = await _pending(receipts, pac)
    pac.queue('query_status', PacUnavailable())

    assert await job.run(pac, session_factory=sessions) == 1

    assert (await _status(db, stamping), await _status(db, pending)) == (
        'issued',
        'cancellation_pending',
    )
    assert f'fiscal document {pending}: pac.unavailable' in capsys.readouterr().out


def test_main_refuses_the_simulator_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(job.settings, 'pac_provider', 'simulated')
    monkeypatch.setattr(job.settings, 'app_env', 'production')

    with pytest.raises(RuntimeError, match='app_env=production'):
        job.main()


async def test_user_actions_touch_the_document(
    receipts: Receipts,  # noqa: F811
    current_user: CurrentUser,
) -> None:
    """Stamp, cancel and refresh by a user set the updater and modification time (the job's
    counterpart is above)."""
    await _ready(receipts)
    db = receipts.db
    other = (await employee_service.ensure_system_employee(db)).employee_id
    pac = ScriptedPac()
    stamping = await _stamping(receipts, pac)
    await _backdate(db, other, stamping)
    await fiscal_stamping.refresh(db, stamping, pac, current_user.employee_id)
    updater, touched = await _audit(db, stamping)
    assert (await _status(db, stamping), updater) == ('issued', current_user.employee_id)
    assert touched > _LONG_AGO

    pac.queue('request_cancellation', CancellationAnswer.PENDING)
    await _backdate(db, other, stamping)
    await fiscal_stamping.cancel(db, stamping, '02', None, pac, current_user.employee_id)
    updater, touched = await _audit(db, stamping)
    assert (await _status(db, stamping), updater) == (
        'cancellation_pending',
        current_user.employee_id,
    )
    assert touched > _LONG_AGO

    pac.queue(
        'query_status',
        SatStatus(state='cancelled', cancellable='with_acceptance', cancellation='accepted'),
    )
    await _backdate(db, other, stamping)
    await fiscal_stamping.refresh(db, stamping, pac, current_user.employee_id)
    updater, touched = await _audit(db, stamping)
    assert (await _status(db, stamping), updater) == ('cancelled', current_user.employee_id)
    assert touched > _LONG_AGO
