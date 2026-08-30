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
account, the wrong name above was the **only** unexplained mapped column in the 100 tables mapped
at the time (95 since spec 016 retired two modules). That is what
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

Measured before being written, as with the name check: across all 100 tables then mapped, one
column in each direction disagreed, and both are explained by a migration that rewrites the column
(`user`.`employee` tightened by 012, `facility`.`logo` loosened by 006) — leaving
`expiration_date` as the only real defect. `NOT NULL DEFAULT` columns are excluded from the second
direction, since an insert that omits one succeeds.

**Type is checked too, since #190.** Nullability, defaults and width say nothing about what a
column *holds*, which is how `supplier_agreement.start` and `.end` were mapped `String(10)`
against `date` columns — carrying a comment saying it was deliberate, while the driver returned
`datetime.date` objects the annotation promised were `str`. Compared by family rather than
exactly: `int(11)` against `SmallInteger` is a width question the width check owns, and only "a
different kind of value" is a defect this can name with confidence.

**What #190 fixed, and why it is worth stating here.** Three of these checks were weaker than they
read. A mapped table appearing in neither the dump nor a migration was *skipped* rather than
failed, so a model pointing at a table the database does not have — exactly what an externally
applied drop leaves behind — passed silently. Four of the five checks read the dump alone, so
every table a migration created was exempt from all of them: five tables and 28 of 798 columns,
and they were specs 008, 012 and 014's own, the newest and least settled. And type was never
compared at all. A skip is invisible in a green run, which is why
`test_every_mapped_table_is_actually_checked` now asserts coverage directly rather than leaving it
to be inferred from the suite passing.
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

# Importing every model module is what puts every mapped table on the shared metadata; importing
# only what this file names would check only those. Deliberately not a count: spec 016 retired
# seven tables and left three such numbers stale in this file alone.
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
    #: The declared width of a `varchar`/`char`, or `None` for a type that has none. A model that
    #: claims more room than the column has writes a row the database refuses — see
    #: `test_no_mapped_column_claims_more_room_than_the_column_has`.
    length: int | None


def _columns_in(sql: str) -> dict[str, dict[str, Column]]:
    tables = {}
    for table in CREATE_TABLE.finditer(sql):
        columns = {}
        for column in COLUMN_SPEC.finditer(table.group(2)):
            spec = column.group(2).upper()
            width = re.match(r'\s*(?:VAR)?CHAR\((\d+)\)', spec)
            columns[column.group(1)] = Column(
                nullable='NOT NULL' not in spec,
                has_default='DEFAULT' in spec or 'AUTO_INCREMENT' in spec,
                length=int(width.group(1)) if width else None,
            )
        tables[table.group(1)] = columns
    return tables


#: Forward migrations only. A `*_rollback.sql` is never applied automatically — `discover()`
#: excludes them — so letting one inform what the deployed schema looks like exempts columns no
#: applied migration ever touched. Measured when this was narrowed: 11 columns were being exempted
#: on the strength of a rollback alone (`active`, `cancelled`, `completed`, `disabled` and the rest
#: of 005's reverted status columns), which silently switched the checks off for them.
MIGRATION_SQL = '\n'.join(
    path.read_text()
    for path in sorted(MIGRATIONS.glob('*.sql'))
    if not path.name.endswith('_rollback.sql')
)
DUMPED = _tables_in(SCHEMA_DUMP.read_text())
CREATED_BY_MIGRATION = _tables_in(MIGRATION_SQL)
#: Columns a migration adds to a table that already existed. Not tracked per-table: matching the
#: two would mean parsing `ALTER TABLE` as a whole. Over-permissive by table, which loses nothing —
#: the point is to tell "introduced deliberately" from "this name is a typo".
ADDED_BY_MIGRATION = set(ADDED.findall(MIGRATION_SQL)) | set(RENAMED.findall(MIGRATION_SQL))
#: Every table's column specifications: the dump, plus the tables a migration creates. Keyed the
#: same way `known` is in `test_every_mapped_column_exists_in_the_schema`, and for the same reason —
#: reading the dump alone silently exempted every table this project created from the nullability
#: and width checks. Measured when this was widened (#190): five tables and 28 of 798 mapped
#: columns, and they were specs 008, 012 and 014's own — the newest and least settled, where the
#: `lot_serial_tracking.expiration_date` class of bug is likeliest and the check that exists to
#: catch it was switched off.
DUMPED_COLUMNS = _columns_in(SCHEMA_DUMP.read_text()) | _columns_in(MIGRATION_SQL)
#: Columns whose definition a migration rewrites, which is how a nullability change is expressed —
#: 012 made `user`.`employee` NOT NULL, 006 made `facility`.`logo` nullable. The dump predates both,
#: so its answer for these is stale and this check has nothing to say about them.
ALTERED_BY_MIGRATION = ADDED_BY_MIGRATION | set(RETYPED.findall(MIGRATION_SQL))

