"""Resolve the CFDIs left in flight: stamping documents and open cancellations (spec 039, R12).

    uv run python -m app.jobs.resolve_fiscal_documents

Run it every ten minutes on dev and test, as the instance's user:

    */10 * * * * cd <instance> && uv run python -m app.jobs.resolve_fiscal_documents

Each document is refreshed oldest first in its own session, as the system employee, exactly as
`POST /fiscal-documents/{id}/refresh` does it. Refresh writes only what is still true when the PAC
answers, so overlapping runs (dev and test share mbe_dev) are safe. A failure is printed, without
the PAC's text, and the run moves on; it exits 1 when any document failed. Installing the cron
entry on a server is a deployment step that needs confirmation.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import check_pac_settings, settings
from app.core.error_codes import ErrorCode
from app.core.errors import DomainError
from app.db.session import AsyncSessionLocal, engine
from app.models.fiscal import FiscalDocument, FiscalDocumentCancellation
from app.services import employee_service, fiscal_stamping
from app.services.pac import get_pac_provider
from app.services.pac.provider import PacProvider


async def run(
    provider: PacProvider,
    session_factory: async_sessionmaker[AsyncSession] = AsyncSessionLocal,
) -> int:
    """Refresh every stamping document and open request; 1 if any failed, else 0."""
    async with session_factory() as db:
        employee_id = (await employee_service.ensure_system_employee(db)).employee_id
        stamping = await db.execute(
            select(FiscalDocument.fiscal_document_id, FiscalDocument.stamping_since).where(
                FiscalDocument.stamping_since.is_not(None)
            )
        )
        requested = await db.execute(
            select(
                FiscalDocumentCancellation.fiscal_document,
                FiscalDocumentCancellation.requested_at,
            ).where(FiscalDocumentCancellation.status.in_(('requested', 'pending')))
        )
        in_flight = sorted([*stamping.all(), *requested.all()], key=lambda row: (row[1], row[0]))
        await db.commit()

    failed = 0
    for document_id, _since in in_flight:
        async with session_factory() as db:
            try:
                await fiscal_stamping.refresh(db, document_id, provider, employee_id)
            except DomainError as error:
                if error.code == ErrorCode.FISCAL_DOCUMENT_NOTHING_TO_REFRESH:
                    continue  # an overlapping run resolved it
                failed += 1
                print(f'fiscal document {document_id}: {error.code}')
            except Exception as error:  # noqa: BLE001 - one document never stops the others
                failed += 1
                print(f'fiscal document {document_id}: {type(error).__name__}')
    return 1 if failed else 0


async def _run(provider: PacProvider) -> int:
    # Dispose inside the loop that opened the pool, as `expire_orders` does.
    try:
        return await run(provider)
    finally:
        await engine.dispose()


def main() -> int:
    check_pac_settings(settings)
    provider = get_pac_provider()
    if provider is None:
        print('no PAC provider is configured on this instance')
        return 0
    return asyncio.run(_run(provider))


if __name__ == '__main__':
    raise SystemExit(main())
