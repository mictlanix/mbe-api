"""The simulated PAC's controls (spec 039, research R7): choose what the simulator answers next,
and answer a pending cancellation as the receiver. Dev and test only: every route is
`request.route_not_found` unless `PAC_PROVIDER=simulated`."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import CurrentUser, require_privilege
from app.core.error_codes import ErrorCode
from app.core.errors import DomainError, not_found
from app.db.session import get_db
from app.enums import AccessRight, SystemObject
from app.models.fiscal import FiscalDocument
from app.schemas.fiscal import PacSimulationResponse, PacSimulationUpdate, ReceiverAnswer
from app.services import fiscal_document_service
from app.services.pac import get_pac_provider
from app.services.pac.simulated import Keys, SimulatedPac


def require_simulator() -> None:
    """404 as an unknown route answers, checked on each request before authentication."""
    if settings.pac_provider != 'simulated':
        raise DomainError(ErrorCode.REQUEST_ROUTE_NOT_FOUND, 'Not Found')


router = APIRouter(dependencies=[Depends(require_simulator)])

_UPDATE = require_privilege(SystemObject.FISCAL_DOCUMENTS, AccessRight.UPDATE)


async def _keys(db: AsyncSession, fiscal_document_id: int) -> Keys:
    """How the simulator knows a sealed or later document (research R6)."""
    document = await fiscal_document_service.get_document(db, fiscal_document_id)
    if document is None:
        raise not_found(FiscalDocument, fiscal_document_id)
    if document.status == 'draft':
        raise DomainError(ErrorCode.FISCAL_DOCUMENT_NOT_SEALED, 'Fiscal document is not sealed')
    return (document.stamp_uuid, document.issuer, document.batch or '', document.serial)


@router.get('/fiscal-documents/{fiscal_document_id}', response_model=PacSimulationResponse)
async def get_simulation(
    fiscal_document_id: int,
    _: CurrentUser = Depends(_UPDATE),
    pac: SimulatedPac = Depends(get_pac_provider),
    db: AsyncSession = Depends(get_db),
) -> PacSimulationResponse:
    """The simulator's record of the document."""
    keys = await _keys(db, fiscal_document_id)
    return PacSimulationResponse.model_validate(await pac.get_record(keys))


@router.put('/fiscal-documents/{fiscal_document_id}', response_model=PacSimulationResponse)
async def set_simulation(
    fiscal_document_id: int,
    data: PacSimulationUpdate,
    _: CurrentUser = Depends(_UPDATE),
    pac: SimulatedPac = Depends(get_pac_provider),
    db: AsyncSession = Depends(get_db),
) -> PacSimulationResponse:
    """The outcome of the document's next stamp or cancellation call only."""
    keys = await _keys(db, fiscal_document_id)
    await pac.set_next(
        keys,
        stamp=data.stamp,
        cancellation=data.cancellation,
        pac_code=data.pac_code,
        pac_message=data.pac_message,
    )
    return PacSimulationResponse.model_validate(await pac.get_record(keys))


@router.post(
    '/fiscal-documents/{fiscal_document_id}/receiver', response_model=PacSimulationResponse
)
async def answer_as_receiver(
    fiscal_document_id: int,
    data: ReceiverAnswer,
    _: CurrentUser = Depends(_UPDATE),
    pac: SimulatedPac = Depends(get_pac_provider),
    db: AsyncSession = Depends(get_db),
) -> PacSimulationResponse:
    """The receiver accepts, rejects or lets expire the document's pending cancellation."""
    keys = await _keys(db, fiscal_document_id)
    await pac.answer_as_receiver(keys, data.answer)
    return PacSimulationResponse.model_validate(await pac.get_record(keys))
