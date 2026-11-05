"""Saved views (spec 038, FR-150–FR-153, research R14).

A view stores the request of a query, dashboard, detail or customer 360 without its dates, and a
relative period that gives them the day it runs. Every save and every run parses the definition
into its kind's request model, so a key the catalog no longer has is refused with
`analytics.view_invalid` and nothing runs. A save also runs the kind's own privilege and scope
check as the owner; a run is the kind's route, as the opener, with the opener's privileges and
facility scope (FR-152). Only the owner changes or deletes a view, and only the owner sees whom it
is shared with.
"""

import json
from datetime import date
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import local_now, local_today
from app.core.deps import CurrentUser, forbidden
from app.core.error_codes import ErrorCode
from app.core.errors import DomainError, not_found
from app.enums import EntityStatus
from app.models.analytics import AnalyticsView, AnalyticsViewShare
from app.models.user import User
from app.schemas.analytics import (
    AnalyticsCustomerRequest,
    AnalyticsDashboardRequest,
    AnalyticsDetailRequest,
    AnalyticsPeriod,
    AnalyticsQueryRequest,
    AnalyticsViewCreate,
    AnalyticsViewResponse,
    AnalyticsViewUpdate,
    ViewKind,
)
from app.services.analytics import customer, detail, engine, periods
from app.services.analytics.catalog import MEASURE_FAMILIES, Family

#: The stored `kind`: 0 query, 1 dashboard, 2 detail, 3 customer 360.
KINDS = (ViewKind.QUERY, ViewKind.DASHBOARD, ViewKind.DETAIL, ViewKind.CUSTOMER)
REQUESTS: dict[ViewKind, type[BaseModel]] = {
    ViewKind.QUERY: AnalyticsQueryRequest,
    ViewKind.DASHBOARD: AnalyticsDashboardRequest,
    ViewKind.DETAIL: AnalyticsDetailRequest,
    ViewKind.CUSTOMER: AnalyticsCustomerRequest,
}
DATES = frozenset({'start', 'end'})


def request_of(
    kind: ViewKind, definition: dict[str, Any], period: AnalyticsPeriod, today: date
) -> Any:
    """The kind's request for the period resolved against `today`, or `analytics.view_invalid`
    naming the first field that no longer validates (`key`, a dotted path) and why."""
    start, end = periods.resolve(period, today)
    try:
        return REQUESTS[kind].model_validate({**definition, 'start': start, 'end': end})
    except ValidationError as exc:
        error = exc.errors()[0]
        key = '.'.join(str(part) for part in error['loc']) or kind.value
        raise DomainError(
            ErrorCode.ANALYTICS_VIEW_INVALID,
            f'The view no longer validates: {key}: {error["msg"]}',
            {'key': key, 'reason': error['msg']},
        ) from None


async def check(db: AsyncSession, kind: ViewKind, request: Any, current: CurrentUser) -> None:
    """The privilege, scope and catalog checks the kind's route makes, without running it."""
    match kind:
        case ViewKind.QUERY:
            await engine.prepare(db, request, current)
        case ViewKind.DASHBOARD:
            engine.validate_period(request.start, request.end)
            if not await engine.families(db, current) & set(MEASURE_FAMILIES):
                raise engine.family_forbidden(Family.SALES)
            await engine.resolve_scope(db, current, request.facility)
        case ViewKind.DETAIL:
            await detail.check(db, request, current)
        case ViewKind.CUSTOMER:
            await customer.check(db, request, current, await engine.families(db, current))


def kind_of(row: AnalyticsView) -> ViewKind:
    return KINDS[row.kind]


def period_of(row: AnalyticsView) -> AnalyticsPeriod:
    return AnalyticsPeriod.model_validate_json(row.period)


def resolved(row: AnalyticsView) -> Any:
    """The view's request with its period resolved today, in business time."""
    return request_of(kind_of(row), json.loads(row.definition), period_of(row), local_today())


async def _validated(
    db: AsyncSession,
    kind: ViewKind,
    definition: dict[str, Any],
    period: AnalyticsPeriod,
    current: CurrentUser,
) -> str:
    """The definition to store: parsed and checked as the owner, without its dates."""
    request = request_of(kind, definition, period, local_today())
    await check(db, kind, request, current)
    return json.dumps(request.model_dump(mode='json', exclude_unset=True, exclude=DATES))


async def _unique_name(db: AsyncSession, owner: str, name: str, view: int | None = None) -> None:
    query = select(AnalyticsView.analytics_view_id).where(
        AnalyticsView.user == owner, AnalyticsView.name == name
    )
    found = (await db.execute(query)).scalar_one_or_none()
    if found is not None and found != view:
        raise DomainError(
            ErrorCode.RESOURCE_ALREADY_EXISTS,
            'You already have a view with this name',
            {'resource': 'analytics_view', 'field': 'name'},
        )


