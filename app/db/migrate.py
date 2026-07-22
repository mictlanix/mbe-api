"""Apply hand-written SQL migrations from the `migrations/` directory.

    uv run python -m app.db.migrate            # apply everything pending
    uv run python -m app.db.migrate status     # report applied vs pending
    uv run python -m app.db.migrate mark V...  # record as applied without executing

Migrations are `migrations/NNN_name.sql`, applied in ascending numeric-prefix order and
recorded in the `schema_migrations` table of the target database. Files ending in
`_rollback.sql` are never applied automatically -- run them by hand.

MariaDB does not roll back DDL, so a file that fails partway leaves earlier statements
applied. The runner reports the exact statement that failed and does not record the
migration, leaving it pending for a human to resolve.

Limitation: `DELIMITER` blocks (stored procedures, triggers, functions) are not supported
by the statement splitter. The current corpus contains none.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.session import engine

MIGRATIONS_DIR = Path('migrations')

_PREFIX_RE = re.compile(r'^(\d+)_')

_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS `schema_migrations` (
  `version`    VARCHAR(255) NOT NULL,
  `checksum`   CHAR(64)     NOT NULL,
  `applied_at` DATETIME     NOT NULL,
  PRIMARY KEY (`version`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


class MigrationError(Exception):
    """A problem with the migration files or the operator's request."""


@dataclass(frozen=True)
class Migration:
    prefix: int
    version: str
    path: Path

    def checksum(self) -> str:
        return hashlib.sha256(self.path.read_bytes()).hexdigest()


@dataclass(frozen=True)
class AppliedRecord:
    checksum: str
    applied_at: str


def split_statements(sql: str) -> list[str]:
    """Split a SQL file into individual statements.

    Strips `--` line comments and splits on `;`, ignoring semicolons inside single-quoted,
    double-quoted, or backtick-quoted spans. Backslash escapes inside quotes are honored.
    """
    statements: list[str] = []
    current: list[str] = []
    quote: str | None = None
    i = 0
    length = len(sql)

    while i < length:
        char = sql[i]

        if quote is not None:
            current.append(char)
            if char == '\\' and i + 1 < length:
                current.append(sql[i + 1])
                i += 2
                continue
            if char == quote:
                quote = None
            i += 1
            continue

        if char in ("'", '"', '`'):
            quote = char
            current.append(char)
            i += 1
            continue

        if char == '-' and sql.startswith('--', i):
            newline = sql.find('\n', i)
            if newline == -1:
                break
            current.append('\n')
            i = newline + 1
            continue

        if char == ';':
            statements.append(''.join(current))
            current = []
            i += 1
            continue

        current.append(char)
        i += 1

    statements.append(''.join(current))
    return [s.strip() for s in statements if s.strip()]


