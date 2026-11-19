"""The simulated PAC's controls (spec 039 R7, spec 040 R12): choose what the CFDI library's
simulator answers next, and answer a pending cancellation as the receiver. Dev and test only:
every route is `request.route_not_found` unless `PAC_PROVIDER=simulated`."""

from collections.abc import Awaitable

from fastapi import APIRouter, Depends
from mictlanix.cfdi.pac import CancellationOutcome, SimulatedPac, StampOutcome
from mictlanix.cfdi.pac import ReceiverAnswer as LibraryReceiverAnswer
from mictlanix.cfdi.sat import SimulatedSatStatusClient
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
from app.services.pac import get_pac_simulator
from app.services.pac.adapter import library_errors
from app.services.pac.provider import PacRejected, PacTimeout, PacUnavailable

# The library's code when no cancellation is pending for the UUID.
_NOTHING_PENDING = 'SIM01'


def require_simulator() -> None:
    """404 as an unknown route answers, checked on each request before authentication."""
    if settings.pac_provider != 'simulated':
        raise DomainError(ErrorCode.REQUEST_ROUTE_NOT_FOUND, 'Not Found')


router = APIRouter(dependencies=[Depends(require_simulator)])

_UPDATE = require_privilege(SystemObject.FISCAL_DOCUMENTS, AccessRight.UPDATE)


async def _document(db: AsyncSession, fiscal_document_id: int) -> FiscalDocument:
    """A sealed or later document, which the simulator can know."""
    document = await fiscal_document_service.get_document(db, fiscal_document_id)
    if document is None:
        raise not_found(FiscalDocument, fiscal_document_id)
    if document.status == 'draft':
        raise DomainError(ErrorCode.FISCAL_DOCUMENT_NOT_SEALED, 'Fiscal document is not sealed')
    return document


async def _call(operation: Awaitable, state: str) -> object:
    """A library call, its errors answered with spec 036's PAC codes."""
    try:
        with library_errors():
            return await operation
    except PacRejected as error:
        raise DomainError(
            ErrorCode.PAC_REJECTED,
            'The simulated PAC refused the request',
            {'pac_code': error.code, 'pac_message': error.message},
        ) from None
    except PacUnavailable:
        raise DomainError(ErrorCode.PAC_UNAVAILABLE, 'The simulated PAC is unavailable') from None
    except PacTimeout:
        raise DomainError(
            ErrorCode.PAC_TIMEOUT, 'The simulated PAC did not answer', {'state': state}
        ) from None


async def _view(
    db: AsyncSession, pac: SimulatedPac, document: FiscalDocument
) -> PacSimulationResponse:
    """The simulated SAT's status of the document; `not_found` until it has a UUID."""
    if document.stamp_uuid is None:
        return PacSimulationResponse(uuid=None, sat_status='not_found', cancellation_status='none')
    xml = await fiscal_document_service.get_xml(db, document.fiscal_document_id)
    total = fiscal_document_service.xml_total(xml) or document.total
    status = await _call(
        SimulatedSatStatusClient(pac).query_status(
            document.issuer, document.recipient, document.stamp_uuid, total
        ),
        document.status,
    )
    return PacSimulationResponse(
        uuid=document.stamp_uuid,
        sat_status=status.state.value,
        cancellation_status=status.cancellation.value,
    )


@router.get('/fiscal-documents/{fiscal_document_id}', response_model=PacSimulationResponse)
async def get_simulation(
    fiscal_document_id: int,
    _: CurrentUser = Depends(_UPDATE),
    pac: SimulatedPac = Depends(get_pac_simulator),
    db: AsyncSession = Depends(get_db),
) -> PacSimulationResponse:
    """The simulated SAT's view of the document."""
    return await _view(db, pac, await _document(db, fiscal_document_id))


@router.put('/fiscal-documents/{fiscal_document_id}', response_model=PacSimulationResponse)
async def set_simulation(
    fiscal_document_id: int,
    data: PacSimulationUpdate,
    _: CurrentUser = Depends(_UPDATE),
    pac: SimulatedPac = Depends(get_pac_simulator),
    db: AsyncSession = Depends(get_db),
) -> PacSimulationResponse:
    """The outcome of the document's next stamp, or of its UUID's next cancellation, only."""
    document = await _document(db, fiscal_document_id)
    if data.stamp is not None and document.status == 'stamping':
        # Another process may be storing this document's stamp in the simulator, whose instances
        # don't lock each other: writing its record now could erase that stamp (research R12).
        raise DomainError(
            ErrorCode.FISCAL_DOCUMENT_STAMPING, 'A stamp is in process; set the outcome after it'
        )
    if data.cancellation is not None and document.stamp_uuid is None:
        raise DomainError(
            ErrorCode.FISCAL_DOCUMENT_NOT_STAMPED, 'A cancellation outcome needs the stamp first'
        )
    if data.stamp is not None:
        xml = await fiscal_document_service.get_xml(db, fiscal_document_id)
        rejected = data.stamp == 'rejected'
        await _call(
            pac.configure_stamp(
                (xml or '').encode('utf-8'),
                StampOutcome(data.stamp),
                code=data.pac_code if rejected else None,
                message=data.pac_message if rejected else None,
            ),
            document.status,
        )
    if data.cancellation is not None:
        rejected = data.cancellation == 'rejected'
        await _call(
            pac.configure_cancellation(
                document.stamp_uuid,
                CancellationOutcome(data.cancellation),
                code=data.pac_code if rejected else None,
                message=data.pac_message if rejected else None,
            ),
            document.status,
        )
    return await _view(db, pac, document)


@router.post(
    '/fiscal-documents/{fiscal_document_id}/receiver', response_model=PacSimulationResponse
)
async def answer_as_receiver(
    fiscal_document_id: int,
    data: ReceiverAnswer,
    _: CurrentUser = Depends(_UPDATE),
    pac: SimulatedPac = Depends(get_pac_simulator),
    db: AsyncSession = Depends(get_db),
) -> PacSimulationResponse:
    """The receiver accepts, rejects or lets expire the document's pending cancellation."""
    document = await _document(db, fiscal_document_id)
    nothing = DomainError(
        ErrorCode.FISCAL_DOCUMENT_NOTHING_TO_REFRESH, 'No cancellation is pending'
    )
    if document.stamp_uuid is None:
        raise nothing
    try:
        await _call(
            pac.answer_as_receiver(document.stamp_uuid, LibraryReceiverAnswer(data.answer)),
            document.status,
        )
    except DomainError as error:
        if error.code == ErrorCode.PAC_REJECTED and error.params.get('pac_code') == (
            _NOTHING_PENDING
        ):
            raise nothing from None
        raise
    return await _view(db, pac, document)
