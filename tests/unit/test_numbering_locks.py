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

from app.enums import ItineraryStatus, StopOutcome
from app.schemas.customer_payment import CustomerPaymentCreate
from app.schemas.sales_order import SalesOrderLineCreate, SalesOrderLineUpdate, SalesOrderUpdate
from app.services import (
    customer_payment_service,
    customer_refund_service,
    delivery_itinerary_service,
    delivery_order_service,
    sales_order_service,
    sales_quote_service,
)
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

    def __init__(self, respond, objects: dict | None = None, *, stop: bool = True) -> None:  # noqa: ANN001
        super().__init__(respond)
        self.objects = objects or {}
        self.flushes = 0
        self.stop = stop

    async def execute(self, statement):  # noqa: ANN001, ANN201
        result = await super().execute(statement)
        sql = self.log[-1] = ' '.join(self.log[-1].split())
        if self.stop and COMMIT in self.log and sql.startswith('SELECT') and not is_lock(sql):
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
    db = StepSession(responder(_order_rows(order, [_line(9)]), busy_on='warehouse', code=code))

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


# ── Customer refund confirm (T015; research R3) ───────────────────────────────


def _refund_rows(*, session_end=None, lines=(9, 4)) -> tuple[dict, dict]:
    session = SimpleNamespace(cash_session_id=77, cashier=8, end=session_end)
    refund = _draft('customer_refund_id', sales_order=7, customer=300, facility=51)
    refund_lines = [
        SimpleNamespace(
            customer_refund_detail_id=500 + i,
            customer_refund=12,
            sales_order_detail=600 + i,
            product=1,
            warehouse=warehouse,
            quantity=Decimal(1),
        )
        for i, warehouse in enumerate(lines)
    ]
    rows = {
        'cash_session': [session],
        'customer_refund': [refund],
        'sales_order': [_order(completed=True)],
        'customer_refund_detail': refund_lines,
        'facility': [(51,)],
    }
    objects = {
        ('sales_order_detail', line.sales_order_detail): SimpleNamespace(
            sales_order_detail_id=line.sales_order_detail, quantity=Decimal(1)
        )
        for line in refund_lines
    }
    return rows, objects


async def test_confirm_refund_locks_session_refund_order_lines_facility_warehouses_then_reads():
    rows, objects = _refund_rows()
    db = StepSession(responder(rows), objects)

    with pytest.raises(StopAtFirstRead):
        await customer_refund_service.confirm_refund(
            db, rows['customer_refund'][0], payout=1, current=CASHIER
        )

    commit = db.log.index(COMMIT)
    # The pre-read: the cashier's open session, a plain read before the commit.
    assert any('FROM cash_session' in sql and not is_lock(sql) for sql in db.log[:commit])
    _, refund, order, lines, _, warehouses = assert_order(
        db.log,
        r'FROM cash_session WHERE cash_session\.cash_session_id = 77 FOR UPDATE$',
        r'FROM customer_refund WHERE .* FOR UPDATE$',
        r'FROM sales_order WHERE sales_order\.sales_order_id = 7 FOR UPDATE$',
        r'FROM customer_refund_detail WHERE .* LOCK IN SHARE MODE$',
        r'FROM facility WHERE facility\.facility_id = 51 FOR UPDATE$',
        r'FROM warehouse WHERE warehouse\.warehouse_id IN \(4, 9\) .*FOR UPDATE$',
    )
    assert commit < db.log.index(
        next(sql for sql in db.log if 'FROM cash_session' in sql and is_lock(sql))
    )
    assert warehouses < first_plain_select(db.log)
    for i in (refund, order, lines):
        assert db.statements[i - 1].get_execution_options()['populate_existing'] is True


