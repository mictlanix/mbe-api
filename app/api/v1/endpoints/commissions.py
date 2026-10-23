"""Commissions: settings, monthly periods and their history (spec 025).

Every route requires SystemObject 112, CommissionsBySalesPerson. The current system's report asks
only for a login; this API is deliberately stricter (FR-014).
"""

from fastapi import APIRouter, Depends, Path, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require_privilege
from app.db.session import get_db
from app.enums import AccessRight, SystemObject
from app.schemas import ListResponse
from app.schemas.commission import (
    CommissionAgentCreate,
    CommissionAgentRateCreate,
    CommissionAgentRateResponse,
    CommissionAgentRateUpdate,
    CommissionAgentResponse,
    CommissionAgentUpdate,
    CommissionLabelCreate,
    CommissionLabelResponse,
    CommissionLabelUpdate,
    CommissionParticipationBody,
    CommissionParticipationResponse,
    CommissionPeriodDetail,
    CommissionPeriodResponse,
    CommissionPeriodSummary,
    CommissionProductLabelResponse,
    CommissionProductLabelSet,
)
from app.services import commission_service

router = APIRouter()

_READ = require_privilege(SystemObject.COMMISSIONS_BY_SALES_PERSON, AccessRight.READ)
_CREATE = require_privilege(SystemObject.COMMISSIONS_BY_SALES_PERSON, AccessRight.CREATE)
_UPDATE = require_privilege(SystemObject.COMMISSIONS_BY_SALES_PERSON, AccessRight.UPDATE)
_DELETE = require_privilege(SystemObject.COMMISSIONS_BY_SALES_PERSON, AccessRight.DELETE)

_YEAR = Path(ge=2000, le=2100)
_MONTH = Path(ge=1, le=12)


@router.get('/periods', response_model=list[CommissionPeriodSummary])
async def list_commission_periods(
    _: CurrentUser = Depends(_READ), db: AsyncSession = Depends(get_db)
) -> list[CommissionPeriodSummary]:
    """Months that have been closed at least once, newest first, with their stored total."""
    rows = await commission_service.list_periods(db)
    return [
        CommissionPeriodSummary(
            **CommissionPeriodResponse.model_validate(period).model_dump(), total=total
        )
        for period, total in rows
    ]


