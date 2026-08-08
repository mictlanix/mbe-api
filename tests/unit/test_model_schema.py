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

**Nullability is checked too, in both directions**, because a name comparison cannot see it.
`lot_serial_tracking.expiration_date` was mapped `NOT NULL` against a `DEFAULT NULL` column and
reached `main` with the name entirely correct; it broke every itinerary departure the moment a
schema was built from the models. The other direction is worse where it happens: a model that
allows `None` on a `NOT NULL` column with no default writes a row the database rejects, at whatever
point in a workflow the value happened to be absent.

Measured before being written, as with the name check: across all 100 tables one column in each
direction disagreed, and both are explained by a migration that rewrites the column
(`user`.`employee` tightened by 012, `facility`.`logo` loosened by 006) — leaving
`expiration_date` as the only real defect. `NOT NULL DEFAULT` columns are excluded from the second
direction, since an insert that omits one succeeds.
"""

import importlib
import pkgutil
import re
from pathlib import Path
from typing import NamedTuple

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
#: The same line, with everything after the name — the type, `NOT NULL`, `DEFAULT`.
COLUMN_SPEC = re.compile(r'^\s+`(\w+)`\s+(.*?),?$', re.M)
#: `ADD COLUMN [IF NOT EXISTS] `x``, and the new name in `CHANGE COLUMN `old` `new``.
ADDED = re.compile(r'ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?`(\w+)`', re.I)
RENAMED = re.compile(r'CHANGE\s+COLUMN\s+`\w+`\s+`(\w+)`', re.I)
#: A migration that alters an existing column, which is how nullability changes here.
RETYPED = re.compile(r'(?:MODIFY|CHANGE)\s+COLUMN\s+`(\w+)`', re.I)


def _tables_in(sql: str) -> dict[str, set[str]]:
    return {m.group(1): set(COLUMN_LINE.findall(m.group(2))) for m in CREATE_TABLE.finditer(sql)}


class Column(NamedTuple):
    """What the schema says about a column, beyond whether it exists."""

    nullable: bool
    #: A `NOT NULL` column with a default can still be inserted without naming it, so a model that
    #: leaves it out is not making a claim the database will refuse.
    has_default: bool


def _columns_in(sql: str) -> dict[str, dict[str, Column]]:
    tables = {}
    for table in CREATE_TABLE.finditer(sql):
        columns = {}
        for column in COLUMN_SPEC.finditer(table.group(2)):
            spec = column.group(2).upper()
            columns[column.group(1)] = Column(
                nullable='NOT NULL' not in spec,
                has_default='DEFAULT' in spec or 'AUTO_INCREMENT' in spec,
            )
        tables[table.group(1)] = columns
    return tables


MIGRATION_SQL = '\n'.join(
    path.read_text() for path in sorted(MIGRATIONS.glob('*.sql'))
)
DUMPED = _tables_in(SCHEMA_DUMP.read_text())
CREATED_BY_MIGRATION = _tables_in(MIGRATION_SQL)
#: Columns a migration adds to a table that already existed. Not tracked per-table: matching the
#: two would mean parsing `ALTER TABLE` as a whole. Over-permissive by table, which loses nothing —
#: the point is to tell "introduced deliberately" from "this name is a typo".
ADDED_BY_MIGRATION = set(ADDED.findall(MIGRATION_SQL)) | set(RENAMED.findall(MIGRATION_SQL))
DUMPED_COLUMNS = _columns_in(SCHEMA_DUMP.read_text())
#: Columns whose definition a migration rewrites, which is how a nullability change is expressed —
#: 012 made `user`.`employee` NOT NULL, 006 made `facility`.`logo` nullable. The dump predates both,
#: so its answer for these is stale and this check has nothing to say about them.
ALTERED_BY_MIGRATION = ADDED_BY_MIGRATION | set(RETYPED.findall(MIGRATION_SQL))

TABLES = sorted(Base.metadata.tables.values(), key=lambda t: t.name)
JUNCTIONS = [t for t in TABLES if all(c.primary_key for c in t.columns) and len(t.columns) > 1]


def test_the_schema_sources_were_actually_read() -> None:
    """The failure mode of a schema test: parse nothing, compare nothing, pass everything."""
    assert len(DUMPED) > 90, f'only {len(DUMPED)} tables parsed out of the dump'
    assert ADDED_BY_MIGRATION, 'no ADD COLUMN found in any migration'
    assert len(TABLES) > 90, f'only {len(TABLES)} tables on the metadata — are the models imported?'


def test_the_column_specifications_were_parsed_too() -> None:
    """The nullability checks say nothing unless the text after each column name was read.

    A `NOT NULL` that fails to parse reads as "nullable", which would make both of those checks
    agree with anything. Pinned on definitions that are not going to change.
    """
    customer = DUMPED_COLUMNS['customer']

    assert customer['code'] == Column(nullable=False, has_default=False)
    assert customer['comment'].nullable
    assert DUMPED_COLUMNS['lot_serial_tracking']['expiration_date'].nullable
    # `NOT NULL DEFAULT '0'` — the case the second direction has to forgive.
    assert DUMPED_COLUMNS['commission']['comment'] == Column(nullable=False, has_default=True)


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


@pytest.mark.parametrize('table', TABLES, ids=lambda t: t.name)
def test_no_mapped_column_forbids_what_the_schema_allows(table) -> None:  # noqa: ANN001
    """A model that says `NOT NULL` where the database says `DEFAULT NULL` is misdeclared.

    This is the `lot_serial_tracking.expiration_date` case. MariaDB was never affected, because
    SQLAlchemy does not enforce nullability on insert — but a schema *generated* from the models
    refused every stock-ledger row a departure writes, which is how it surfaced. It reached `main`
    because a column-name comparison cannot see it: the name was always right.

    The usual cause is a shadowed type name. `date` is a column on that class, so inside the class
    body the annotation `date | None` resolves to the column rather than to `datetime.date`,
    SQLAlchemy cannot read it as optional, and it falls back to `NOT NULL`.
    """
    known = DUMPED_COLUMNS.get(table.name)
    if not known:
        pytest.skip(f'{table.name} is not in the schema dump')

    misdeclared = sorted(
        column.name
        for column in table.columns
        if (spec := known.get(column.name))
        and spec.nullable
        and not column.nullable
        and column.name not in ALTERED_BY_MIGRATION
    )

    assert not misdeclared, (
        f'`{table.name}` maps {misdeclared} as NOT NULL, but the schema allows NULL. Either the '
        f'annotation is being read wrongly — a shadowed type name will do it — or a migration that '
        f'tightens the column is missing.'
    )


@pytest.mark.parametrize('table', TABLES, ids=lambda t: t.name)
def test_no_mapped_column_permits_what_the_schema_refuses(table) -> None:  # noqa: ANN001
    """The other direction, which fails in production rather than in a test.

    A model that allows `None` where the column is `NOT NULL` **and has no default** lets the code
    write a row the database will reject — error 1048, at whatever point in a workflow the value
    happened to be absent. A `NOT NULL DEFAULT` column is excluded: an insert that omits it
    succeeds, so the model leaving it optional claims nothing untrue.
    """
    known = DUMPED_COLUMNS.get(table.name)
    if not known:
        pytest.skip(f'{table.name} is not in the schema dump')

    too_permissive = sorted(
        column.name
        for column in table.columns
        if (spec := known.get(column.name))
        and not spec.nullable
        and not spec.has_default
        and column.nullable
        and column.name not in ALTERED_BY_MIGRATION
    )

    assert not too_permissive, (
        f'`{table.name}` maps {too_permissive} as optional, but the schema refuses NULL and gives '
        f'no default — writing one of these without a value fails at the database.'
    )
