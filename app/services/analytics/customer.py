"""Customer 360 (spec 038, FR-125, contracts/api.md "Customer 360").

Every section is an `engine.run` of a query definition with the customer's filter forced, so each
equals `POST /analytics/query` for the definition it echoes. Orders per month is the `orders`
measure over the 12 months up to `end`; the credit limit and utilization are spec 033's.
"""

from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import local_now
from app.core.deps import CurrentUser
from app.core.errors import not_found
from app.models.customer import Customer
from app.schemas.analytics import (
    AnalyticsCustomerRequest,
    AnalyticsCustomerResponse,
    AnalyticsFilter,
    AnalyticsQueryRequest,
    Comparison,
    DimensionKey,
    MeasureKey,
)
from app.services import accounts_service
from app.services.analytics import engine, facts
from app.services.analytics.catalog import Family

TOP = 10
MONTHS = 12
#: The response's figures; each stays null when the caller lacks its family.
FIGURES = (
    'sales', 'receivable', 'top_products', 'last_purchase', 'orders_per_month', 'credit_limit',
    'credit_utilization',
)  # fmt: skip


async def check(
    db: AsyncSession, request: AnalyticsCustomerRequest, current: CurrentUser, held: set[Family]
) -> tuple[Customer, int | None]:
    """The customer and the facility the figures cover. Needs the sales or receivables family;
    an unknown customer is 404 and another facility is refused as in spec 037."""
    if not held & {Family.SALES, Family.RECEIVABLES}:
        raise engine.family_forbidden(Family.SALES)
    engine.validate_period(request.start, request.end)
    facility = await engine.resolve_scope(db, current, request.facility)
    customer = await db.get(Customer, request.customer)
    if customer is None:
        raise not_found(Customer, request.customer)
    return customer, facility


async def last_purchase(
    db: AsyncSession, customer: int, start: date, end: date, facility: int | None
) -> datetime | None:
    """The latest confirmation of the customer's orders the `orders` measure counts."""
    lines = facts.sale_lines(start, end, facility)
    return (
        await db.execute(select(func.max(lines.c.at)).where(lines.c.customer == customer))
    ).scalar()


async def build(
    db: AsyncSession,
    request: AnalyticsCustomerRequest,
    current: CurrentUser,
    held: set[Family] | None = None,
) -> AnalyticsCustomerResponse:
    """The sections the caller's families open; the others are null.

    `held` replaces the caller's families when they were checked earlier (a background export).
    """
    if held is None:
        held = await engine.families(db, current)
    customer, facility = await check(db, request, current, held)
    forced = [AnalyticsFilter(dimension=DimensionKey.CUSTOMER, values=[str(request.customer)])]

    def definition(**kw: Any) -> AnalyticsQueryRequest:
        values = {'start': request.start, 'end': request.end, 'facility': facility}
        return AnalyticsQueryRequest(**{**values, 'filters': forced, **kw})

    async def run(**kw: Any) -> Any:
        return await engine.run(db, definition(**kw), current)

    figures: dict[str, Any] = dict.fromkeys(FIGURES)
    if Family.SALES in held:
        measures = [MeasureKey.NET_SALES, MeasureKey.REFUNDS]
        if Family.PAYMENTS in held:
            measures.append(MeasureKey.COLLECTED)
        if Family.COST in held:
            measures += [MeasureKey.GROSS_MARGIN, MeasureKey.MARGIN_PCT]
        figures['sales'] = await run(measures=measures, comparison=Comparison.PREVIOUS_PERIOD)
        figures['top_products'] = await run(
            measures=[MeasureKey.NET_SALES], dimensions=[DimensionKey.PRODUCT],
            sort=f'-{MeasureKey.NET_SALES.value}', top=TOP,
        )  # fmt: skip
        year_earlier, _ = engine.comparison_period(
            request.end, request.end, Comparison.SAME_PERIOD_LAST_YEAR
        )
        since = year_earlier + timedelta(days=1)
        orders = await run(measures=[MeasureKey.ORDERS], start=since)
        count = orders.totals.values[MeasureKey.ORDERS.value] or Decimal(0)
        figures['orders_per_month'] = (count / MONTHS).quantize(engine.CENT, rounding=ROUND_HALF_UP)
        figures['last_purchase'] = await last_purchase(
            db, request.customer, since, request.end, facility
        )
    if Family.RECEIVABLES in held:
        today = local_now().date()
        figures['receivable'] = await run(
            measures=[
                MeasureKey.RECEIVABLE, MeasureKey.RECEIVABLE_CURRENT, MeasureKey.RECEIVABLE_OVERDUE,
            ],
            dimensions=[DimensionKey.AGING_BUCKET], start=today, end=today,
        )  # fmt: skip
        summary = (
            await accounts_service.detail(
                db, accounts_service.CUSTOMERS, request.customer, facility=facility
            )
        )['summary']
        figures['credit_limit'] = summary['credit_limit']
        figures['credit_utilization'] = summary['utilization']
    return AnalyticsCustomerResponse(
        as_of=local_now(),
        customer=customer.customer_id,
        customer_name=customer.name,
        start=request.start,
        end=request.end,
        **figures,
    )