async def test_confirm_refund_into_a_session_closed_meanwhile_is_refused():
    rows, objects = _refund_rows(session_end='2026-10-28 12:00')
    db = StepSession(responder(rows), objects)

    with pytest.raises(HTTPException) as exc:
        await customer_refund_service.confirm_refund(
            db, rows['customer_refund'][0], payout=1, current=CASHIER
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == 'The cash session is closed'
    assert db.log[-1] == ROLLBACK
    assert not any('FROM customer_refund ' in sql and is_lock(sql) for sql in db.log)


@pytest.mark.parametrize('code', [1213, 1205])
async def test_confirm_refund_deadlock_or_lock_timeout_is_a_409(code):
    rows, objects = _refund_rows()
    db = StepSession(responder(rows, busy_on='warehouse', code=code), objects)

    with pytest.raises(HTTPException) as exc:
        await customer_refund_service.confirm_refund(
            db, rows['customer_refund'][0], payout=1, current=CASHIER
        )

    assert exc.value.detail == 'The warehouse is busy, retry'
    assert db.log[-1] == ROLLBACK


# ── Itinerary stop close (T016; research R5) ──────────────────────────────────


def _stop_rows(*, outcome=StopOutcome.PENDING, status=ItineraryStatus.DEPARTED) -> dict:
    return {
        # Pre-read: (delivery order facility, dispatch warehouse, sales line warehouse).
        'deliveries_itinerary_detail': [(8, 20, 21), (3, 30, None), (8, 20, 21)],
        # Pre-read: dispatch warehouse → its facility's in-transit warehouse.
        'warehouse': [(20, 25), (30, 35)],
        'deliveries_itinerary': [SimpleNamespace(deliveries_itinerary_id=4, status=status)],
        'deliveries_itinerary_stop': [
            SimpleNamespace(
                deliveries_itinerary_stop_id=40, deliveries_itinerary=4, outcome=outcome
            )
        ],
        'facility': [(3,)],
    }


async def _close(db: StepSession, rows: dict) -> None:
    await delivery_itinerary_service.close_stop(
        db,
        rows['deliveries_itinerary'][0],
        rows['deliveries_itinerary_stop'][0],
        outcomes=[],
        receiver_name='R',
        receiver_id_shown='ID',
        image_file='x.png',
        current=CASHIER,
    )


async def test_close_stop_pre_reads_commits_then_locks_itinerary_stop_facilities_warehouses():
    rows = _stop_rows()
    db = StepSession(responder(rows))

    with pytest.raises(StopAtFirstRead):
        await _close(db, rows)

    commit = db.log.index(COMMIT)
    before = db.log[:commit]
    assert not any(is_lock(sql) for sql in before)
    assert any('FROM deliveries_itinerary_detail' in sql for sql in before)
    assert any('in_transit' in sql for sql in before)
    itinerary, stop, _, _, warehouses = assert_order(
        db.log[commit:],
        r'FROM deliveries_itinerary WHERE deliveries_itinerary\.deliveries_itinerary_id = 4 '
        r'FOR UPDATE$',
        r'FROM deliveries_itinerary_stop WHERE .*= 40 FOR UPDATE$',
        r'FROM facility WHERE facility\.facility_id = 3 FOR UPDATE$',
        r'FROM facility WHERE facility\.facility_id = 8 FOR UPDATE$',
        # Dispatch 20 and 30, their facilities' transit 25 and 35, the sales line's 21.
        r'FROM warehouse WHERE warehouse\.warehouse_id IN \(20, 21, 25, 30, 35\) .*FOR UPDATE$',
    )
    assert commit + warehouses < first_plain_select(db.log)
    for i in (itinerary, stop):
        assert db.statements[commit + i - 1].get_execution_options()['populate_existing'] is True


async def test_close_stop_of_a_stop_resolved_meanwhile_is_refused():
    """The copy in hand is pending; the locked row was closed by the request it waited on."""
    rows = _stop_rows()
    pending = rows['deliveries_itinerary_stop'][0]
    rows['deliveries_itinerary_stop'] = [
        SimpleNamespace(
            deliveries_itinerary_stop_id=40, deliveries_itinerary=4, outcome=StopOutcome.DELIVERED
        )
    ]
    db = StepSession(responder(rows))

    with pytest.raises(HTTPException) as exc:
        await delivery_itinerary_service.close_stop(
            db,
            rows['deliveries_itinerary'][0],
            pending,
            outcomes=[],
            receiver_name='R',
            receiver_id_shown='ID',
            image_file='x.png',
            current=CASHIER,
        )

    assert exc.value.detail == 'This stop is already resolved'
    assert db.log[-1] == ROLLBACK


async def test_close_stop_deadlock_is_a_409():
    rows = _stop_rows()
    db = StepSession(responder(rows, busy_on='facility'))

    with pytest.raises(HTTPException) as exc:
        await _close(db, rows)

    assert exc.value.detail == 'The warehouse is busy, retry'
    assert db.log[-1] == ROLLBACK


# ── Sales order edits (T024; research R3) ─────────────────────────────────────

_LINE = _line(9)


def _edit(name: str):  # noqa: ANN202
    order = _order()
    return {
        'update_order': lambda db, o=order: sales_order_service.update_order(
            db, o, SalesOrderUpdate(comment='x'), current=CASHIER
        ),
        'add_line': lambda db, o=order: sales_order_service.add_line(
            db, o, SalesOrderLineCreate(product=1), current=CASHIER
        ),
        'update_line': lambda db, o=order: sales_order_service.update_line(
            db, o, _LINE, SalesOrderLineUpdate(comment='x'), current=CASHIER
        ),
        'remove_line': lambda db, o=order: sales_order_service.remove_line(
            db, o, _LINE, current=CASHIER
        ),
    }[name]


EDITS = ['update_order', 'add_line', 'update_line', 'remove_line']


@pytest.mark.parametrize('name', EDITS)
async def test_an_edit_commits_then_locks_the_order_before_anything_else(name):
    objects = {
        ('product', 1): SimpleNamespace(product_id=1, min_order_qty=1),
        ('customer', 300): SimpleNamespace(customer_id=300, price_list=1),
    }
    db = StepSession(responder({'sales_order': [_order()], 'sales_order_detail': [_LINE]}), objects)

    with pytest.raises(StopAtFirstRead) as stop:
        await _edit(name)(db)

    commit, locked = assert_order(db.log, f'^{COMMIT}$', r'FROM sales_order WHERE .* FOR UPDATE$')
    assert commit == 0
    assert db.statements[locked - 1].get_execution_options()['populate_existing'] is True
    assert not any('FROM facility' in sql or 'FROM warehouse' in sql for sql in db.log)
    if name in ('update_line', 'remove_line'):
        # The line handed in was read before the commit: it is re-read under the order's lock.
        assert 'FROM sales_order_detail WHERE sales_order_detail.sales_order_detail_id = 109' in (
            str(stop.value)
        )
        assert db.statements[-1].get_execution_options()['populate_existing'] is True


@pytest.mark.parametrize('name', EDITS)
async def test_an_edit_that_waited_behind_a_confirmation_is_refused(name):
    """FR-019: the copy in hand is a draft; the locked row is completed."""
    db = StepSession(
        responder({'sales_order': [_order(completed=True)], 'sales_order_detail': [_LINE]})
    )

    with pytest.raises(HTTPException) as exc:
        await _edit(name)(db)

    assert exc.value.status_code == 409
    assert exc.value.detail == 'Document is completed and can no longer be edited'
    # Nothing written; the request's session rolls the lock back when it closes, as today.
    assert db.added == []
    assert db.flushes == 0


@pytest.mark.parametrize('name', ['update_line', 'remove_line'])
async def test_a_line_deleted_meanwhile_is_a_404(name):
    db = StepSession(responder({'sales_order': [_order()], 'sales_order_detail': []}), stop=False)

    with pytest.raises(HTTPException) as exc:
        await _edit(name)(db)

    assert exc.value.status_code == 404
    assert exc.value.detail == 'Line not found'
    assert db.added == []


# ── Customer payment create (T028; research R6) ───────────────────────────────


def _payment_session(*, session_id: int | None = 77, end=None, busy_on: str | None = None):  # noqa: ANN202
    """The pre-read returns the open session's id; its lock returns the session row."""
    locked = SimpleNamespace(cash_session_id=session_id, cashier=8, end=end)

    def respond(sql: str) -> list:
        table = _table(sql)
        if busy_on is not None and table == busy_on and is_lock(sql):
            raise busy(1213)
        if table == 'cash_session':
            return [locked] if is_lock(sql) else ([session_id] if session_id else [])
        if table == 'facility':
            return [(51,)]
        return []

    customer = {('customer', 300): SimpleNamespace(customer_id=300)}
    return StepSession(respond, customer)


def _payment() -> CustomerPaymentCreate:
    return CustomerPaymentCreate(customer=300, amount=Decimal(100), method=1, payment_type=1)


async def test_create_payment_pre_reads_commits_locks_session_then_facility_then_numbers():
    db = _payment_session()

    with pytest.raises(StopAtFirstRead) as stop:
        await customer_payment_service.create_payment(db, _payment(), current=CASHIER)

    commit, _, _ = assert_order(
        db.log,
        f'^{COMMIT}$',
        r'FROM cash_session WHERE cash_session\.cash_session_id = 77 FOR UPDATE$',
        r'FROM facility WHERE facility\.facility_id = 51 FOR UPDATE$',
    )
    assert any('FROM cash_session' in sql and not is_lock(sql) for sql in db.log[:commit])
    # The first ordinary read after the locks is the folio's MAX, scoped to the facility.
    assert 'max(customer_payment.serial)' in str(stop.value)
    assert 'customer_payment.facility = 51' in str(stop.value)


async def test_create_payment_without_a_session_locks_only_the_facility():
    db = _payment_session(session_id=None)

    with pytest.raises(StopAtFirstRead):
        await customer_payment_service.create_payment(db, _payment(), current=CASHIER)

    assert not any('FROM cash_session' in sql and is_lock(sql) for sql in db.log)
    assert_order(db.log, f'^{COMMIT}$', r'FROM facility WHERE .* FOR UPDATE$')


async def test_create_payment_numbers_the_payment_from_the_facility_max(monkeypatch):
    db = _payment_session()
    db.stop = False
    inner = db.respond
    db.respond = lambda sql: [18713] if 'max(' in sql else inner(sql)
    monkeypatch.setattr(
        customer_payment_service, 'attach_unapplied', lambda _db, payment: _done(payment)
    )

    payment = await customer_payment_service.create_payment(db, _payment(), current=CASHIER)

    assert payment.serial == 18714
    assert payment.cash_session == 77
    assert db.log[-1] == COMMIT


async def _done(value):  # noqa: ANN001, ANN202
    return value


async def test_create_payment_into_a_session_closed_meanwhile_is_refused():
    db = _payment_session(end='2026-10-28 12:00')

    with pytest.raises(HTTPException) as exc:
        await customer_payment_service.create_payment(db, _payment(), current=CASHIER)

    assert exc.value.status_code == 409
    assert exc.value.detail == 'The cash session is closed'
    assert db.added == []
    assert db.log[-1] == ROLLBACK


async def test_create_payment_busy_facility_is_a_409():
    db = _payment_session(busy_on='facility')

    with pytest.raises(HTTPException) as exc:
        await customer_payment_service.create_payment(db, _payment(), current=CASHIER)

    assert exc.value.detail == 'The facility is busy, retry'
    assert db.added == []
    assert db.log[-1] == ROLLBACK
