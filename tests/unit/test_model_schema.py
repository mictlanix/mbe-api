"""Every mapped column must exist in the database — checked against the schema, not a mock.

`customer_taxpayer` was mapped with a column the table does not have (`taxpayer_recipient`; it is
`taxpayer`). Nothing failed until #150 first joined through it, and then `GET /customers/{id}`,
`POST /customers` and `PUT /customers/{id}` all returned 500 — the writes having already committed,
so a 500 that reads as "nothing happened" had in fact created or updated a customer (#154).

**Why the existing tests could not catch it.** Every test of that code mocks `db.execute`, so
the SQL is never handed to a database with a schema. Worse, the unit test asserted the insert
payload `{'customer': 1, 'taxpayer_recipient': ...}` — it encoded the wrong name and passed happily.
A test that asserts what the code does cannot notice that what the code does is impossible.

So this compares the mapped metadata against the two files that describe the real schema:
`docs/mbe_schema.sql`, the checked-in dump, and `migrations/*.sql`, which is how this project
changes schemas (numbered SQL, no Alembic). A mapped column has to appear in one or the other.

The dump is a snapshot from before spec 005, so on its own it disagrees with 18 tables. Every one of
those disagreements is a column a migration adds — measured, not assumed: with migrations taken into
account, the wrong name above was the **only** unexplained mapped column in 100 tables. That is what
makes this check precise enough to be worth having rather than a source of standing noise.

Junction tables are the reason this matters most. An ORM class is exercised by every test that
builds one, so a wrong attribute name surfaces early; a `Table()` is just column names in a
string, invisible until a query runs against a live database.
"""

import importlib
import pkgutil
import re
from pathlib import Path

import pytest

import app.models
from app.db.base import Base

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DUMP = REPO_ROOT / 'docs' / 'mbe_schema.sql'
MIGRATIONS = REPO_ROOT / 'migrations'

# Importing every model module is what puts all 100 tables on the shared metadata; importing only
# what this file names would check only those.
for _, name, _ in pkgutil.iter_modules(app.models.__path__):
    importlib.import_module(f'app.models.{name}')

#: `CREATE TABLE `x` (...)` in the dump, and the same in a migration for tables it creates.
CREATE_TABLE = re.compile(r'CREATE TABLE (?:IF NOT EXISTS )?`?(\w+)`? \((.*?)\n\)', re.S)
#: A column definition line inside such a block.
COLUMN_LINE = re.compile(r'^\s+`(\w+)`\s', re.M)
#: `ADD COLUMN [IF NOT EXISTS] `x``, and the new name in `CHANGE COLUMN `old` `new``.
ADDED = re.compile(r'ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?`(\w+)`', re.I)
RENAMED = re.compile(r'CHANGE\s+COLUMN\s+`\w+`\s+`(\w+)`', re.I)


def _tables_in(sql: str) -> dict[str, set[str]]:
    return {m.group(1): set(COLUMN_LINE.findall(m.group(2))) for m in CREATE_TABLE.finditer(sql)}


MIGRATION_SQL = '\n'.join(
    path.read_text() for path in sorted(MIGRATIONS.glob('*.sql'))
)
DUMPED = _tables_in(SCHEMA_DUMP.read_text())
CREATED_BY_MIGRATION = _tables_in(MIGRATION_SQL)
#: Columns a migration adds to a table that already existed. Not tracked per-table: matching the
#: two would mean parsing `ALTER TABLE` as a whole. Over-permissive by table, which loses nothing —
#: the point is to tell "introduced deliberately" from "this name is a typo".
ADDED_BY_MIGRATION = set(ADDED.findall(MIGRATION_SQL)) | set(RENAMED.findall(MIGRATION_SQL))

TABLES = sorted(Base.metadata.tables.values(), key=lambda t: t.name)
JUNCTIONS = [t for t in TABLES if all(c.primary_key for c in t.columns) and len(t.columns) > 1]


def test_the_schema_sources_were_actually_read() -> None:
    """The failure mode of a schema test: parse nothing, compare nothing, pass everything."""
    assert len(DUMPED) > 90, f'only {len(DUMPED)} tables parsed out of the dump'
    assert ADDED_BY_MIGRATION, 'no ADD COLUMN found in any migration'
    assert len(TABLES) > 90, f'only {len(TABLES)} tables on the metadata — are the models imported?'


def test_junctions_were_found() -> None:
    """These are the tables this test exists for, so their absence must not read as success."""
    names = {t.name for t in JUNCTIONS}

    assert {'customer_address', 'customer_contact', 'customer_taxpayer'} <= names, names


@pytest.mark.parametrize('table', TABLES, ids=lambda t: t.name)
def test_every_mapped_column_exists_in_the_schema(table) -> None:  # noqa: ANN001
    """A mapped column must be in the dump, in a migration's `CREATE TABLE`, or added by one."""
    known = DUMPED.get(table.name, set()) | CREATED_BY_MIGRATION.get(table.name, set())
    if not known:
        pytest.skip(f'{table.name} is described by neither the dump nor a migration')

    unknown = sorted(
        c.name for c in table.columns if c.name not in known and c.name not in ADDED_BY_MIGRATION
    )

    assert not unknown, (
        f'`{table.name}` is mapped with column(s) the schema does not have: {unknown}. '
        f'Either the model name is wrong, or a migration that adds them is missing.'
    )


@pytest.mark.parametrize('table', JUNCTIONS, ids=lambda t: t.name)
def test_a_junction_matches_the_schema_exactly(table) -> None:  # noqa: ANN001
    """Both directions, for junctions only.

    A missing column on an ORM-mapped table is ordinary — the model need not map every column of
    a legacy table. On a junction it is not: the whole table is the two ends of the relation, so a
    name that does not line up means one of the ends is wrong, which is exactly #154.
    """
    if table.name not in DUMPED:
        pytest.skip(f'{table.name} is not in the schema dump')

    assert {c.name for c in table.columns} == DUMPED[table.name], (
        f'`{table.name}` maps {sorted(c.name for c in table.columns)}, '
        f'the schema has {sorted(DUMPED[table.name])}'
    )
