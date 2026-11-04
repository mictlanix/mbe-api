"""Lock helpers and lock order of the inventory documents (spec 029, T004; research R1, R2).

SQLite ignores row locks, so these run against a fake session that records every statement as
MariaDB would receive it (compiled with `sqlalchemy.dialects.mysql`), plus every commit and
rollback. What they pin is the order: pre-reads, commit, the document lock, the warehouse lock,
and only then ordinary reads such as `MAX(serial)`.
"""

from collections.abc import Callable
from decimal import Decimal
from types import SimpleNamespace

import pymysql
import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import OperationalError

from app.models.inventory import InventoryReceipt
from app.services import inventory_documents as inv
from app.services.inventory_documents import COUNT, ISSUE, RECEIPT

COMMIT = 'COMMIT'
ROLLBACK = 'ROLLBACK'


class FakeResult:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def all(self) -> list:
        return list(self._rows)

    def scalars(self) -> 'FakeResult':
        return FakeResult([row[0] if isinstance(row, tuple) else row for row in self._rows])

    def scalar_one_or_none(self):  # noqa: ANN201
        return self.scalars()._rows[0] if self._rows else None

    def scalar_one(self):  # noqa: ANN201
        return self.scalars()._rows[0]

    def first(self):  # noqa: ANN201
        return self._rows[0] if self._rows else None


class FakeSession:
    """Records each statement compiled for MariaDB; `respond(sql)` gives the rows it returns."""

    def __init__(self, respond: Callable[[str], list] = lambda sql: []) -> None:
        self.respond = respond
        self.log: list[str] = []
        self.statements: list = []
        self.added: list = []
        self.commit_error: Exception | None = None

    async def execute(self, statement):  # noqa: ANN001, ANN201
        compiled = statement.compile(
            dialect=mysql.dialect(), compile_kwargs={'literal_binds': True}
        )
        sql = str(compiled)
        self.log.append(sql)
        self.statements.append(statement)
        return FakeResult(self.respond(sql))

    async def commit(self) -> None:
        self.log.append(COMMIT)
        if self.commit_error is not None:
            raise self.commit_error

    async def rollback(self) -> None:
        self.log.append(ROLLBACK)

    async def refresh(self, _obj) -> None:  # noqa: ANN001
        pass

    def add(self, obj) -> None:  # noqa: ANN001
        self.added.append(obj)


def _is_lock(sql: str) -> bool:
    return sql.rstrip().endswith('FOR UPDATE')


