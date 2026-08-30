"""Every column the deployed schema has must be described in `docs/data-dictionary.md` (#179).

`tests/unit/test_model_schema.py` checks the other direction — every column the models *map* has
to exist in the schema — and that pairing is deliberate. Between them the two cover the gap each
one leaves: a mapped column that the database does not have (#154), and a column the database has
that nobody wrote down.

The second is the quieter failure, and it is not hypothetical. A `code vs docs` audit against the
deployment found 25 undocumented columns, and while none of them came from this project's own
migrations, nothing was stopping the next one from adding to the pile: the dictionary is updated by
convention (spec 014, T046), and a convention with no check behind it is a convention that holds
until someone is in a hurry. This is that check. It would have caught every column migration 008
added, the day it landed.

**Where the schema comes from.** The same two sources `test_model_schema.py` reads: `mbe_schema.sql`
for the baseline and `migrations/*.sql` for everything since — numbered SQL, no Alembic. Unlike that
test, which only needs to know whether a column exists *somewhere*, this one has to know the state
the deployment is actually in, so the migrations are replayed in order: `ADD COLUMN` adds, `DROP
COLUMN` removes, `CHANGE COLUMN` renames, `RENAME TABLE` and `DROP TABLE` move and remove whole
tables. Without the drops, every status column migration 005 retired would read as an undocumented
column and this test would be 20 lines of noise. Rollbacks are excluded for the reason
`test_model_schema.py` gives: one is never applied automatically, so letting it inform the schema
exempts columns no applied migration ever touched.

**The waivers are the point of the file, not an escape hatch.** Each one is a debt with a name and
a reason, and documenting a column without removing its waiver fails too
(`test_no_waiver_is_stale`), so the list can only shrink. It already has: #179's 25 are down to one
abandoned table's six, which is what the mechanism is for. The two that issue most wanted confirmed
rather than inferred — `commissions_history.osp` and `participation varchar(19)` — turned out to be
the *sales order's* salesperson (as against the customer's, which the column beside it carries) and
a snapshot of `commission_participation.name`, neither of them the legacy serialisation the column
types suggested. That is the argument for the rule the waivers encode: a confidently wrong
description is worse than an absent one, and both of those would have been confidently wrong — as
was the first answer written down for `osp`, which called it the *original* salesperson and had to
be corrected against the data.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DUMP = REPO_ROOT / 'docs' / 'mbe_schema.sql'
DICTIONARY = REPO_ROOT / 'docs' / 'data-dictionary.md'
MIGRATIONS = REPO_ROOT / 'migrations'

#: `CREATE TABLE `x` (…)` — in the dump, and in a migration for a table it creates.
CREATE_TABLE = re.compile(r'CREATE TABLE (?:IF NOT EXISTS )?`?(\w+)`?\s*\((.*?)\n\)', re.S)
#: A column definition line inside such a block.
COLUMN_LINE = re.compile(r'^\s+`(\w+)`\s', re.M)
#: A `### `table`` heading in the dictionary, and the column cells of the table under it.
SECTION = re.compile(r'^#{2,3} `(\w+)`', re.M)
DOCUMENTED_COLUMN = re.compile(r'^\|\s*`(\w+)`\s*\|', re.M)

#: Tables with no section at all. Legacy or scratch, and listed in #179 for completeness rather
#: than as a request — `temp_referencias` and `tmpConcreto` look like leftovers from a data load,
#: where the right fix is dropping the table rather than describing it.
UNDOCUMENTED_TABLES = frozenset(
    {
        'abc_classification',
        'details',
        'lead_time_purchase',
        'payments',
        'product_cost',
        'refunds',
        'special_receipt',
        'temp_referencias',
        'tmpConcreto',
    }
)

#: `table.column` pairs known to be undocumented, each because its meaning needs someone who knows
#: the legacy system rather than someone reading the column type. Removing a waiver is what closing
#: the gap looks like; nothing here may be added to without the same justification.
#:
#: **Empty, and that is the point.** #179 opened with 25. Six were plain FK pairs, thirteen were
#: resolved by asking the person who knows and checking each answer against `mbe_dev`, and the last
#: six went with their table when spec 016 retired the technical service module. The mechanism did
#: what it was built to do: `test_no_waiver_is_stale` refuses to let a waiver outlive its gap, so
#: dropping those tables from the schema *forced* this list to empty rather than leaving six
#: entries naming columns that no longer exist.
UNDOCUMENTED_COLUMNS: frozenset[str] = frozenset()


def _strip_comments(sql: str) -> str:
    """`--` lines and `/* … */` blocks, so a commented-out `DROP COLUMN` is not replayed."""
    return re.sub(r'/\*.*?\*/', '', re.sub(r'^\s*--.*$', '', sql, flags=re.M), flags=re.S)


def _live_schema() -> dict[str, list[str]]:
    """`{table: [column, …]}` as the deployment stands: the dump, with the migrations replayed."""
    tables = {
        match.group(1): list(dict.fromkeys(COLUMN_LINE.findall(match.group(2))))
        for match in CREATE_TABLE.finditer(SCHEMA_DUMP.read_text())
    }

    for path in sorted(MIGRATIONS.glob('*.sql')):
        if path.name.endswith('_rollback.sql'):
            continue
        for statement in _strip_comments(path.read_text()).split(';'):
            statement = statement.strip()
            if not statement:
                continue

            if created := re.match(
                r'CREATE TABLE (?:IF NOT EXISTS )?`?(\w+)`?\s*\((.*)', statement, re.S
            ):
                tables[created.group(1)] = list(
                    dict.fromkeys(COLUMN_LINE.findall(created.group(2)))
                )
            elif renamed := re.match(r'RENAME TABLE `?(\w+)`? TO `?(\w+)`?', statement, re.I):
                # Guarded on the source existing: the dump is already post-004, so replaying that
                # rename unguarded would pop nothing and blank out the table it renames *to*.
                if renamed.group(1) in tables:
                    tables[renamed.group(2)] = tables.pop(renamed.group(1))
            elif dropped := re.match(r'DROP TABLE (?:IF EXISTS )?`?(\w+)`?', statement, re.I):
                tables.pop(dropped.group(1), None)
            elif altered := re.match(r'ALTER TABLE `?(\w+)`?(.*)', statement, re.S | re.I):
                columns = tables.setdefault(altered.group(1), [])
                clauses = altered.group(2)
                for column in re.findall(
                    r'ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?`(\w+)`', clauses, re.I
                ):
                    if column not in columns:
                        columns.append(column)
                for old, new in re.findall(r'CHANGE\s+COLUMN\s+`(\w+)`\s+`(\w+)`', clauses, re.I):
                    if old in columns:
                        columns[columns.index(old)] = new
                    elif new not in columns:
                        columns.append(new)
                for column in re.findall(r'DROP\s+COLUMN\s+`(\w+)`', clauses, re.I):
                    if column in columns:
                        columns.remove(column)

    return tables


def _documented() -> dict[str, set[str]]:
    """`{table: {column, …}}` from the dictionary's `### `table`` sections."""
    text = DICTIONARY.read_text()
    sections: dict[str, set[str]] = {}
    for match in SECTION.finditer(text):
        end = text.find('\n### ', match.end())
        body = text[match.end() : end if end != -1 else len(text)]
        sections[match.group(1)] = set(DOCUMENTED_COLUMN.findall(body))
    return sections


