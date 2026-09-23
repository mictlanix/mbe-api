"""Every datetime the API exchanges is business wall-clock time, and the schema must say so (#228).

The OpenAPI document declared every datetime `format: date-time`, which designates RFC 3339 and
therefore an offset, while the wire carried naive local time. A spec-conformant client was entitled
to read the string as UTC, and mbe-ui's generated client did: every date it showed was six hours
off (mictlanix/mbe-ui#176). The fix is the written contract, so this asserts it reaches
`app.openapi()`, the only thing a generated client sees.

The description is attached only by `LocalDateTime`, which also converts an inbound offset. So a
date-time node carrying it is a field that converts, and a node without it is a field that would
hand the database driver an aware value to strip silently.
"""

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.main import app

OPENAPI = app.openapi()


def _date_time_nodes(node: Any, path: str) -> Iterator[tuple[str, dict]]:
    """Every `format: date-time` schema under `node`, with a readable path to it."""
    if isinstance(node, dict):
        if node.get('format') == 'date-time':
            yield path, node
        for key, value in node.items():
            yield from _date_time_nodes(value, f'{path}.{key}')
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _date_time_nodes(value, f'{path}[{index}]')


def _all_date_time_nodes() -> list[tuple[str, dict]]:
    nodes = list(_date_time_nodes(OPENAPI['components']['schemas'], 'components'))
    for route, operations in OPENAPI['paths'].items():
        for method, operation in operations.items():
            for parameter in operation.get('parameters', []):
                where = f'{method.upper()} {route} ?{parameter["name"]}'
                nodes.extend(_date_time_nodes(parameter, where))
    return nodes


def test_the_schema_declares_datetimes_at_all() -> None:
    """Guards the check below against passing vacuously if the walk stops finding anything."""
    nodes = _all_date_time_nodes()

    assert any(where.startswith('components') for where, _ in nodes)
    assert any('?date_from' in where for where, _ in nodes)


def test_every_datetime_says_it_is_local_wall_clock_time() -> None:
    zone = settings.business_timezone.key
    undescribed = [
        where
        for where, node in _all_date_time_nodes()
        if 'no UTC offset' not in node.get('description', '') or zone not in node['description']
    ]

    assert not undescribed, (
        'These datetimes do not state the local-time convention. Declare them `LocalDateTime` '
        '(app/schemas/__init__.py), not `datetime`:\n  ' + '\n  '.join(undescribed)
    )


# ── No stray clock reads ──────────────────────────────────────────────────────

_APP = Path(__file__).resolve().parents[2] / 'app'
_HOST_CLOCK = re.compile(r'datetime\.now\(|date\.today\(|datetime\.utcnow\(')
#: The business clock itself, and JWT expiry — an absolute UTC instant that is never stored.
_ALLOWED = {_APP / 'core' / 'clock.py', _APP / 'core' / 'security.py'}


def test_nothing_reads_the_host_clock_outside_the_business_clock() -> None:
    """`datetime.now()` is the host's zone, not the business's. One stray call is enough to write a
    timestamp hours off into the shared database — `vehicle_operator` already did, in UTC."""
    offenders = [
        f'{path.relative_to(_APP.parent)}:{number}: {line.strip()}'
        for path in sorted(_APP.rglob('*.py'))
        if path not in _ALLOWED
        for number, line in enumerate(path.read_text().splitlines(), start=1)
        if _HOST_CLOCK.search(line)
    ]

    assert not offenders, (
        'Use app.core.clock.local_now() / local_today() instead:\n  ' + '\n  '.join(offenders)
    )


def test_token_expiry_stays_an_absolute_utc_instant() -> None:
    """FR-008: the allowance above is for this, and must not be "fixed" to local time."""
    assert 'datetime.now(UTC)' in (_APP / 'core' / 'security.py').read_text()