#: The declared SQL type of every column, keyed `table -> column -> type`, lowercased and stripped
#: of its width (`varchar(100)` -> `varchar`). Parsed separately from `Column` because the three
#: existing checks compare nullability, defaults and width, and none of them looks at the type at
#: all — which is how `supplier_agreement.start` and `.end` sat mapped as `String(10)` against
#: `date` columns, with a comment saying it was deliberate, while the driver returned
#: `datetime.date` objects the model claimed were `str` (#190).
RAW_TYPES: dict[str, dict[str, str]] = {}
for _source in (SCHEMA_DUMP.read_text(), MIGRATION_SQL):
    for _table in CREATE_TABLE.finditer(_source):
        RAW_TYPES.setdefault(_table.group(1), {}).update(
            {
                _column.group(1): _column.group(2).strip().split()[0].lower().split('(')[0]
                for _column in COLUMN_SPEC.finditer(_table.group(2))
            }
        )

#: SQL types grouped by what they actually hold. Compared by family rather than exactly, because
#: `int(11)` against `SmallInteger` is a width question the width check owns, while `date` against
#: `String` is a different kind of value — and only the second is a defect this can name with
#: confidence.
_SQL_FAMILY = {
    **dict.fromkeys(('int', 'bigint', 'smallint', 'mediumint', 'tinyint', 'year'), 'int'),
    **dict.fromkeys(('varchar', 'char', 'text', 'mediumtext', 'longtext', 'tinytext'), 'str'),
    **dict.fromkeys(('decimal', 'numeric', 'float', 'double'), 'num'),
    **dict.fromkeys(('datetime', 'timestamp'), 'datetime'),
    **dict.fromkeys(('blob', 'tinyblob', 'mediumblob', 'longblob', 'binary', 'varbinary'), 'bytes'),
    'date': 'date',
    'time': 'time',
    'bit': 'bit',
}

_MODEL_FAMILY = {
    'Integer': 'int',
    'SmallInteger': 'int',
    'BigInteger': 'int',
    'String': 'str',
    'Text': 'str',
    'Numeric': 'num',
    'Float': 'num',
    'DateTime': 'datetime',
    'Date': 'date',
    'Time': 'time',
    'Boolean': 'bool',
    'LargeBinary': 'bytes',
}

#: Pairs that disagree by family and are still correct. A `Boolean` is stored either as the legacy
#: `bit(1)` or as `tinyint(1)`; both read back as a bool through the driver conversion in
#: `app/db/session.py`. Nothing else is exempt — an addition here needs the same justification.
_COMPATIBLE = frozenset({('bool', 'bit'), ('bool', 'int')})


TABLES = sorted(Base.metadata.tables.values(), key=lambda t: t.name)
JUNCTIONS = [t for t in TABLES if all(c.primary_key for c in t.columns) and len(t.columns) > 1]


def test_the_schema_sources_were_actually_read() -> None:
    """The failure mode of a schema test: parse nothing, compare nothing, pass everything.

    **These floors are deliberately loose.** They exist to tell "the regex matched nothing" and
    "the models were never imported" from a working run — failures that produce a handful of
    tables or none, not eighty. A floor set just under the current count instead reads as an
    assertion about how many tables the schema has, and then fires on the next legitimate removal
    with a message about imports: spec 016 retired seven tables and took the old margin of ten
    down to five without anything being wrong (#190).

    The real coverage assertion is that nothing is skipped — every mapped table is compared —
    which `test_every_mapped_table_is_actually_checked` states directly.
    """
    assert len(DUMPED) > 50, f'only {len(DUMPED)} tables parsed out of the dump'
    assert ADDED_BY_MIGRATION, 'no ADD COLUMN found in any migration'
    assert len(TABLES) > 50, f'only {len(TABLES)} tables on the metadata — are the models imported?'


def test_every_mapped_table_is_actually_checked() -> None:
    """No mapped table may sit outside the comparison — the assertion the loose floors are not.

    Before #190 four of the five checks read the dump alone, so every table a migration created
    was skipped by all of them: five tables and 28 columns, and they were the newest ones this
    project wrote itself. A skip is invisible in a green run, which is why coverage is asserted
    here rather than inferred from the suite passing.
    """
    unchecked = sorted(t.name for t in TABLES if t.name not in DUMPED_COLUMNS)

    assert not unchecked, (
        f'{len(unchecked)} mapped table(s) have no parsed column specification, so the '
        f'nullability, width and type checks silently skip them: {unchecked}'
    )


def test_the_column_specifications_were_parsed_too() -> None:
    """The nullability and width checks say nothing unless the text after each name was read.

    A `NOT NULL` that fails to parse reads as "nullable", which would make both nullability checks
    agree with anything; a width that fails to parse reads as `None`, which makes the width check
    skip the column silently. Pinned on definitions that are not going to change.
    """
    customer = DUMPED_COLUMNS['customer']

    assert customer['code'] == Column(nullable=False, has_default=False, length=25)
    assert customer['comment'].nullable
    assert customer['comment'].length == 1024
    assert DUMPED_COLUMNS['lot_serial_tracking']['expiration_date'].nullable
    # A type with no width at all must read as None rather than as 0.
    assert DUMPED_COLUMNS['lot_serial_tracking']['expiration_date'].length is None
    # `NOT NULL DEFAULT '0'` — the case the second direction has to forgive.
    assert DUMPED_COLUMNS['commission']['comment'] == Column(
        nullable=False, has_default=True, length=50
    )