def discover(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    """Find migrations in `directory`, ordered by numeric prefix.

    Rollback files are excluded. Raises MigrationError on a duplicate prefix or a `.sql`
    file without a numeric prefix -- both are ambiguities the operator must resolve.
    """
    migrations: list[Migration] = []
    seen: dict[int, Path] = {}

    for path in sorted(directory.glob('*.sql')):
        if path.name.endswith('_rollback.sql'):
            continue

        match = _PREFIX_RE.match(path.name)
        if match is None:
            raise MigrationError(
                f'{path.name} has no numeric ordering prefix. '
                f'Migrations must be named NNN_description.sql'
            )

        prefix = int(match.group(1))
        if prefix in seen:
            raise MigrationError(
                f'Duplicate ordering prefix {match.group(1)}: '
                f'{seen[prefix].name} and {path.name}. Renumber one of them.'
            )

        seen[prefix] = path
        migrations.append(Migration(prefix=prefix, version=path.stem, path=path))

    migrations.sort(key=lambda m: m.prefix)
    return migrations


async def _ledger_exists(conn: AsyncConnection) -> bool:
    result = await conn.execute(
        text(
            'SELECT COUNT(*) FROM information_schema.tables '
            "WHERE table_schema = DATABASE() AND table_name = 'schema_migrations'"
        )
    )
    return bool(result.scalar())


async def _ensure_ledger(conn: AsyncConnection) -> None:
    await conn.execute(text(_LEDGER_DDL))
    await conn.commit()


async def _fetch_applied(conn: AsyncConnection) -> dict[str, AppliedRecord]:
    result = await conn.execute(text('SELECT version, checksum, applied_at FROM schema_migrations'))
    return {
        row[0]: AppliedRecord(checksum=row[1], applied_at=str(row[2])) for row in result.fetchall()
    }


async def _record(conn: AsyncConnection, migration: Migration) -> None:
    await conn.execute(
        text(
            'INSERT INTO schema_migrations (version, checksum, applied_at) '
            'VALUES (:version, :checksum, UTC_TIMESTAMP())'
        ),
        {'version': migration.version, 'checksum': migration.checksum()},
    )
    await conn.commit()


def _plural(count: int, noun: str = 'migration') -> str:
    return f'{count} {noun}' if count == 1 else f'{count} {noun}s'


def _warn_drift(migrations: list[Migration], applied: dict[str, AppliedRecord]) -> None:
    """Report altered or missing files. Never blocks -- see specs/009-sql-migrations."""
    by_version = {m.version: m for m in migrations}

    for version, record in sorted(applied.items()):
        migration = by_version.get(version)
        if migration is None:
            print(
                f'warning: {version} is recorded as applied but has no file on disk',
                file=sys.stderr,
            )
        elif migration.checksum() != record.checksum:
            print(
                f'warning: {version} has changed since it was applied',
                file=sys.stderr,
            )


async def _apply(conn: AsyncConnection) -> int:
    migrations = discover()
    await _ensure_ledger(conn)
    applied = await _fetch_applied(conn)
    _warn_drift(migrations, applied)

    pending = [m for m in migrations if m.version not in applied]
    if not pending:
        print(f'Database is up to date ({_plural(len(applied))} applied).')
        return 0

    for migration in pending:
        statements = split_statements(migration.path.read_text())
        print(f'Applying {migration.version} ... ', end='', flush=True)

        for index, statement in enumerate(statements, start=1):
            try:
                await conn.execute(text(statement))
            except Exception as exc:  # noqa: BLE001 - reported verbatim to the operator
                print('FAILED\n', file=sys.stderr)
                print(f'  file:      {migration.path}', file=sys.stderr)
                print(f'  statement: {index} of {len(statements)}', file=sys.stderr)
                print(f'  sql:       {statement}', file=sys.stderr)
                print(f'  error:     {exc}', file=sys.stderr)
                print(
                    f'\nMariaDB does not roll back DDL. Statements 1-{index - 1} of this file '
                    f'have been applied and {migration.version} was NOT recorded. '
                    f'Inspect the database before re-running.',
                    file=sys.stderr,
                )
                return 1

        await _record(conn, migration)
        print(f'ok ({_plural(len(statements), "statement")})')

    print(f'Applied {_plural(len(pending))}.')
    return 0


async def _status(conn: AsyncConnection) -> int:
    migrations = discover()
    applied = await _fetch_applied(conn) if await _ledger_exists(conn) else {}

    if not applied:
        for migration in migrations:
            print(f'  pending  {migration.version}')
        print(f'\n{_plural(len(migrations))} pending. This database has never been migrated.')
        return 0

    print()
    for migration in migrations:
        record = applied.get(migration.version)
        if record is None:
            print(f'  pending  {migration.version}')
        elif migration.checksum() != record.checksum:
            print(
                f'  ALTERED  {migration.version}  {record.applied_at}'
                '  (file changed since it was applied)'
            )
        else:
            print(f'  applied  {migration.version}  {record.applied_at}')

    known = {m.version for m in migrations}
    for version, record in sorted(applied.items()):
        if version not in known:
            print(f'  MISSING  {version}  {record.applied_at}  (recorded but no file on disk)')

    pending = [m for m in migrations if m.version not in applied]
    print(f'\n{_plural(len(pending))} pending.')
    return 0


async def _mark(conn: AsyncConnection, versions: list[str]) -> int:
    migrations = {m.version: m for m in discover()}

    unknown = [v for v in versions if v not in migrations]
    if unknown:
        print(f'error: no migration file for {", ".join(unknown)}', file=sys.stderr)
        return 1

    await _ensure_ledger(conn)
    applied = await _fetch_applied(conn)

    already = [v for v in versions if v in applied]
    if already:
        print(f'error: already recorded as applied: {", ".join(already)}', file=sys.stderr)
        return 1

    for version in versions:
        await _record(conn, migrations[version])
        print(f'Marked {version} as applied (not executed).')
    return 0


async def _run(args: argparse.Namespace) -> int:
    try:
        async with engine.connect() as conn:
            if args.command == 'status':
                return await _status(conn)
            if args.command == 'mark':
                return await _mark(conn, args.versions)
            return await _apply(conn)
    finally:
        # Return pooled connections before asyncio.run() tears the loop down, otherwise
        # aiomysql's __del__ fires against a closed loop and prints a spurious traceback.
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(
        prog='python -m app.db.migrate',
        description='Apply SQL migrations from migrations/ to the configured database.',
    )
    subparsers = parser.add_subparsers(dest='command')
    subparsers.add_parser('status', help='report which migrations this database has received')
    mark = subparsers.add_parser('mark', help='record migrations as applied without executing them')
    mark.add_argument('versions', nargs='+', help='e.g. 004_facility_rename')

    args = parser.parse_args()
    try:
        return asyncio.run(_run(args))
    except MigrationError as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
