"""Lock order of every step that numbers a document per facility (spec 035, research R2, R3, R8).

SQLite ignores row locks, so these run against spec 029's fake session, which records every
statement as MariaDB would receive it, plus every commit and rollback. Each step is driven until its
first ordinary read after the commit, where `StepSession` stops it: what is pinned is everything
before that read. The commit, then the step's own rows `FOR UPDATE`, its lines `FOR SHARE`, the
facility row, the warehouses ascending, and only then the read that opens the snapshot.
"""

import re

import pymysql
from sqlalchemy.exc import OperationalError

from tests.unit.test_inventory_locks import COMMIT, ROLLBACK, FakeResult, FakeSession

__all__ = ['COMMIT', 'ROLLBACK', 'FakeResult']


class StopAtFirstRead(Exception):
    """Raised by `StepSession` at the step's first ordinary read after its commit."""


def is_lock(sql: str) -> bool:
    return sql.rstrip().endswith(('FOR UPDATE', 'LOCK IN SHARE MODE'))


def is_share(sql: str) -> bool:
    return sql.rstrip().endswith('LOCK IN SHARE MODE')


class StepSession(FakeSession):
    """A fake session that answers pre-reads and locks through `respond`, and stops the step at
    its first plain `SELECT` after the commit.

    `objects` answers `db.get(model, key)` by `(table name, key)`.
    """

    def __init__(self, respond, objects: dict | None = None) -> None:  # noqa: ANN001
        super().__init__(respond)
        self.objects = objects or {}
        self.flushes = 0

    async def execute(self, statement):  # noqa: ANN001, ANN201
        result = await super().execute(statement)
        sql = self.log[-1]
        if COMMIT in self.log and sql.startswith('SELECT') and not is_lock(sql):
            raise StopAtFirstRead(sql)
        return result

    async def get(self, model, key):  # noqa: ANN001, ANN201
        self.log.append(f'GET {model.__tablename__} {key}')
        return self.objects.get((model.__tablename__, key))

    async def flush(self) -> None:
        self.flushes += 1


def index_of(log: list[str], pattern: str, start: int = 0) -> int:
    """Index of the first entry at or after `start` that matches the regex `pattern`."""
    for i in range(start, len(log)):
        if re.search(pattern, log[i]):
            return i
    raise AssertionError(f'{pattern!r} not found after entry {start} in:\n' + '\n'.join(log))


def assert_order(log: list[str], *patterns: str) -> list[int]:
    """Assert the patterns match entries of `log` in this order; return their indexes."""
    found, start = [], 0
    for pattern in patterns:
        i = index_of(log, pattern, start)
        found.append(i)
        start = i + 1
    return found


def first_plain_select(log: list[str]) -> int:
    """Index of the first `SELECT` without a lock clause after the commit."""
    commit = log.index(COMMIT)
    for i in range(commit + 1, len(log)):
        if log[i].startswith('SELECT') and not is_lock(log[i]):
            return i
    raise AssertionError('no plain SELECT after the commit')


def busy(code: int) -> OperationalError:
    return OperationalError('SELECT 1', {}, pymysql.err.OperationalError(code, 'busy'))
