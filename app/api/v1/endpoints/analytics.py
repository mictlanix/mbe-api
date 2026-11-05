"""Business analytics (spec 037): one catalog, one query, answered the same on every route.

Each family is gated on the existing report privileges it replaces (FR-060): the sales reports for
sales measures, received payments for payment measures, and the gross-profit reports for cost and
margin. Every figure is scoped to the caller's facility unless they are an administrator or hold
SearchCreditsFromAllStores (101), as in specs 033–035.
"""

from datetime import date

import anyio
from fastapi import APIRouter, Depends, Header, Query, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import local_now
from app.core.deps import CurrentUser, require_any_privilege
from app.core.error_codes import ErrorCode
from app.core.errors import DomainError
from app.db.session import get_db
from app.enums import AccessRight
from app.schemas.analytics import (
    AnalyticsCatalogResponse,
    AnalyticsDashboardRequest,
    AnalyticsDashboardResponse,
    AnalyticsDetailRequest,
    AnalyticsDetailResponse,
    AnalyticsDimension,
    AnalyticsExportRequest,
    AnalyticsExportResponse,
    AnalyticsMeasure,
    AnalyticsQueryRequest,
    AnalyticsQueryResponse,
    Comparison,
    DimensionKey,
    ExportFormat,
)
from app.services.analytics import cache, catalog, dashboard, detail, engine, exports
from app.services.analytics.catalog import DIMENSIONS, FAMILY_PRIVILEGES

router = APIRouter()

#: Any privilege of a measure family (sales, payments, and spec 038's) opens the analytics routes;
#: each measure then checks its own family.
_ANY_FAMILY = require_any_privilege(
    *(
        (system_object, AccessRight.READ)
        for family in catalog.MEASURE_FAMILIES
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
    measures = catalog.visible_measures(await engine.families(db, current))
    return AnalyticsCatalogResponse(
        as_of=local_now(),
        measures=[
            AnalyticsMeasure(
                key=m.key,
                label=m.label,
                unit=m.unit,
                family=m.family.value,
                kind=m.kind.value,
                sensitive=m.sensitive,
                vat=m.vat,
                additive=m.additive,
                dimensions=[d for d in DimensionKey if catalog.applicable(m.key, d)],
                definition=m.definition,
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


@router.post('/detail', response_model=AnalyticsDetailResponse)
async def read_detail(
    request: AnalyticsDetailRequest,
    current: CurrentUser = Depends(_ANY_FAMILY),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """The documents behind an aggregate, paged and sorted, with sums over every page that equal
    it (FR-040–FR-042). Needs the family and read on the document kind. Without the cost privilege,
    `cost` and `margin` are absent from every item and from `sums`."""
    facility, cost = await detail.check(db, request, current)
    result = await detail.execute(db, request, facility, everything=False)
    return JSONResponse(detail.serialize_items(result, cost))


@router.post(
    '/exports',
    response_class=Response,
    responses={
        200: {
            'description': 'The file, when it has at most 10,000 rows (every PDF).',
            'content': {media: exports.FILE_SCHEMA for media in exports.MEDIA_TYPES.values()},
        },
        202: {'model': AnalyticsExportResponse, 'description': 'A background export to poll.'},
    },
)
async def create_export(
    request: AnalyticsExportRequest,
    current: CurrentUser = Depends(_ANY_FAMILY),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Export a query, the dashboard or a detail as XLSX, PDF or CSV (FR-050–FR-055), from the same
    definition the screen ran. Up to 10,000 rows the file comes back at once; above, an XLSX or CSV
    runs in the background and is downloaded by its owner for 24 hours. Bulk detail downloads
    (FR-055) need Download CSV files (113)."""
    checked = await exports.check(db, request, current)
    rows, answer = await exports.build_table(db, checked, count_only=True)
    mode = exports.export_mode(checked.format, rows)
    if mode == 'refused':
        raise DomainError(
            ErrorCode.ANALYTICS_PDF_TOO_LARGE,
            'Too many rows for a PDF; export to XLSX or CSV',
            {'rows': rows, 'max_rows': exports.PDF_ROWS},
        )
    if mode == 'background':
        row = await exports.start_background(db, checked)
        return JSONResponse(
            exports.response_of(row, local_now()).model_dump(mode='json'), status_code=202
        )
    if answer is None:
        _, answer = await exports.build_table(db, checked)
    if checked.format is ExportFormat.PDF:
        content = await exports.render_pdf(db, checked, answer)
    else:
        content = await anyio.to_thread.run_sync(exports.file_bytes, answer, checked.format)
    name = f'analytics-{checked.source.value}-{checked.request.start}-{checked.request.end}'
    return Response(
        content,
        media_type=exports.MEDIA_TYPES[checked.format],
        headers={'Content-Disposition': f'attachment; filename="{name}.{checked.format.value}"'},
    )


@router.get('/exports/{analytics_export_id}', response_model=AnalyticsExportResponse)
async def read_export(
    analytics_export_id: int,
    current: CurrentUser = Depends(_ANY_FAMILY),
    db: AsyncSession = Depends(get_db),
) -> AnalyticsExportResponse:
    """A background export's status. Only its owner sees it; after 24 hours it is gone (410)."""
    row = await exports.read_export(db, analytics_export_id, current)
    return exports.response_of(row, local_now())


@router.get(
    '/exports/{analytics_export_id}/file',
    response_class=Response,
    responses={
        200: {'content': {media: exports.FILE_SCHEMA for media in exports.MEDIA_TYPES.values()}}
    },
)
async def download_export(
    analytics_export_id: int,
    current: CurrentUser = Depends(_ANY_FAMILY),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Download a ready export, until its expiry (FR-054)."""
    row = await exports.read_export(db, analytics_export_id, current)
    content = await exports.read_file(row)
    format_ = exports.response_of(row, local_now()).format
    return Response(
        content,
        media_type=exports.MEDIA_TYPES[format_],
        headers={'Content-Disposition': f'attachment; filename="{row.file_name}"'},
    )


def cached_response(body: bytes, if_none_match: str | None) -> Response:
    """The body with its ETag, or 304 when the client already holds it (research R13)."""
    tag = cache.etag(body)
    headers = {'ETag': tag, 'Cache-Control': f'private, max-age={int(cache.TTL_SECONDS)}'}
    if if_none_match is not None and tag in [t.strip() for t in if_none_match.split(',')]:
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type='application/json', headers=headers)
