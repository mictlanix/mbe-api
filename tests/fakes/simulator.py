"""The CFDI library's simulated PAC as the integration tests use it (spec 040, research R10).

`simulated_provider(sessions)` is what `PAC_PROVIDER=simulated` builds, over the tests' database:
the adapter around a `SimulatedPac` on a `DatabaseSimulatorStore`. The `set_*` helpers choose the
next outcome for a document as the /pac-simulator routes do (contracts/simulator-controls.md).
"""

from collections.abc import Callable
from datetime import datetime

from mictlanix.cfdi import pac as lib
from mictlanix.cfdi.sat import SimulatedSatStatusClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.fiscal import FiscalDocument
from app.services import fiscal_document_service as fds
from app.services.pac.adapter import CfdiPacProvider
from app.services.pac.simulator_store import DatabaseSimulatorStore


def simulated_provider(
    sessions: async_sessionmaker, *, clock: Callable[[], datetime] | None = None
) -> CfdiPacProvider:
    """The adapter over the library's simulator on the tests' database. `provider.simulator` is the
    `SimulatedPac` itself, for its `configure_*` and `answer_as_receiver`."""
    simulator = lib.SimulatedPac(DatabaseSimulatorStore(sessions), clock=clock)
    provider = CfdiPacProvider(
        simulator,
        SimulatedSatStatusClient(simulator),
        name='simulator',
        resolves_by_resubmitting=False,
    )
    provider.simulator = simulator  # type: ignore[attr-defined]
    return provider


def is_simulated(pac: object) -> bool:
    return isinstance(pac, CfdiPacProvider) and pac.name == 'simulator'


async def set_stamp(
    pac: CfdiPacProvider,
    db: AsyncSession,
    document_id: int,
    outcome: str,
    *,
    code: str | None = None,
    message: str | None = None,
) -> None:
    """The outcome of the document's next stamp, keyed by its stored sealed XML."""
    xml = await fds.get_xml(db, document_id)
    await pac.simulator.configure_stamp(  # type: ignore[attr-defined]
        xml.encode('utf-8'), lib.StampOutcome(outcome), code=code, message=message
    )


async def set_cancellation(
    pac: CfdiPacProvider,
    db: AsyncSession,
    document_id: int,
    outcome: str,
    *,
    code: str | None = None,
    message: str | None = None,
) -> None:
    """The outcome of the next cancellation of the document's UUID."""
    db.expire_all()
    document = await db.get(FiscalDocument, document_id)
    await pac.simulator.configure_cancellation(  # type: ignore[attr-defined]
        document.stamp_uuid, lib.CancellationOutcome(outcome), code=code, message=message
    )


async def answer_as_receiver(
    pac: CfdiPacProvider, db: AsyncSession, document_id: int, answer: str
) -> None:
    db.expire_all()
    document = await db.get(FiscalDocument, document_id)
    await pac.simulator.answer_as_receiver(  # type: ignore[attr-defined]
        document.stamp_uuid, lib.ReceiverAnswer(answer)
    )