@router.get('/periods/{year}/{month}', response_model=CommissionPeriodDetail)
async def read_commission_period(
    year: int = _YEAR,
    month: int = _MONTH,
    salesperson: int | None = Query(None),
    _: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> CommissionPeriodDetail:
    """A month's lines and totals per agent and label: stored once closed, live while open."""
    view = await commission_service.read_period(db, year, month, salesperson=salesperson)
    return CommissionPeriodDetail.model_validate(view)


@router.post('/periods/{year}/{month}/close', response_model=CommissionPeriodResponse)
async def close_commission_period(
    year: int = _YEAR,
    month: int = _MONTH,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> CommissionPeriodResponse:
    """Freeze the month's lines. Only after the month ends, and only after the previous one."""
    period = await commission_service.close(db, year, month, current=current)
    return CommissionPeriodResponse.model_validate(period)


@router.post('/periods/{year}/{month}/reopen', response_model=CommissionPeriodResponse)
async def reopen_commission_period(
    year: int = _YEAR,
    month: int = _MONTH,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> CommissionPeriodResponse:
    """Discard a closed, unpaid month's lines. Only the latest closed month."""
    period = await commission_service.reopen(db, year, month, current=current)
    return CommissionPeriodResponse.model_validate(period)


@router.post('/periods/{year}/{month}/pay', response_model=CommissionPeriodResponse)
async def pay_commission_period(
    year: int = _YEAR,
    month: int = _MONTH,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> CommissionPeriodResponse:
    """Mark a closed month paid. A paid month is final."""
    period = await commission_service.pay(db, year, month, current=current)
    return CommissionPeriodResponse.model_validate(period)


# ── Settings ──────────────────────────────────────────────────────────────────


@router.get('/labels', response_model=list[CommissionLabelResponse])
async def list_commission_labels(
    _: CurrentUser = Depends(_READ), db: AsyncSession = Depends(get_db)
) -> list[CommissionLabelResponse]:
    rows = await commission_service.list_labels(db)
    return [CommissionLabelResponse.model_validate(r) for r in rows]


@router.post('/labels', response_model=CommissionLabelResponse, status_code=201)
async def create_commission_label(
    body: CommissionLabelCreate,
    current: CurrentUser = Depends(_CREATE),
    db: AsyncSession = Depends(get_db),
) -> CommissionLabelResponse:
    row = await commission_service.create_label(db, body, current=current)
    return CommissionLabelResponse.model_validate(row)


@router.put('/labels/{commission_id}', response_model=CommissionLabelResponse)
async def update_commission_label(
    commission_id: int,
    body: CommissionLabelUpdate,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> CommissionLabelResponse:
    row = await commission_service.update_label(db, commission_id, body, current=current)
    return CommissionLabelResponse.model_validate(row)


@router.delete('/labels/{commission_id}', status_code=204)
async def delete_commission_label(
    commission_id: int,
    current: CurrentUser = Depends(_DELETE),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await commission_service.delete_label(db, commission_id, current=current)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get('/participations', response_model=list[CommissionParticipationResponse])
async def list_commission_participations(
    _: CurrentUser = Depends(_READ), db: AsyncSession = Depends(get_db)
) -> list[CommissionParticipationResponse]:
    rows = await commission_service.list_participations(db)
    return [CommissionParticipationResponse.model_validate(r) for r in rows]


@router.post('/participations', response_model=CommissionParticipationResponse, status_code=201)
async def create_commission_participation(
    body: CommissionParticipationBody,
    current: CurrentUser = Depends(_CREATE),
    db: AsyncSession = Depends(get_db),
) -> CommissionParticipationResponse:
    row = await commission_service.create_participation(db, body, current=current)
    return CommissionParticipationResponse.model_validate(row)


@router.put('/participations/{participation_id}', response_model=CommissionParticipationResponse)
async def update_commission_participation(
    participation_id: int,
    body: CommissionParticipationBody,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> CommissionParticipationResponse:
    row = await commission_service.update_participation(db, participation_id, body, current=current)
    return CommissionParticipationResponse.model_validate(row)


@router.delete('/participations/{participation_id}', status_code=204)
async def delete_commission_participation(
    participation_id: int,
    current: CurrentUser = Depends(_DELETE),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await commission_service.delete_participation(db, participation_id, current=current)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get('/agents', response_model=list[CommissionAgentResponse])
async def list_commission_agents(
    _: CurrentUser = Depends(_READ), db: AsyncSession = Depends(get_db)
) -> list[CommissionAgentResponse]:
    rows = await commission_service.list_agents(db)
    return [CommissionAgentResponse.model_validate(r) for r in rows]


@router.post('/agents', response_model=CommissionAgentResponse, status_code=201)
async def create_commission_agent(
    body: CommissionAgentCreate,
    current: CurrentUser = Depends(_CREATE),
    db: AsyncSession = Depends(get_db),
) -> CommissionAgentResponse:
    row = await commission_service.create_agent(db, body, current=current)
    return CommissionAgentResponse.model_validate(row)


@router.put('/agents/{employee}', response_model=CommissionAgentResponse)
async def update_commission_agent(
    employee: int,
    body: CommissionAgentUpdate,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> CommissionAgentResponse:
    """The scheme takes effect at the next computation."""
    row = await commission_service.update_agent(db, employee, body, current=current)
    return CommissionAgentResponse.model_validate(row)


@router.delete('/agents/{employee}', status_code=204)
async def delete_commission_agent(
    employee: int,
    current: CurrentUser = Depends(_DELETE),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await commission_service.delete_agent(db, employee, current=current)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get('/agent-rates', response_model=list[CommissionAgentRateResponse])
async def list_commission_agent_rates(
    salesperson: int | None = Query(None),
    commission: int | None = Query(None),
    _: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> list[CommissionAgentRateResponse]:
    rows = await commission_service.list_agent_rates(
        db, salesperson=salesperson, commission=commission
    )
    return [CommissionAgentRateResponse.model_validate(r) for r in rows]


@router.post('/agent-rates', response_model=CommissionAgentRateResponse, status_code=201)
async def create_commission_agent_rate(
    body: CommissionAgentRateCreate,
    current: CurrentUser = Depends(_CREATE),
    db: AsyncSession = Depends(get_db),
) -> CommissionAgentRateResponse:
    row = await commission_service.create_agent_rate(db, body, current=current)
    return CommissionAgentRateResponse.model_validate(row)


@router.put('/agent-rates/{commission_salesperson_id}', response_model=CommissionAgentRateResponse)
async def update_commission_agent_rate(
    commission_salesperson_id: int,
    body: CommissionAgentRateUpdate,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> CommissionAgentRateResponse:
    row = await commission_service.update_agent_rate(
        db, commission_salesperson_id, body, current=current
    )
    return CommissionAgentRateResponse.model_validate(row)


@router.delete('/agent-rates/{commission_salesperson_id}', status_code=204)
async def delete_commission_agent_rate(
    commission_salesperson_id: int,
    current: CurrentUser = Depends(_DELETE),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await commission_service.delete_agent_rate(db, commission_salesperson_id, current=current)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get('/product-labels', response_model=ListResponse[CommissionProductLabelResponse])
async def list_commission_product_labels(
    commission: int | None = Query(None),
    product: int | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    _: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> ListResponse[CommissionProductLabelResponse]:
    rows, total = await commission_service.list_product_labels(
        db, commission=commission, product=product, skip=skip, limit=limit
    )
    items = [CommissionProductLabelResponse.model_validate(r) for r in rows]
    return ListResponse(items=items, total=total)


@router.put('/product-labels/{product}', response_model=CommissionProductLabelResponse)
async def set_commission_product_label(
    product: int,
    body: CommissionProductLabelSet,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> CommissionProductLabelResponse:
    """Set the product's label, or change it: a product has one."""
    row = await commission_service.set_product_label(db, product, body.commission, current=current)
    return CommissionProductLabelResponse.model_validate(row)


@router.delete('/product-labels/{product}', status_code=204)
async def delete_commission_product_label(
    product: int,
    current: CurrentUser = Depends(_DELETE),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await commission_service.delete_product_label(db, product, current=current)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
