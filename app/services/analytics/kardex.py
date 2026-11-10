"""The analytics kardex (spec 038, FR-104, research R6): one product's ledger in one warehouse, any
range from the stock history start, paged, with value.

Spec 030's kardex caps a range at 366 days, so this pages instead and reuses its pieces: the
running balance (`kardex_rows`), the documents (`_documents`, `resolve`) and the type names
(`source_name`). Each page starts from the opening balance plus the entries it skipped, so a page
continues the balance exactly where the one before ended. Spec 030's route is untouched.
"""

from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import local_now
from app.core.deps import CurrentUser
from app.core.error_codes import ErrorCode
from app.core.errors import DomainError, not_found
from app.models.core import Warehouse
from app.models.inventory import LotSerialTracking
from app.models.product import Product
from app.schemas.analytics import (
    AnalyticsKardexRequest,
    AnalyticsKardexResponse,
    AnalyticsKardexRow,
    MeasureKey,
)
from app.services import kardex_service
from app.services.analytics import balances, engine
from app.services.analytics.catalog import Family


async def check(
    db: AsyncSession, request: AnalyticsKardexRequest, current: CurrentUser
) -> tuple[int | None, bool]:
    """The history, the range, the product and warehouse, and the facility scope by the
    warehouse's facility. Returns that facility and whether the caller sees cost."""
    engine.history_guard([MeasureKey.ON_HAND], request.start, request.end)
    if request.end < request.start:
        raise DomainError(ErrorCode.KARDEX_RANGE_REVERSED, 'The range ends before it starts')
    held = await engine.families(db, current)
    if Family.INVENTORY not in held:
        raise engine.family_forbidden(Family.INVENTORY)
    if await db.get(Product, request.product) is None:
        raise not_found(Product, request.product)
    warehouse = await db.get(Warehouse, request.warehouse)
    if warehouse is None:
        raise not_found(Warehouse, request.warehouse)
    await engine.resolve_scope(db, current, warehouse.facility)
    return warehouse.facility, Family.COST in held


async def read(
    db: AsyncSession, request: AnalyticsKardexRequest, *, cost: bool, everything: bool = False
) -> AnalyticsKardexResponse:
    """A page of entries (every entry for an export) with the balances the whole range gives."""
    entry = LotSerialTracking
    since = datetime.combine(request.start, time())
    until = datetime.combine(request.end + timedelta(days=1), time())
    pair = (entry.product == request.product, entry.warehouse == request.warehouse)
    in_range = (*pair, entry.date >= since, entry.date < until)
    ordered = (entry.date, entry.lot_serial_tracking_id)

    opening = Decimal(
        (
            await db.execute(
                select(func.coalesce(func.sum(entry.quantity), 0)).where(*pair, entry.date < since)
            )
        ).scalar_one()
    )
    total, moved = (
        await db.execute(
            select(func.count(), func.coalesce(func.sum(entry.quantity), 0)).where(*in_range)
        )
    ).one()
    skip = 0 if everything else request.skip
    skipped = Decimal(0)
    if skip:
        first = select(entry.quantity).where(*in_range).order_by(*ordered).limit(skip).subquery()
        skipped = Decimal(
            (await db.execute(select(func.coalesce(func.sum(first.c.quantity), 0)))).scalar_one()
        )
    page = select(entry).where(*in_range).order_by(*ordered).offset(skip)
    if not everything:
        page = page.limit(request.limit)
    entries = (await db.execute(page)).scalars().all()

    documents = await kardex_service._documents(
        db, kardex_service.references_by_type(entries), {request.product}
    )
    movements = kardex_service.kardex_rows(
        opening + skipped, entries, kardex_service.resolve(entries, documents)
    )
    unit_cost = (await balances.costs(db, [request.product])).get(request.product) if cost else None
    return AnalyticsKardexResponse(
        as_of=local_now(),
        product=request.product,
        warehouse=request.warehouse,
        start=request.start,
        end=request.end,
        opening=opening,
        unit_cost=unit_cost,
        rows=[
            AnalyticsKardexRow(
                date=row.date,
                movement_type=row.source,
                source=row.source_name,
                reference=row.reference,
                document_id=None if row.document is None else row.document.id,
                quantity=row.quantity_in - row.quantity_out,
                balance=row.balance,
                value=None
                if unit_cost is None
                else engine.money(balances.cents(row.balance * unit_cost)),
            )
            for row in movements.rows
        ],
        total=total,
        closing=opening + Decimal(moved),
    )


def serialize(response: AnalyticsKardexResponse, cost_visible: bool) -> dict[str, Any]:
    """Without the cost privilege, `unit_cost` and every row's `value` are absent, not null."""
    if cost_visible:
        return response.model_dump(mode='json')
    return response.model_dump(
        mode='json', exclude={'unit_cost': True, 'rows': {'__all__': {'value'}}}
    )