async def _sharable(db: AsyncSession, users: list[str], owner: str) -> list[str]:
    """The users to share with, in order and without repeats or the owner; each must be active."""
    wanted = [u for u in dict.fromkeys(users) if u != owner]
    if not wanted:
        return []
    query = select(User.user_id).where(User.user_id.in_(wanted), User.status == EntityStatus.ACTIVE)
    active = set((await db.execute(query)).scalars())
    for user in wanted:
        if user not in active:
            raise not_found(User, user)
    return wanted


async def _share(db: AsyncSession, view: int, users: list[str]) -> None:
    await db.execute(delete(AnalyticsViewShare).where(AnalyticsViewShare.analytics_view == view))
    db.add_all(AnalyticsViewShare(analytics_view=view, user=user) for user in users)


async def _shared_with(db: AsyncSession, views: list[int]) -> dict[int, list[str]]:
    if not views:
        return {}
    query = (
        select(AnalyticsViewShare.analytics_view, AnalyticsViewShare.user)
        .where(AnalyticsViewShare.analytics_view.in_(views))
        .order_by(AnalyticsViewShare.user)
    )
    found: dict[int, list[str]] = {view: [] for view in views}
    for view, user in (await db.execute(query)).all():
        found[view].append(user)
    return found


def response_of(row: AnalyticsView, shared_with: list[str]) -> AnalyticsViewResponse:
    return AnalyticsViewResponse(
        analytics_view_id=row.analytics_view_id,
        name=row.name,
        kind=kind_of(row),
        owner=row.user,
        definition=json.loads(row.definition),
        period=period_of(row),
        shared_with=shared_with,
        creation_time=row.creation_time,
        modification_time=row.modification_time,
    )


async def respond(
    db: AsyncSession, row: AnalyticsView, current: CurrentUser
) -> AnalyticsViewResponse:
    """The view as the caller sees it: `shared_with` only for the owner."""
    if row.user != current.user_id:
        return response_of(row, [])
    shared = await _shared_with(db, [row.analytics_view_id])
    return response_of(row, shared[row.analytics_view_id])


async def list_views(db: AsyncSession, current: CurrentUser) -> list[AnalyticsViewResponse]:
    """The caller's views and those shared with them, by name."""
    shared = select(AnalyticsViewShare.analytics_view).where(
        AnalyticsViewShare.user == current.user_id
    )
    query = (
        select(AnalyticsView)
        .where(
            or_(AnalyticsView.user == current.user_id, AnalyticsView.analytics_view_id.in_(shared))
        )
        .order_by(AnalyticsView.name, AnalyticsView.analytics_view_id)
    )
    rows = (await db.execute(query)).scalars().all()
    own = await _shared_with(
        db, [row.analytics_view_id for row in rows if row.user == current.user_id]
    )
    return [response_of(row, own.get(row.analytics_view_id, [])) for row in rows]


async def get(db: AsyncSession, view: int, current: CurrentUser) -> AnalyticsView:
    """A view the caller owns or that is shared with them; any other is not found."""
    row = await db.get(AnalyticsView, view)
    if row is not None and row.user != current.user_id:
        share = await db.get(AnalyticsViewShare, (view, current.user_id))
        if share is None:
            row = None
    if row is None:
        raise not_found(AnalyticsView, view)
    return row


async def owned(db: AsyncSession, view: int, current: CurrentUser) -> AnalyticsView:
    """A view the caller may change: their own. Shared users are refused (`auth.forbidden`)."""
    row = await get(db, view, current)
    if row.user != current.user_id:
        raise forbidden([])
    return row


async def create(
    db: AsyncSession, data: AnalyticsViewCreate, current: CurrentUser
) -> AnalyticsView:
    definition = await _validated(db, data.kind, data.definition, data.period, current)
    await _unique_name(db, current.user_id, data.name)
    shared = await _sharable(db, data.shared_with, current.user_id)
    now = local_now().replace(microsecond=0)
    row = AnalyticsView(
        user=current.user_id,
        name=data.name,
        kind=KINDS.index(data.kind),
        definition=definition,
        period=data.period.model_dump_json(exclude_none=True),
        creation_time=now,
        modification_time=now,
    )
    db.add(row)
    await db.flush()
    await _share(db, row.analytics_view_id, shared)
    await db.commit()
    return row


async def update(
    db: AsyncSession, view: int, data: AnalyticsViewUpdate, current: CurrentUser
) -> AnalyticsView:
    """Rename, change the definition, period or sharing. Every save validates the view again."""
    row = await owned(db, view, current)
    period = data.period or period_of(row)
    definition = data.definition if data.definition is not None else json.loads(row.definition)
    row.definition = await _validated(db, kind_of(row), definition, period, current)
    row.period = period.model_dump_json(exclude_none=True)
    if data.name is not None:
        await _unique_name(db, row.user, data.name, view)
        row.name = data.name
    if data.shared_with is not None:
        await _share(db, view, await _sharable(db, data.shared_with, row.user))
    row.modification_time = local_now().replace(microsecond=0)
    await db.commit()
    return row


async def delete_view(db: AsyncSession, view: int, current: CurrentUser) -> None:
    """Delete the view; its shares and schedules (and their sends) go with it."""
    row = await owned(db, view, current)
    await db.delete(row)
    await db.commit()
