"""Cancel confirmed orders nobody paid for or delivered, releasing the stock they reserved.

    uv run python -m app.jobs.expire_orders             # apply
    uv run python -m app.jobs.expire_orders --dry-run   # list what would go
    uv run python -m app.jobs.expire_orders --days 5    # override the configured window

Run it on a schedule. A CLI rather than a route because this is an operational sweep, not
something a user does: it cancels in bulk, and putting that behind an HTTP verb invites it being
fired by accident.

Exit codes: 0 when everything expired cleanly, 1 when any order had to be skipped. A skipped order
is still holding stock, so a scheduler that only watches for failures should hear about it.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.core.config import settings
from app.db.session import AsyncSessionLocal, engine
from app.services.order_expiry import ExpiryReport, expire_unpaid_orders


async def _run(days: int | None, dry_run: bool) -> ExpiryReport:
    # Dispose inside the same loop that opened the pool: closing it from a second `asyncio.run`
    # leaves aiomysql's connections owned by a loop that is already gone, which surfaces as a
    # wall of teardown tracebacks after a run that actually succeeded.
    try:
        async with AsyncSessionLocal() as db:
            return await expire_unpaid_orders(db, days=days, dry_run=dry_run)
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        '--days',
        type=int,
        default=None,
        help=f'days since the order date (default: {settings.unpaid_order_expiry_days})',
    )
    parser.add_argument(
        '--dry-run', action='store_true', help='report what would be cancelled, change nothing'
    )
    args = parser.parse_args(argv)

    try:
        report = asyncio.run(_run(args.days, args.dry_run))
    except RuntimeError as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2

    verb = 'would cancel' if args.dry_run else 'cancelled'
    print(f'{verb} {len(report.cancelled)} order(s)')
    for order_id in report.cancelled:
        print(f'  {order_id}')

    if report.skipped:
        print(f'\nskipped {len(report.skipped)} order(s) still holding stock:', file=sys.stderr)
        for order_id, reason in report.skipped:
            print(f'  {order_id}: {reason}', file=sys.stderr)
        return 1

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