def test_junctions_were_found() -> None:
    """These are the tables this test exists for, so their absence must not read as success."""
    names = {t.name for t in JUNCTIONS}

    assert {'customer_address', 'customer_contact', 'customer_taxpayer'} <= names, names


@pytest.mark.parametrize('table', TABLES, ids=lambda t: t.name)
def test_every_mapped_column_exists_in_the_schema(table) -> None:  # noqa: ANN001
    """A mapped column must be in the dump, in a migration's `CREATE TABLE`, or added by one."""
    known = DUMPED.get(table.name, set()) | CREATED_BY_MIGRATION.get(table.name, set())
    assert known, (
        f'`{table.name}` is mapped but appears in neither the dump nor a migration. Either the '
        'table name is wrong, or the table was dropped without its model — see #190.'
    )

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


@pytest.mark.parametrize('table', TABLES, ids=lambda t: t.name)
def test_no_mapped_column_claims_more_room_than_the_column_has(table) -> None:  # noqa: ANN001
    """A model declaring `String(n)` wider than the column writes a row the database refuses.

    This is the `user.password` case (issue #161). The model has declared `String(255)` since it was
    written, anticipating a bcrypt migration; the column was `varchar(40)` until migration 016. The
    name matched and the nullability matched, so neither existing check could see it, and SQLAlchemy
    does not enforce length on insert — so nothing failed locally either.

    Worse, `tests/integration/` builds its schema from this metadata, so there the column was
    `VARCHAR(255)`: a 60-character bcrypt hash would have fitted and passed the whole suite, then
    raised error 1406 against MariaDB. The test environment was more permissive than production,
    which is the same shape as spec 014 research R4.

    Measured when this was written: with migrations accounted for, **zero** columns disagree, so it
    is precise rather than a standing source of noise. A model narrower than the column is fine and
    not checked — it only means the code cannot use all the room available.
    """
    known = DUMPED_COLUMNS.get(table.name)
    if not known:
        pytest.skip(f'{table.name} is not in the schema dump')

    overclaimed = sorted(
        f'{column.name} String({declared}) > varchar({spec.length})'
        for column in table.columns
        if (spec := known.get(column.name))
        and spec.length is not None
        and (declared := getattr(column.type, 'length', None)) is not None
        and declared > spec.length
        and column.name not in ALTERED_BY_MIGRATION
    )

    assert not overclaimed, (
        f'`{table.name}` maps {overclaimed} — the model claims more room than the column has, so a '
        f'value that fits the model is refused by the database with error 1406. Either narrow the '
        f'model or add a migration widening the column.'
    )


def test_the_declared_types_were_parsed() -> None:
    """A type check that parses nothing compares nothing and passes everything."""
    assert len(RAW_TYPES) > 90, f'only {len(RAW_TYPES)} tables parsed for types'
    assert RAW_TYPES['customer']['code'] == 'varchar'
    assert RAW_TYPES['supplier_agreement']['start'] == 'date'
    assert RAW_TYPES['product']['tax_rate'] == 'decimal'


@pytest.mark.parametrize('table', TABLES, ids=lambda t: t.name)
def test_no_mapped_column_claims_a_different_kind_of_value(table) -> None:  # noqa: ANN001
    """A model must not read a column as a kind of value the column does not hold (#190).

    The other checks compare nullability, defaults and width. None compares the *type*, which is
    how `supplier_agreement.start`/`.end` were mapped `String(10)` against `date` columns — it
    wrote fine, because MariaDB casts a string on the way in, and read back as `datetime.date`
    while the annotation promised `str`. Nothing failed; it was simply a lie to every reader and
    to the type checker.

    Compared by **family**, not exactly: `int(11)` against `SmallInteger` is a width question the
    width check already owns, and holding this check to exact types would make it a source of
    standing noise rather than a signal. What it catches is a column read as the wrong kind of
    thing — a date as a string, a number as text, bytes as a string.
    """
    declared = RAW_TYPES.get(table.name, {})
    assert declared, (
        f'{table.name} has no parsed column types — see test_the_declared_types_were_parsed'
    )

    wrong = []
    for column in table.columns:
        sql = declared.get(column.name)
        if sql is None:
            continue  # added by a migration's ALTER; the name check owns that case
        sql_family = _SQL_FAMILY.get(sql, sql)
        model_family = _MODEL_FAMILY.get(type(column.type).__name__, type(column.type).__name__)
        if sql_family == model_family or (model_family, sql_family) in _COMPATIBLE:
            continue
        wrong.append(f'{column.name}: schema {sql}, mapped {type(column.type).__name__}')

    assert not wrong, (
        f'`{table.name}` maps column(s) as a different kind of value than the schema holds: '
        f'{wrong}. Fix the model, or add the pair to `_COMPATIBLE` with a reason.'
    )
