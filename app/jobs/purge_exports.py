"""Delete the analytics exports past their expiry: their files and their rows (spec 037, FR-054).

    uv run python -m app.jobs.purge_exports

Run it on a schedule, hourly or so. A download after expiry already answers 410 and deletes the
file it finds, so a late run only leaves rows and files around a little longer.
"""

from __future__ import annotations

import asyncio

from app.core.clock import local_now
from app.db.session import AsyncSessionLocal, engine
from app.services.analytics.exports import purge


async def _run() -> int:
    # Dispose inside the loop that opened the pool, as `expire_orders` does.
    try:
        async with AsyncSessionLocal() as db:
            return await purge(db, local_now())
    finally:
        await engine.dispose()


def main() -> int:
    print(f'purged {asyncio.run(_run())} export(s)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
