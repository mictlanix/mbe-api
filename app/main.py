import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.core.config import settings

logger = logging.getLogger(__name__)


# ── Startup checks ────────────────────────────────────────────────────────────
#
# Both run before the first request is served, and both are deliberately fatal. Each guards a
# value that cannot be defaulted and whose absence would otherwise surface far from its cause —
# midway through an expiry sweep, or as stock filed against a warehouse that does not exist.
#
# They stay module-level functions rather than closures over the lifespan, so tests can call them
# directly without booting an application.


async def ensure_system_employee() -> None:
    """Make sure the actor for automated actions exists before anything can need it.

    `sales_order.updater` is an enforced foreign key, so a missing row surfaces as a constraint
    violation partway through the expiry sweep rather than at boot. Created rather than merely
    checked because there is exactly one correct value and nothing for an operator to decide —
    migration 010 seeds it, and this covers a database that has not run it.
    """
    from app.db.session import AsyncSessionLocal
    from app.services import employee_service

    async with AsyncSessionLocal() as db:
        await employee_service.ensure_system_employee(db)


async def verify_in_transit_warehouse() -> None:
    """Refuse to serve until the in-transit warehouse is configured and exists.

    Delivery movements post outbound from the dispatch warehouse and inbound to this one. With
    the setting left at its `0` default the inbound half would be written against a warehouse
    that does not exist — stock silently misfiled rather than an error anyone would notice. The
    id is created by migration 008 and cannot be defaulted, so this is checked here instead.
    """
    from sqlalchemy import select

    from app.db.session import AsyncSessionLocal
    from app.models.core import Warehouse

    if not settings.in_transit_warehouse_id:
        raise RuntimeError(
            'IN_TRANSIT_WAREHOUSE_ID is not set. Migration 008 seeds the warehouse; recover its '
            "id with: SELECT warehouse_id FROM warehouse WHERE code = 'IN-TRANSIT';"
        )

    async with AsyncSessionLocal() as db:
        found = (
            await db.execute(
                select(Warehouse.warehouse_id).where(
                    Warehouse.warehouse_id == settings.in_transit_warehouse_id
                )
            )
        ).scalar_one_or_none()

    if found is None:
        raise RuntimeError(
            f'IN_TRANSIT_WAREHOUSE_ID={settings.in_transit_warehouse_id} names no warehouse. '
            'Point it at the row migration 008 created.'
        )


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Run the startup checks, then serve.

    `lifespan` rather than `@app.on_event('startup')`, which FastAPI deprecated. The property that
    matters is preserved: an exception raised here still aborts the boot, so a misconfigured
    deployment fails to start rather than starting and misfiling stock.

    Ordered — the system employee is created first, because it is the row a later failure would
    otherwise leave for the expiry sweep to trip over.
    """
    await ensure_system_employee()
    await verify_in_transit_warehouse()
    yield


app = FastAPI(
    title=settings.app_name,
    debug=settings.debug,
    lifespan=lifespan,
)


@app.exception_handler(IntegrityError)
async def integrity_error_handler(request: Request, exc: IntegrityError) -> JSONResponse:
    """Backstop for constraints the services do not check up front.

    A rejected constraint is a client mistake, not a server fault, so it must not surface as
    a 500. Services that can say *what* conflicts should still check first and raise their
    own 409 — this only guarantees the generic case is never a 500. The driver message is
    logged rather than returned: it names tables and constraints.
    """
    logger.warning('IntegrityError on %s %s: %s', request.method, request.url.path, exc.orig)
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={'detail': 'The request conflicts with existing data'},
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=['*'],
    allow_headers=['*'],
)

app.include_router(api_router, prefix=settings.api_v1_prefix)
# Product images only. Proof-of-delivery captures live under settings.pod_dir and are served by
# an authenticated route: a customer signature must never sit behind an unauthenticated URL.
app.mount('/images', StaticFiles(directory=settings.images_dir, check_dir=False), name='images')