def _receipt(**overrides) -> SimpleNamespace:
    values = dict(
        inventory_receipt_id=10,
        facility=1,
        warehouse=5,
        # A hand-made receipt: no purchase order, so no cost step (spec 031).
        purchase_order=None,
        completed=False,
        cancelled=False,
        serial=None,
        updater=1,
        modification_time=None,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _respond(document: SimpleNamespace, *, ledger: list[int] = (), extra=None):  # noqa: ANN001, ANN202
    """Transit map {1: 2, 3: 4}, the given ledger warehouses, and the locked document."""

    def respond(sql: str) -> list:
        if _is_lock(sql):
            return [document] if 'FROM inventory_' in sql else []
        if 'in_transit' in sql:
            return [(1, 2), (3, 4)]
        if 'FROM lot_serial_tracking' in sql and 'DISTINCT' in sql:
            return list(ledger)
        if extra is not None:
            return extra(sql)
        return []

    return respond


def _busy(code: int) -> OperationalError:
    return OperationalError('SELECT 1', {}, pymysql.err.OperationalError(code, 'busy'))


ADMIN = SimpleNamespace(administrator=True, facility_id=1, employee_id=1)


# ── lock_document ─────────────────────────────────────────────────────────────


async def test_lock_document_selects_the_header_by_key_for_update():
    document = _receipt()
    db = FakeSession(lambda sql: [document])

    assert await inv.lock_document(db, RECEIPT, 10) is document

    [sql] = db.log
    assert sql.startswith('SELECT')
    assert 'FROM inventory_receipt' in sql
    assert 'WHERE inventory_receipt.inventory_receipt_id = 10 FOR UPDATE' in sql
    assert db.statements[0].get_execution_options()['populate_existing'] is True


async def test_lock_document_of_a_vanished_row_is_404():
    with pytest.raises(HTTPException) as exc:
        await inv.lock_document(FakeSession(), RECEIPT, 10)
    assert exc.value.status_code == 404


# ── lock_warehouses ───────────────────────────────────────────────────────────


async def test_lock_warehouses_is_one_ordered_primary_key_lock():
    db = FakeSession()

    await inv.lock_warehouses(db, [9, 2, 5, 2, 9])

    [sql] = db.log
    assert 'FROM warehouse' in sql
    assert 'WHERE warehouse.warehouse_id IN (2, 5, 9)' in sql
    assert sql.endswith('ORDER BY warehouse.warehouse_id FOR UPDATE')
    assert 'facility' not in sql
    assert 'in_transit' not in sql


# ── next_serial (R1) ──────────────────────────────────────────────────────────


async def test_next_serial_is_an_ordinary_max_read_per_facility():
    db = FakeSession(lambda sql: [41])

    assert await inv.next_serial(db, InventoryReceipt, 3) == 42

    [sql] = db.log
    assert 'max(inventory_receipt.serial)' in sql
    assert 'WHERE inventory_receipt.facility = 3' in sql
    assert 'FOR UPDATE' not in sql
    assert 'LOCK IN SHARE MODE' not in sql


async def test_next_serial_of_a_facility_with_none_is_one():
    assert await inv.next_serial(FakeSession(lambda sql: [None]), InventoryReceipt, 3) == 1


# ── begin_locked (R2) ─────────────────────────────────────────────────────────


async def test_begin_locked_reads_commits_then_locks_document_then_warehouses():
    document = _receipt()
    db = FakeSession(_respond(document, ledger=[7, 5]))

    locked = await inv.begin_locked(db, RECEIPT, 10, stock=True, ledger=True)

    assert locked is document
    commit = db.log.index(COMMIT)
    before, after = db.log[:commit], db.log[commit + 1 :]
    # Pre-reads are plain reads: the transit map and this document's own ledger warehouses.
    assert len(before) == 2
    assert not any(_is_lock(sql) for sql in before)
    assert any('in_transit' in sql for sql in before)
    ledger_read = next(sql for sql in before if 'lot_serial_tracking' in sql)
    assert 'lot_serial_tracking.source = 4' in ledger_read
    assert 'lot_serial_tracking.reference = 10' in ledger_read
    # After the commit: the document lock, then the warehouse lock, and nothing in between.
    assert len(after) == 2
    assert 'FROM inventory_receipt' in after[0] and _is_lock(after[0])
    assert 'FROM warehouse' in after[1] and _is_lock(after[1])
    # The facility's transit (2), the header warehouse (5) and the ledger's warehouses (5, 7).
    assert 'IN (2, 5, 7)' in after[1]


async def test_begin_locked_without_stock_locks_the_document_only():
    db = FakeSession(_respond(_receipt()))

    await inv.begin_locked(db, RECEIPT, 10)

    assert db.log[0] == COMMIT
    assert len(db.log) == 2
    assert 'FROM inventory_receipt' in db.log[1] and _is_lock(db.log[1])


async def test_begin_locked_without_ledger_skips_the_ledger_read():
    db = FakeSession(_respond(_receipt()))

    await inv.begin_locked(db, RECEIPT, 10, stock=True)

    assert not any('lot_serial_tracking' in sql for sql in db.log)


async def test_warehouse_lock_follows_the_locked_row_not_the_copy_in_hand():
    stale = _receipt(warehouse=5)
    changed = _receipt(warehouse=8)  # another request moved the draft to warehouse 8
    db = FakeSession(_respond(changed))

    locked = await inv.begin_locked(db, RECEIPT, stale.inventory_receipt_id, stock=True)

    assert locked is changed
    assert 'IN (2, 8)' in db.log[-1]


async def test_begin_locked_refuses_a_facility_with_no_transit_after_locking_the_document():
    db = FakeSession(_respond(_receipt(facility=9)))

    with pytest.raises(HTTPException) as exc:
        await inv.begin_locked(db, RECEIPT, 10, stock=True)

    assert exc.value.status_code == 422
    assert exc.value.detail == 'Facility 9 has no in-transit location'
    assert exc.value.code == 'facility.no_in_transit_location'
    assert exc.value.params == {'facility_ids': [9]}
    assert not any('FROM warehouse' in sql and _is_lock(sql) for sql in db.log)


# ── busy_as_conflict (R2, known limits) ───────────────────────────────────────


@pytest.mark.parametrize('code', [1213, 1205])
async def test_deadlock_and_lock_wait_timeout_become_409_after_a_rollback(code):
    db = FakeSession()

    with pytest.raises(HTTPException) as exc:
        async with inv.busy_as_conflict(db):
            raise _busy(code)

    assert exc.value.status_code == 409
    assert exc.value.detail == 'The warehouse is busy, retry'
    assert exc.value.code == 'lock.busy'
    assert exc.value.params == {'resource': 'warehouse'}
    assert db.log == [ROLLBACK]


async def test_other_database_errors_are_re_raised():
    db = FakeSession()

    with pytest.raises(OperationalError):
        async with inv.busy_as_conflict(db):
            raise _busy(2013)


async def test_other_exceptions_are_re_raised_unchanged():
    db = FakeSession()
    refusal = HTTPException(status_code=409, detail='Document is cancelled')

    with pytest.raises(HTTPException) as exc:
        async with inv.busy_as_conflict(db):
            raise refusal

    assert exc.value is refusal


async def test_nothing_happens_when_the_block_succeeds():
    db = FakeSession()

    async with inv.busy_as_conflict(db):
        pass

    assert db.log == []


# ── The order inside a confirm (R1, R2) ───────────────────────────────────────


def _lines_and_products(sql: str) -> list:
    if 'FROM inventory_receipt_detail' in sql or 'FROM inventory_issue_detail' in sql:
        return [
            SimpleNamespace(receipt_detail_id=1, issue_detail_id=1, product=7, quantity=Decimal(2))
        ]
    if 'FROM product' in sql:
        return [SimpleNamespace(product_id=7, code='P7', stockable=True)]
    if 'max(' in sql:
        return [41]
    if 'sum(lot_serial_tracking.quantity)' in sql:
        return [Decimal(10)]
    return []


async def test_confirm_numbers_only_after_every_lock():
    document = _receipt()
    db = FakeSession(_respond(document, extra=_lines_and_products))

    await inv.confirm_simple(db, RECEIPT, 10, current=ADMIN)

    commits = [i for i, sql in enumerate(db.log) if sql == COMMIT]
    last_lock = max(i for i, sql in enumerate(db.log) if _is_lock(sql))
    serial = next(i for i, sql in enumerate(db.log) if 'max(' in sql)
    # Between ending the opening transaction and the last lock, only locks.
    assert all(_is_lock(sql) for sql in db.log[commits[0] + 1 : last_lock + 1])
    assert serial > last_lock
    assert 'FOR UPDATE' not in db.log[serial]
    assert document.serial == 42
    assert document.completed is True
    assert db.log[-1] == COMMIT


async def test_issue_confirm_checks_stock_after_the_locks():
    document = SimpleNamespace(
        inventory_issue_id=10,
        facility=1,
        warehouse=5,
        completed=False,
        cancelled=False,
        serial=None,
        updater=1,
        modification_time=None,
    )
    db = FakeSession(_respond(document, extra=_lines_and_products))

    await inv.confirm_simple(db, ISSUE, 10, current=ADMIN)

    last_lock = max(i for i, sql in enumerate(db.log) if _is_lock(sql))
    stock = [i for i, sql in enumerate(db.log) if 'sum(lot_serial_tracking.quantity)' in sql]
    assert stock and min(stock) > last_lock
    assert not any('lot_serial_tracking' in sql and _is_lock(sql) for sql in db.log)
    [entry] = db.added
    assert entry.quantity == Decimal(-2)


async def test_a_deadlock_at_commit_answers_409_and_numbers_nothing():
    document = _receipt()
    db = FakeSession(_respond(document, extra=_lines_and_products))
    db.commit_error = None

    async def commit() -> None:
        db.log.append(COMMIT)
        if db.log.count(COMMIT) == 2:
            raise _busy(1213)

    db.commit = commit

    with pytest.raises(HTTPException) as exc:
        await inv.confirm_simple(db, RECEIPT, 10, current=ADMIN)

    assert exc.value.status_code == 409
    assert db.log[-1] == ROLLBACK


async def test_cancel_reads_the_documents_ledger_warehouses_before_the_commit():
    document = _receipt(completed=True, serial=3)
    db = FakeSession(_respond(document, ledger=[6]))

    await inv.cancel(db, RECEIPT, 10, current=ADMIN)

    commit = db.log.index(COMMIT)
    assert any('lot_serial_tracking' in sql for sql in db.log[:commit])
    warehouse_lock = next(sql for sql in db.log if 'FROM warehouse' in sql and _is_lock(sql))
    assert 'IN (2, 5, 6)' in warehouse_lock


# ── Cancelling a confirmed count (spec 030, R1) ───────────────────────────────


def _confirmed_count() -> SimpleNamespace:
    return SimpleNamespace(
        inventory_count_id=10,
        facility=1,
        warehouse=5,
        completed=True,
        cancelled=False,
        serial=3,
        updater=1,
        modification_time=None,
    )


async def test_a_confirmed_count_is_refused_after_the_locks_and_before_any_ledger_work():
    count = _confirmed_count()
    db = FakeSession(_respond(count, ledger=[5]))

    with pytest.raises(HTTPException) as exc:
        await inv.cancel(db, COUNT, 10, current=ADMIN)

    assert exc.value.status_code == 409
    assert exc.value.detail == 'A confirmed count cannot be cancelled'
    assert exc.value.code == 'inventory_count.confirmed_cannot_cancel'
    commit = db.log.index(COMMIT)
    after = db.log[commit + 1 :]
    # The document lock, the warehouse lock, then nothing but the rollback.
    assert 'FROM inventory_count' in after[0] and _is_lock(after[0])
    assert 'FROM warehouse' in after[1] and _is_lock(after[1])
    assert after[2:] == [ROLLBACK]
    assert db.added == []
    assert count.cancelled is False


async def test_another_facility_learns_nothing_about_a_confirmed_count():
    db = FakeSession(_respond(_confirmed_count()))
    stranger = SimpleNamespace(administrator=False, facility_id=3, employee_id=9)

    with pytest.raises(HTTPException) as exc:
        await inv.cancel(db, COUNT, 10, current=stranger)

    assert exc.value.status_code == 403
