"""Business analytics (spec 037): one catalog, one query, answered the same on every route.

Each family is gated on the existing report privileges it replaces (FR-060): the sales reports for
sales measures, received payments for payment measures, and the gross-profit reports for cost and
margin. Every figure is scoped to the caller's facility unless they are an administrator or hold
SearchCreditsFromAllStores (101), as in specs 033–035.
"""

from datetime import date

from fastapi import APIRouter, Depends, Header, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import local_now
from app.core.deps import CurrentUser, require_any_privilege
from app.db.session import get_db
from app.enums import AccessRight
from app.schemas.analytics import (
    AnalyticsCatalogResponse,
    AnalyticsDashboardRequest,
    AnalyticsDashboardResponse,
    AnalyticsDimension,
    AnalyticsMeasure,
    AnalyticsQueryRequest,
    AnalyticsQueryResponse,
    Comparison,
    DimensionKey,
)
from app.services.analytics import cache, catalog, dashboard, engine
from app.services.analytics.catalog import DIMENSIONS, FAMILY_PRIVILEGES, Family

router = APIRouter()

#: Any sales or payments report privilege opens the analytics routes; each measure then checks its
#: own family.
_ANY_FAMILY = require_any_privilege(
    *(
        (system_object, AccessRight.READ)
        for family in (Family.SALES, Family.PAYMENTS)
        for system_object in FAMILY_PRIVILEGES[family]
    )
)


@router.get('/catalog', response_model=AnalyticsCatalogResponse)
async def read_catalog(
    current: CurrentUser = Depends(_ANY_FAMILY),
    db: AsyncSession = Depends(get_db),
) -> AnalyticsCatalogResponse:
    """The measures and dimensions this caller may use (FR-001). Sensitive measures are listed only
    for holders of the cost privilege (FR-005)."""
    held = await engine.families(db, current)
    measures = catalog.visible_measures(
        cost=Family.COST in held, sales=Family.SALES in held, payments=Family.PAYMENTS in held
    )
    return AnalyticsCatalogResponse(
        as_of=local_now(),
        measures=[
            AnalyticsMeasure(
                key=m.key,
                label=m.label,
                unit=m.unit,
                sensitive=m.sensitive,
                vat=m.vat,
                additive=m.additive,
                dimensions=[d for d in DimensionKey if catalog.applicable(m.key, d)],
            )
            for m in measures
        ],
        dimensions=[
            AnalyticsDimension(key=d.key, label=d.label, grains=list(d.grains))
            for d in DIMENSIONS.values()
        ],
    )


@router.post('/query', response_model=AnalyticsQueryResponse)
async def query(
    definition: AnalyticsQueryRequest,
    if_none_match: str | None = Header(default=None),
    current: CurrentUser = Depends(_ANY_FAMILY),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Rows, totals, others and comparisons for any measures and up to three dimensions
    (FR-010–FR-015). Unknown keys fail request validation; the catalog's own refusals use the
    `analytics.*` codes. Identical requests within a minute come from the cache (FR-021)."""
    prepared = await engine.prepare(db, definition, current)
    name = cache.key(
        'query',
        prepared.definition.model_dump_json(),
        prepared.facility,
        [str(family) for family in prepared.families],
    )
    body = cache.responses.get(name)
    if body is None:
        body = (await engine.execute(db, prepared)).model_dump_json().encode()
        cache.responses.put(name, body)
    return cached_response(body, if_none_match)


@router.get('/dashboard', response_model=AnalyticsDashboardResponse)
async def read_dashboard(
    start: date,
    end: date,
    facility: int | None = None,
    comparison: Comparison = Query(default=Comparison.PREVIOUS_PERIOD),
    if_none_match: str | None = Header(default=None),
    current: CurrentUser = Depends(_ANY_FAMILY),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Every tile the caller may see for a period and scope, in one call (FR-031–FR-033). Tiles
    and alerts of a family the caller lacks are absent, not zero."""
    request = AnalyticsDashboardRequest(
        start=start, end=end, facility=facility, comparison=comparison
    )
    scope = await engine.resolve_scope(db, current, facility)
    held = await engine.families(db, current)
    name = cache.key(
        'dashboard',
        request.model_copy(update={'facility': scope}).model_dump_json(),
        scope,
        [str(family) for family in held],
    )
    body = cache.responses.get(name)
    if body is None:
        body = (await dashboard.build(db, request, current)).model_dump_json().encode()
        cache.responses.put(name, body)
    return cached_response(body, if_none_match)


def cached_response(body: bytes, if_none_match: str | None) -> Response:
    """The body with its ETag, or 304 when the client already holds it (research R13)."""
    tag = cache.etag(body)
    headers = {'ETag': tag, 'Cache-Control': 'private, max-age=60'}
    if if_none_match is not None and tag in [t.strip() for t in if_none_match.split(',')]:
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type='application/json', headers=headers)
