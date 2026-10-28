"""Lock order of every step that numbers a document per facility (spec 035, research R2, R3, R8).

SQLite ignores row locks, so these run against spec 029's fake session, which records every
statement as MariaDB would receive it, plus every commit and rollback. Each step is driven until its
first ordinary read after the commit, where `StepSession` stops it: what is pinned is everything
before that read. The commit, then the step's own rows `FOR UPDATE`, its lines `FOR SHARE`, the
facility row, the warehouses ascending, and only then the read that opens the snapshot.
"""

import re
from decimal import Decimal
from types import SimpleNamespace

import pymysql
import pytest
from fastapi import HTTPException
from sqlalchemy.exc import OperationalError

from app.services import delivery_order_service, sales_order_service, sales_quote_service
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
        sql = self.log[-1] = ' '.join(self.log[-1].split())
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


# ── Shared fixtures ───────────────────────────────────────────────────────────

CASHIER = SimpleNamespace(
    administrator=False, facility_id=51, employee_id=8, user_id='cashier', point_sale=None
)


def _table(sql: str) -> str | None:
    """The table a single-table `SELECT … FROM <table> …` reads."""
    match = re.search(r'\bFROM (\w+)', sql)
    return match.group(1) if match else None


def responder(rows: dict[str, list], *, busy_on: str | None = None, code: int = 1213):  # noqa: ANN201
    """Answer each statement with `rows[table]`; raise a lock error on the first statement whose
    table is `busy_on` and which locks."""

    def respond(sql: str) -> list:
        table = _table(sql)
        if busy_on is not None and table == busy_on and is_lock(sql):
            raise busy(code)
        return list(rows.get(table, []))

    return respond


# ── Sales order confirm (T013; research R4) ───────────────────────────────────

