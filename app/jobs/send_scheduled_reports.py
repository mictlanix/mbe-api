"""Email the saved views whose schedules are due (spec 038, FR-160–FR-165, research R15).

    uv run python -m app.jobs.send_scheduled_reports

Run it every five minutes, as the instance's user, on the one instance where
`SCHEDULED_REPORTS_ENABLED=true` (dev and test share mbe_dev); on every other one it does nothing:

    */5 * * * * cd <instance> && uv run python -m app.jobs.send_scheduled_reports

Each due schedule is claimed in the database before it is sent, so overlapping runs, on one
instance or several, send each occurrence once, and a run after downtime sends only the latest
missed occurrence. Every attempt is recorded in `analytics_send`. Installing the cron entry on a
server is a deployment step that needs confirmation.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import local_now
from app.core.config import settings
from app.db.session import AsyncSessionLocal, engine
from app.models.analytics import AnalyticsSchedule
from app.services.analytics import schedules


async def run(db: AsyncSession, now: datetime) -> int:
    """Claim and send every schedule due at `now`; the number of sends attempted."""
    if not settings.scheduled_reports_enabled:
        return 0
    query = (
        select(AnalyticsSchedule.analytics_schedule_id, AnalyticsSchedule.next_run_time)
        .where(AnalyticsSchedule.enabled.is_(True), AnalyticsSchedule.next_run_time <= now)
        .order_by(AnalyticsSchedule.next_run_time)
    )
    attempted = 0
    for schedule_id, read in (await db.execute(query)).all():
        schedule = await db.get(AnalyticsSchedule, schedule_id, populate_existing=True)
        occurrence, following = schedules.due(schedule, now)
        if not await schedules.claim(db, schedule_id, read, following):
            continue  # another run has it
        await schedules.send(db, schedule, occurrence or read, now)
        attempted += 1
    return attempted


async def _run() -> int:
    # Dispose inside the loop that opened the pool, as `expire_orders` does.
    try:
        async with AsyncSessionLocal() as db:
            return await run(db, local_now().replace(microsecond=0))
    finally:
        await engine.dispose()


def main() -> int:
    if not settings.scheduled_reports_enabled:
        print('scheduled reports are disabled on this instance')
        return 0
    print(f'{asyncio.run(_run())} scheduled report(s) attempted')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