LIVE = _live_schema()
DOCUMENTED = _documented()


def test_the_parse_found_a_plausible_schema() -> None:
    """A regex that silently matches nothing would make every assertion below vacuous."""
    assert len(LIVE) > 90
    assert len(DOCUMENTED) > 90
    assert LIVE['product_price'] == [
        'product_price_id',
        'product',
        'list',
        'price',
        'low_profit',
        'high_profit',
    ]


def test_the_migrations_were_replayed_not_merely_scanned() -> None:
    """`user.disabled` is dropped by 005, `user.profile` added by 014 — both after the dump."""
    assert 'disabled' not in LIVE['user']
    assert 'profile' in LIVE['user']
    assert 'store' not in LIVE and 'facility' in LIVE  # 004 renamed it, and the dump is post-004


@pytest.mark.parametrize('table', sorted(t for t in LIVE if t not in UNDOCUMENTED_TABLES))
def test_every_live_table_has_a_section(table: str) -> None:
    assert table in DOCUMENTED, (
        f'`{table}` exists in the schema but has no section in docs/data-dictionary.md. '
        'Add one, or waive it in UNDOCUMENTED_TABLES with a reason.'
    )


@pytest.mark.parametrize('table', sorted(t for t in LIVE if t not in UNDOCUMENTED_TABLES))
def test_every_live_column_is_documented(table: str) -> None:
    missing = [
        column
        for column in LIVE.get(table, [])
        if column not in DOCUMENTED.get(table, set())
        and f'{table}.{column}' not in UNDOCUMENTED_COLUMNS
    ]
    assert not missing, (
        f'`{table}` has columns the dictionary does not describe: {", ".join(missing)}. '
        'A migration that adds a column updates docs/data-dictionary.md in the same change '
        '(spec 014, T046).'
    )


def test_no_waiver_is_stale() -> None:
    """A waiver outliving the gap it names turns the list into decoration.

    Both directions: a column that has since been documented must lose its waiver, and one that
    no longer exists at all must lose it too — otherwise the file records debts nobody owes.
    """
    documented_anyway = sorted(
        pair
        for pair in UNDOCUMENTED_COLUMNS
        if (table := pair.split('.', 1)[0]) in DOCUMENTED
        and pair.split('.', 1)[1] in DOCUMENTED[table]
    )
    assert not documented_anyway, (
        f'Now documented — remove from UNDOCUMENTED_COLUMNS: {", ".join(documented_anyway)}'
    )

    gone = sorted(
        pair
        for pair in UNDOCUMENTED_COLUMNS
        if pair.split('.', 1)[1] not in LIVE.get(pair.split('.', 1)[0], [])
    )
    assert not gone, (
        f'No longer in the schema — remove from UNDOCUMENTED_COLUMNS: {", ".join(gone)}'
    )

    described = sorted(UNDOCUMENTED_TABLES & set(DOCUMENTED))
    assert not described, (
        f'Now has a section — remove from UNDOCUMENTED_TABLES: {", ".join(described)}'
    )

    vanished = sorted(UNDOCUMENTED_TABLES - set(LIVE))
    assert not vanished, (
        f'No longer in the schema — remove from UNDOCUMENTED_TABLES: {", ".join(vanished)}'
    )
