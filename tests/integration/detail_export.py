"""Checks that a detail export of a spec 038 kind holds what the screen shows (spec 037's rule):
the same items in the same order, with the warehouse and every `figures.values` column."""

import csv
import io
import re
from datetime import datetime
from typing import Any

from httpx import AsyncClient

URL = '/api/v1/analytics'
#: Item fields the generic detail columns don't carry.
NOT_EXPORTED = {'kind', 'id', 'product', 'figures'}


def _shown(value: Any) -> str:
    """A JSON value as the CSV writes it: ISO datetimes to the minute, null as empty."""
    if value is None:
        return ''
    if isinstance(value, str) and re.fullmatch(r'\d{4}-\d\d-\d\dT.*', value):
        return datetime.fromisoformat(value).isoformat(sep=' ', timespec='minutes')
    return str(value)


async def assert_export_matches(client: AsyncClient, detail: dict) -> list[dict[str, str]]:
    """Exports `detail` as CSV and asserts each line equals its JSON item; returns the lines."""
    shown = await client.post(f'{URL}/detail', json={**detail, 'limit': 100})
    exported = await client.post(f'{URL}/exports', json={'format': 'csv', 'detail': detail})
    assert shown.status_code == 200, shown.text
    assert exported.status_code == 200, exported.text
    items = shown.json()['items']
    assert items, 'the detail must list something to compare'
    reader = csv.DictReader(io.StringIO(exported.content.decode('utf-8-sig')))
    header = reader.fieldnames or []
    assert len(header) == len(set(header)), header
    lines = list(reader)
    assert len(lines) == len(items) + 1  # and the TOTAL line
    for item, line in zip(items, lines, strict=False):
        expected = {k: v for k, v in item.items() if k not in NOT_EXPORTED}
        if item['warehouse'] is None:
            expected.pop('warehouse')
        for key, value in item['figures']['values'].items():
            expected.setdefault(key, value)
        assert set(expected) <= set(header), sorted(set(expected) - set(header))
        assert {k: line[k] for k in expected} == {k: _shown(v) for k, v in expected.items()}
    assert lines[-1][header[0]] == 'TOTAL'
    return lines
