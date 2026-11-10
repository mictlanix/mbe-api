"""Saved views and their schedules (spec 038, FR-150–FR-165, research R14–R16).

Any authenticated user saves views of what they may run, shares them with active users, and opens
the views shared with them. A run is the route of the view's kind, as the caller: their privileges,
their facility scope, and that route's cache and `If-None-Match`. The owner of a view schedules
it to be emailed to users who may read it; the job `app.jobs.send_scheduled_reports` sends it.
"""

from fastapi import APIRouter, Depends, Header, Query, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints import analytics
from app.core.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.schemas import ListResponse
from app.schemas.analytics import (
    AnalyticsCustomerResponse,
    AnalyticsDashboardResponse,
    AnalyticsDetailResponse,
    AnalyticsQueryResponse,
    AnalyticsScheduleCreate,
    AnalyticsScheduleResponse,
    AnalyticsScheduleUpdate,
    AnalyticsSendResponse,
    AnalyticsViewCreate,
    AnalyticsViewResponse,
    AnalyticsViewUpdate,
    ViewKind,
)
from app.services.analytics import schedules, views

router = APIRouter()


@router.get('/views', response_model=list[AnalyticsViewResponse])
async def list_views(
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[AnalyticsViewResponse]:
    """The caller's views and those shared with them, by name."""
    return await views.list_views(db, current)


@router.post('/views', response_model=AnalyticsViewResponse, status_code=201)
async def create_view(
    data: AnalyticsViewCreate,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AnalyticsViewResponse:
    """Save a definition with a relative period. It must validate against the catalog
    (`analytics.view_invalid`) and the caller must be able to run it; names are unique per owner."""
    row = await views.create(db, data, current)
    return await views.respond(db, row, current)


@router.get('/views/{analytics_view_id}', response_model=AnalyticsViewResponse)
async def read_view(
    analytics_view_id: int,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AnalyticsViewResponse:
    """One view the caller owns or that is shared with them. `shared_with` is the owner's only."""
    return await views.respond(db, await views.get(db, analytics_view_id, current), current)


@router.patch('/views/{analytics_view_id}', response_model=AnalyticsViewResponse)
async def update_view(
    analytics_view_id: int,
    data: AnalyticsViewUpdate,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AnalyticsViewResponse:
    """Rename, change the definition, period or sharing. Owner only; validated again."""
    row = await views.update(db, analytics_view_id, data, current)
    return await views.respond(db, row, current)


@router.delete('/views/{analytics_view_id}', status_code=204)
async def delete_view(
    analytics_view_id: int,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Delete a view with its schedules. Owner only."""
    await views.delete_view(db, analytics_view_id, current)
    return Response(status_code=204)


@router.post(
    '/views/{analytics_view_id}/run',
    response_model=AnalyticsQueryResponse
    | AnalyticsDashboardResponse
    | AnalyticsDetailResponse
    | AnalyticsCustomerResponse,
)
async def run_view(
    analytics_view_id: int,
    if_none_match: str | None = Header(default=None),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Run the view with its period resolved today, as the caller: the same response as the
    query, dashboard, detail or customer route, with that route's cache and `If-None-Match`.
    A view that no longer validates is refused with `analytics.view_invalid`."""
    row = await views.get(db, analytics_view_id, current)
    request = views.resolved(row)
    match views.kind_of(row):
        case ViewKind.QUERY:
            return await analytics.query(request, if_none_match, current, db)
        case ViewKind.DASHBOARD:
            return await analytics.read_dashboard(
                request.start,
                request.end,
                request.facility,
                request.comparison,
                request.families,
                if_none_match,
                current,
                db,
            )
        case ViewKind.DETAIL:
            return await analytics.read_detail(request, current, db)
        case ViewKind.CUSTOMER:
            found = await analytics.read_customer(
                request.customer, request.start, request.end, request.facility, current, db
            )
            return JSONResponse(found.model_dump(mode='json'))


# ── Schedules ─────────────────────────────────────────────────────────────────


@router.get('/schedules', response_model=list[AnalyticsScheduleResponse])
async def list_schedules(
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[AnalyticsScheduleResponse]:
    """The schedules of the caller's views; an administrator sees every one."""
    return await schedules.list_schedules(db, current)


@router.post('/schedules', response_model=AnalyticsScheduleResponse, status_code=201)
async def create_schedule(
    data: AnalyticsScheduleCreate,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AnalyticsScheduleResponse:
    """Schedule one of the caller's views. Its export is checked as the caller, and each recipient
    must be an active user holding the families it reads (`analytics.recipient_ineligible`)."""
    return await schedules.respond(db, await schedules.create(db, data, current))


@router.get('/schedules/{analytics_schedule_id}', response_model=AnalyticsScheduleResponse)
async def read_schedule(
    analytics_schedule_id: int,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AnalyticsScheduleResponse:
    """One schedule of the caller's views, or any for an administrator."""
    row, _ = await schedules.get(db, analytics_schedule_id, current)
    return await schedules.respond(db, row)


@router.patch('/schedules/{analytics_schedule_id}', response_model=AnalyticsScheduleResponse)
async def update_schedule(
    analytics_schedule_id: int,
    data: AnalyticsScheduleUpdate,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AnalyticsScheduleResponse:
    """Change a schedule or enable/disable it. The view's owner only; checked again."""
    row = await schedules.update_schedule(db, analytics_schedule_id, data, current)
    return await schedules.respond(db, row)


@router.delete('/schedules/{analytics_schedule_id}', status_code=204)
async def delete_schedule(
    analytics_schedule_id: int,
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Delete a schedule with its send history. The view's owner only."""
    await schedules.delete_schedule(db, analytics_schedule_id, current)
    return Response(status_code=204)


@router.get(
    '/schedules/{analytics_schedule_id}/sends',
    response_model=ListResponse[AnalyticsSendResponse],
)
async def list_sends(
    analytics_schedule_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ListResponse[AnalyticsSendResponse]:
    """The schedule's send history, newest first. A send left running over 10 minutes reads as
    failed."""
    return await schedules.list_sends(db, analytics_schedule_id, current, skip, limit)
