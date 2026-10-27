"""Flag paid the credit orders that owe nothing but are still flagged unpaid (spec 033, FR-012).

    uv run python -m app.jobs.settle_paid_flags            # dry run: list what would change
    uv run python -m app.jobs.settle_paid_flags --apply    # change it, one incidence per order

A one-time correction, run on mbe_dev after confirmation and once more at cutover: 257 orders on
mbe_dev, 3 from 2024, 219 from 2025 and 35 from 2026. Only orders from 2026 on get a zero time, so
earlier ones never produce a commission. A second run finds nothing.

A job rather than a migration because the balance is the API's rule (line rounding, prorated
credit notes, note totals read from the XML), which SQL can't reproduce.
"""

from __future__ import annotations

import argparse
import asyncio

from app.db.session import AsyncSessionLocal, engine
from app.services import employee_service
from app.services.customer_payment_service import settle_stale_flags


async def _run(apply: bool) -> list[dict]:
    # Dispose inside the same loop that opened the pool, as `expire_orders` does.
    try:
        async with AsyncSessionLocal() as db:
            if apply:
                # The incidences are recorded against it; migration 010 seeds it.
                await employee_service.ensure_system_employee(db)
            return await settle_stale_flags(db, apply=apply)
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        '--apply', action='store_true', help='change the orders; without it, only list them'
    )
    args = parser.parse_args(argv)

    report = asyncio.run(_run(args.apply))

    for row in report:
        zero_time = row['zero_time'] or 'unset'
        print(f'  {row["sales_order_id"]}  {row["date"]:%Y-%m-%d}  {row["balance"]}  {zero_time}')
    verb = 'flagged paid' if args.apply else 'would flag paid'
    print(f'{verb} {len(report)} order(s)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