def _order(**overrides) -> SimpleNamespace:
    values = dict(
        sales_order_id=7,
        facility=51,
        customer=300,
        completed=False,
        cancelled=False,
        origin=0,
        payment_terms=0,
        serial=None,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _line(warehouse: int | None, product: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        sales_order_detail_id=100 + (warehouse or 0),
        sales_order=7,
        product=product,
        warehouse=warehouse,
        quantity=Decimal(1),
        price=Decimal(10),
    )


def _order_rows(order: SimpleNamespace, lines: list) -> dict[str, list]:
    return {'sales_order': [order], 'sales_order_detail': lines, 'facility': [(order.facility,)]}


async def test_confirm_order_commits_then_locks_order_lines_facility_warehouses_then_reads():
    order = _order()
    db = StepSession(responder(_order_rows(order, [_line(9), _line(4), _line(None)])))

    with pytest.raises(StopAtFirstRead):
        await sales_order_service.confirm_order(db, order, current=CASHIER)

    commit, locked, lines, facility, warehouses = assert_order(
        db.log,
        f'^{COMMIT}$',
        r'FROM sales_order WHERE .* FOR UPDATE$',
        r'FROM sales_order_detail WHERE .* LOCK IN SHARE MODE$',
        r'FROM facility WHERE facility\.facility_id = 51 FOR UPDATE$',
        r'FROM warehouse WHERE warehouse\.warehouse_id IN \(4, 9\) .*FOR UPDATE$',
    )
    assert commit == 0
    assert warehouses + 1 == first_plain_select(db.log)
    assert db.statements[locked - 1].get_execution_options()['populate_existing'] is True
    assert db.statements[lines - 1].get_execution_options()['populate_existing'] is True


async def test_confirm_order_locks_nothing_wider_than_its_facility_and_line_warehouses():
    """FR-020: a line on another facility's warehouse adds that warehouse, not its facility."""
    order = _order()
    db = StepSession(responder(_order_rows(order, [_line(31), _line(5)])))

    with pytest.raises(StopAtFirstRead):
        await sales_order_service.confirm_order(db, order, current=CASHIER)

    facility_locks = [sql for sql in db.log if 'FROM facility' in sql and is_lock(sql)]
    warehouse_locks = [sql for sql in db.log if 'FROM warehouse' in sql and is_lock(sql)]
    assert facility_locks == [
        next(sql for sql in db.log if 'facility.facility_id = 51 FOR UPDATE' in sql)
    ]
    assert len(warehouse_locks) == 1
    assert 'IN (5, 31)' in warehouse_locks[0]


async def test_confirm_order_without_stock_lines_takes_no_warehouse_lock():
    order = _order()
    db = StepSession(responder(_order_rows(order, [_line(None)])))

    with pytest.raises(StopAtFirstRead):
        await sales_order_service.confirm_order(db, order, current=CASHIER)

    assert not any('FROM warehouse' in sql for sql in db.log)


async def test_a_second_confirm_waits_on_the_order_then_is_refused_with_no_serial():
    """FR-019: the copy in hand is a draft; the locked row is already completed."""
    stale = _order()
    db = StepSession(responder(_order_rows(_order(completed=True), [_line(9)])))

    with pytest.raises(HTTPException) as exc:
        await sales_order_service.confirm_order(db, stale, current=CASHIER)

    assert exc.value.status_code == 409
    assert exc.value.detail == 'Document is completed and can no longer be edited'
    assert db.log[-1] == ROLLBACK
    assert not any('FROM facility' in sql or 'max(' in sql for sql in db.log)


@pytest.mark.parametrize('code', [1213, 1205])
async def test_confirm_order_deadlock_or_lock_timeout_is_a_409(code):
    order = _order()
    db = StepSession(
        responder(_order_rows(order, [_line(9)]), busy_on='warehouse', code=code)
    )

    with pytest.raises(HTTPException) as exc:
        await sales_order_service.confirm_order(db, order, current=CASHIER)

    assert exc.value.status_code == 409
    assert exc.value.detail == 'The warehouse is busy, retry'
    assert db.log[-1] == ROLLBACK


# ── Quote and delivery order confirm (T014) ───────────────────────────────────


def _draft(key: str, **overrides) -> SimpleNamespace:
    values = {key: 12, 'facility': 51, 'completed': False, 'cancelled': False, 'serial': None}
    values.update(overrides)
    return SimpleNamespace(**values)


async def test_confirm_quote_commits_then_locks_quote_then_facility_then_reads():
    quote = _draft('sales_quote_id')
    db = StepSession(responder({'sales_quote': [quote], 'facility': [(51,)]}))

    with pytest.raises(StopAtFirstRead) as stop:
        await sales_quote_service.confirm_quote(db, quote, current=CASHIER)

    commit, locked, _ = assert_order(
        db.log,
        f'^{COMMIT}$',
        r'FROM sales_quote WHERE sales_quote\.sales_quote_id = 12 FOR UPDATE$',
        r'FROM facility WHERE facility\.facility_id = 51 FOR UPDATE$',
    )
    assert commit == 0
    assert 'max(sales_quote.serial)' in str(stop.value)
    assert db.statements[locked - 1].get_execution_options()['populate_existing'] is True
    assert not any('FROM warehouse' in sql for sql in db.log)


async def test_confirm_quote_already_completed_under_the_lock_is_refused():
    db = StepSession(
        responder({'sales_quote': [_draft('sales_quote_id', completed=True)], 'facility': [(51,)]})
    )

    with pytest.raises(HTTPException) as exc:
        await sales_quote_service.confirm_quote(db, _draft('sales_quote_id'), current=CASHIER)

    assert exc.value.status_code == 409
    assert db.log[-1] == ROLLBACK
    assert not any('FROM facility' in sql for sql in db.log)


@pytest.mark.parametrize('code', [1213, 1205])
async def test_confirm_quote_busy_facility_is_a_409(code):
    quote = _draft('sales_quote_id')
    db = StepSession(
        responder({'sales_quote': [quote], 'facility': [(51,)]}, busy_on='facility', code=code)
    )

    with pytest.raises(HTTPException) as exc:
        await sales_quote_service.confirm_quote(db, quote, current=CASHIER)

    assert exc.value.status_code == 409
    assert exc.value.detail == 'The facility is busy, retry'
    assert db.log[-1] == ROLLBACK


async def test_confirm_delivery_order_commits_then_locks_it_then_facility_then_reads():
    order = _draft('delivery_order_id', status=0, date=None, rejection_reason=None)
    db = StepSession(responder({'delivery_order': [order], 'facility': [(51,)]}))

    with pytest.raises(StopAtFirstRead):
        await delivery_order_service.confirm(db, order, current=CASHIER)

    commit, locked, facility = assert_order(
        db.log,
        f'^{COMMIT}$',
        r'FROM delivery_order WHERE delivery_order\.delivery_order_id = 12 FOR UPDATE$',
        r'FROM facility WHERE facility\.facility_id = 51 FOR UPDATE$',
    )
    assert commit == 0
    assert facility + 1 == first_plain_select(db.log)
    assert db.statements[locked - 1].get_execution_options()['populate_existing'] is True
    assert not any('FROM warehouse' in sql for sql in db.log)


async def test_confirm_delivery_order_busy_facility_is_a_409():
    order = _draft('delivery_order_id', status=0, date=None, rejection_reason=None)
    db = StepSession(
        responder({'delivery_order': [order], 'facility': [(51,)]}, busy_on='facility')
    )

    with pytest.raises(HTTPException) as exc:
        await delivery_order_service.confirm(db, order, current=CASHIER)

    assert exc.value.detail == 'The facility is busy, retry'
    assert db.log[-1] == ROLLBACK
