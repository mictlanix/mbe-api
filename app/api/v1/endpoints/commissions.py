"""Commissions: settings, monthly periods and their history (spec 025).

Every route requires SystemObject 112, CommissionsBySalesPerson. The current system's report asks
only for a login; this API is deliberately stricter (FR-014).
"""

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require_privilege
from app.db.session import get_db
from app.enums import AccessRight, SystemObject
from app.schemas.commission import CommissionPeriodDetail, CommissionPeriodResponse
from app.services import commission_service

router = APIRouter()

_READ = require_privilege(SystemObject.COMMISSIONS_BY_SALES_PERSON, AccessRight.READ)
_UPDATE = require_privilege(SystemObject.COMMISSIONS_BY_SALES_PERSON, AccessRight.UPDATE)

_YEAR = Path(ge=2000, le=2100)
_MONTH = Path(ge=1, le=12)


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
