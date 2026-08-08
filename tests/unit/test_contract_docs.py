"""The pseudo-schemas in `specs/*/contracts/*.md` must match the components the app really serves.

`specs/002-master-data-endpoints/contracts/api.md` drifted for three releases without anyone
noticing: spec 005 replaced every `disabled`/`deactivated`/`active`/`enabled` boolean with
`status: EntityStatus`, FR-039 turned 14 documented ints into embedded objects, and #132/#133/#150
added three collections the file never mentioned. A client written against it would have sent fields
the API rejects and expected fields it never sends. Nothing failed: nothing compared the two.

**The type check is the load-bearing half.** Comparing field *names* alone passes clean on every one
of those FK expansions — `facility` is still called `facility`, it just stopped being an `int`. Only
comparing the documented type against the live one catches that, which is the drift most likely to
recur: adding an expansion is a routine change that reads as backward-compatible.

Scope and its limits, so the next reader knows what this does not cover:

- Only fenced blocks in the `Name:` / `NameA / NameB:` pseudo-schema form are read. Contract files
  that document their endpoints as prose or tables (011, 012, 013) are invisible here.
- A name is required to resolve to a live component only when it *looks* like one (`XResponse`,
  `XCreate`, …). Illustrative names are left alone; a renamed component still fails, because the
  rename keeps the suffix. `SatXxxResponse`, a placeholder for `SatCatalogResponse`, was caught this
  way.
- Types are compared by class — scalar, array, or a named component — not field by field into the
  nested shape. `price_list: PriceListResponse` is checked to be that component and no further.
- Query parameters and status codes are **not** checked. Those live in section prose, and matching
  them means encoding one file's heading structure here.
"""

import json
import re
from pathlib import Path
from typing import NamedTuple

import pytest

from app.main import app

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_DOCS = sorted((REPO_ROOT / 'specs').glob('*/contracts/*.md'))
SCHEMAS = app.openapi()['components']['schemas']

#: Documented type names that stand for a plain value rather than an embedded object.
SCALARS = frozenset({'int', 'str', 'bool', 'float', 'Decimal', 'date', 'datetime', 'time'})

#: A name in this form is taken to be a real component, so failing to resolve it is drift rather
#: than an illustrative placeholder.
COMPONENT_NAME = re.compile(r'^[A-Z]\w*(Create|Update|Response|ListItem|Summary|Request|Facet)$')


class Block(NamedTuple):
    """One pseudo-schema: the component name(s) it claims to describe, and the fields it lists."""

    doc: str
    name: str
    fields: dict[str, str]

    def __str__(self) -> str:
        return f'{self.doc}:{self.name}'


def _parse(path: Path) -> list[Block]:
    """Every schema block in one file. A fenced block may hold several, separated by blank lines."""
    blocks: list[Block] = []
    doc = path.relative_to(REPO_ROOT / 'specs').as_posix()
    for fence in re.findall(r'```\n(.*?)```', path.read_text(), re.S):
        for chunk in re.split(r'\n\s*\n', fence):
            lines = [line for line in chunk.splitlines() if line.strip()]
            if not lines:
                continue
            names = [n.strip() for n in lines[0].rstrip(':').strip().split('/')]
            if not all(re.fullmatch(r'\w+', n) for n in names):
                continue
            fields = {}
            for line in lines[1:]:
                # Indented, so the head of the next schema in the same fence is not read as a field.
                field = re.match(r'\s+(\w+)\s*:\s*([^#]*)', line)
                if field:
                    fields[field.group(1)] = field.group(2).strip()
            if not fields:
                # A prose block that happens to end its first line in a colon, not a schema.
                continue
            blocks.extend(Block(doc, name, fields) for name in names)
    return blocks


BLOCKS = [block for path in CONTRACT_DOCS for block in _parse(path)]
KNOWN = [b for b in BLOCKS if b.name in SCHEMAS]


def _live_type(prop: dict) -> str:
    """The class of a live property: `array<...>`, a component name, `scalar`, or `unknown`.

    Arrays are read before refs, or an array of embedded objects would report as the object.
    """
    branches = [prop, *prop.get('anyOf', [])]
    for branch in branches:
        if branch.get('type') == 'array':
            return f'array<{_live_type(branch.get("items", {}))}>'
    ref = re.search(r'#/components/schemas/(\w+)', json.dumps(prop))
    if ref:
        return ref.group(1)
    types = {b.get('type') for b in branches if b.get('type')} - {'null'}
    if types & {'string', 'integer', 'boolean', 'number'}:
        return 'scalar'
    return 'unknown'


def _documented_type(declared: str) -> str:
    """The same classes, read off the document. `unknown` where the notation says nothing useful."""
    text = declared.split('|')[0].strip()
    inner = re.fullmatch(r'\[(.+?)(,\s*\.\.\.)?\]', text)
    if inner:
        return f'array<{_documented_type(inner.group(1))}>'
    if text in SCALARS:
        return 'scalar'
    if re.fullmatch(r'[A-Z]\w+', text):
        return text
    return 'unknown'


def test_the_parser_still_finds_the_schema_blocks() -> None:
    """The failure mode of a docs test: the format shifts, nothing parses, everything passes.

    Pinned to the file this exists for. If `api.md` is restructured, this fails and asks whether the
    parser should follow rather than quietly checking nothing.
    """
    master_data = [b for b in BLOCKS if b.doc == '002-master-data-endpoints/contracts/api.md']

    assert len(master_data) >= 30, f'only {len(master_data)} schema blocks parsed out of api.md'
    assert BLOCKS, 'no contract documentation was parsed at all'


@pytest.mark.parametrize('block', BLOCKS, ids=str)
def test_a_documented_component_exists(block: Block) -> None:
    """A name shaped like a component must resolve to one — that is what catches a rename."""
    if not COMPONENT_NAME.match(block.name):
        pytest.skip(f'{block.name} is not in the component naming convention')

    assert block.name in SCHEMAS, (
        f'{block.doc} documents `{block.name}`, which no longer exists in the OpenAPI schema'
    )


@pytest.mark.parametrize('block', KNOWN, ids=str)
def test_documented_fields_match_the_component(block: Block) -> None:
    """Both directions: a field the API dropped is as misleading as one it gained."""
    live = set(SCHEMAS[block.name].get('properties', {}))
    documented = set(block.fields)

    assert not documented - live, (
        f'{block.doc} documents fields `{block.name}` no longer has: '
        f'{sorted(documented - live)}'
    )
    assert not live - documented, (
        f'{block.doc} does not document fields `{block.name}` returns: {sorted(live - documented)}'
    )


@pytest.mark.parametrize('block', KNOWN, ids=str)
def test_documented_types_match_the_component(block: Block) -> None:
    """The half a name comparison cannot see: a scalar that became an embedded object (FR-039)."""
    properties = SCHEMAS[block.name].get('properties', {})
    mismatches = []
    for field, declared in block.fields.items():
        prop = properties.get(field)
        if prop is None:
            continue  # Reported by the field test; not this one's business.
        documented, live = _documented_type(declared), _live_type(prop)
        if 'unknown' in (documented, live):
            continue  # The notation says nothing checkable — do not invent a failure.
        if documented != live:
            mismatches.append(f'{field}: documented as `{declared}`, API returns `{live}`')

    assert not mismatches, f'{block.doc} `{block.name}` — ' + '; '.join(mismatches)
