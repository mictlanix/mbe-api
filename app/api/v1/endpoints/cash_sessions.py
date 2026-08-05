from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require_privilege
from app.db.session import get_db
from app.enums import AccessRight, SystemObject
from app.schemas import ListResponse
from app.schemas.cash_session import (
    CashSessionClose,
    CashSessionOpen,
    CashSessionResponse,
    CashSessionSort,
    CashSessionStatus,
    CurrentSessionResponse,
)
from app.services import cash_session_service

router = APIRouter()

# Opening and reading a session is ordinary counter work, governed by POS (44). Closing is the
# privileged part and has its own system object — which is why (111) exists at all.
_READ = require_privilege(SystemObject.POS, AccessRight.READ)
_CREATE = require_privilege(SystemObject.POS, AccessRight.CREATE)
_CLOSE = require_privilege(SystemObject.CASH_SESSION_CLOSE, AccessRight.UPDATE)


@router.get('/current', response_model=CurrentSessionResponse)
async def get_current_session(
    current: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> CurrentSessionResponse:
    state, session = await cash_session_service.current_session(db, current=current)
    return CurrentSessionResponse(
        state=state,
        session=CashSessionResponse.model_validate(session) if session is not None else None,
    )


@router.get('', response_model=ListResponse[CashSessionResponse])
async def list_cash_sessions(
    cash_drawer: int | None = Query(None),
    cashier: int | None = Query(None),
    facility: int | None = Query(None),
    session_status: CashSessionStatus | None = Query(None, alias='status'),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    sort: CashSessionSort = Query(CashSessionSort.ID_DESC),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> ListResponse[CashSessionResponse]:
    items, total = await cash_session_service.list_sessions(
        db,
        current=current,
        cash_drawer=cash_drawer,
        cashier=cashier,
        facility=facility,
        session_status=session_status,
        date_from=date_from,
        date_to=date_to,
        sort=sort,
        skip=skip,
        limit=limit,
    )
    return ListResponse(
        items=[CashSessionResponse.model_validate(s) for s in items], total=total
    )


@router.post('', response_model=CashSessionResponse, status_code=status.HTTP_201_CREATED)
async def open_cash_session(
    data: CashSessionOpen,
    current: CurrentUser = Depends(_CREATE),
    db: AsyncSession = Depends(get_db),
) -> CashSessionResponse:
    session = await cash_session_service.open_session(db, data, current=current)
    return CashSessionResponse.model_validate(session)


@router.get('/{cash_session_id}', response_model=CashSessionResponse)
async def get_cash_session(
    cash_session_id: int,
    _: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> CashSessionResponse:
    session = await cash_session_service.get_session(db, cash_session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Cash session not found')
    await cash_session_service.attach_derived(db, session)
    return CashSessionResponse.model_validate(session)


@router.post('/{cash_session_id}/close', response_model=CashSessionResponse)
async def close_cash_session(
    cash_session_id: int,
    data: CashSessionClose,
    current: CurrentUser = Depends(_CLOSE),
    db: AsyncSession = Depends(get_db),
) -> CashSessionResponse:
    session = await cash_session_service.get_session(db, cash_session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Cash session not found')
    session = await cash_session_service.close_session(db, session, data, current=current)
    return CashSessionResponse.model_validate(session)
