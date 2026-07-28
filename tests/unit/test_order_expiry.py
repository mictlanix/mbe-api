"""The sweep that stops abandoned orders holding stock forever (#118).

Confirmation reserves stock and only cancellation, departure or a counter pickup releases it, so
an order confirmed and then abandoned keeps stock unavailable indefinitely — visible on the shelf,
missing from availability. These tests pin the selection rule, the guards, and the reporting of
what the sweep could *not* do, which is the half that still needs a person.
"""

import inspect
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.services.order_expiry import ExpiryReport, expire_unpaid_orders, find_expired

NOW = datetime(2026, 7, 28, 12, 0)
SERVICE = 'app.services.order_expiry'


def _order(order_id: int) -> SimpleNamespace:
    return SimpleNamespace(sales_order_id=order_id)


def _db(rows: list, *, employee_exists: bool = True) -> AsyncMock:
    """A session that answers the employee-existence probe, then the order query."""
    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            SimpleNamespace(scalar_one_or_none=lambda: -1 if employee_exists else None),
            SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows)),
        ]
    )
    return db


def _query_db(rows: list) -> AsyncMock:
    """For calling `find_expired` directly, which does not probe the employee."""
    db = AsyncMock()
    db.execute = AsyncMock(
        return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows))
    )
    return db


class TestSelection:
    @pytest.mark.asyncio
    async def test_filters_on_completed_uncancelled_unpaid_undelivered_and_the_cutoff(self) -> None:
        db = _query_db([])

        await find_expired(db, days=2, now=NOW)

        sql = str(db.execute.await_args.args[0]).lower()
        for column in ('completed', 'cancelled', 'paid', 'delivered', 'date'):
            assert column in sql

    @pytest.mark.asyncio
    async def test_only_orders_actually_holding_stock_are_selected(self) -> None:
        """The condition that keeps the sweep to its purpose.

        Reservations exist only for orders confirmed after this model shipped — none were
        backfilled. Without this the sweep matches every historical order never paid or delivered:
        measured on the live database, 1,363 matched the age and payment rules and **not one held
        a reservation**. That is a mass retirement of historical documents releasing nothing.
        """
        db = _query_db([])

        await find_expired(db, days=2, now=NOW)

        sql = str(db.execute.await_args.args[0]).lower()
        assert 'exists' in sql
        assert 'lot_serial_rqmt' in sql

    @pytest.mark.asyncio
    async def test_the_cutoff_moves_with_the_window(self) -> None:
        db = _query_db([])

        await find_expired(db, days=5, now=NOW)
        five = db.execute.await_args.args[0].compile().params

        assert any(v == NOW - timedelta(days=5) for v in five.values())


class TestExpiry:
    @pytest.mark.asyncio
    async def test_cancels_each_expired_order(self) -> None:
        db = _db([_order(1), _order(2)])

        with patch(
            f'{SERVICE}.sales_order_service.cancel_order', AsyncMock()
        ) as cancel:
            report = await expire_unpaid_orders(db, days=2, employee=7, now=NOW)

        assert report.cancelled == [1, 2]
        assert report.skipped == []
        assert cancel.await_count == 2

    @pytest.mark.asyncio
    async def test_cancellation_goes_through_the_ordinary_path(self) -> None:
        """Not a bespoke delete: an expired order is retired by the same code, and the same
        guards, as one a person cancels — which is what releases the reservation."""
        db = _db([_order(1)])

        with patch(f'{SERVICE}.sales_order_service.cancel_order', AsyncMock()) as cancel:
            await expire_unpaid_orders(db, days=2, employee=7, now=NOW)

        assert cancel.await_args.kwargs['current'].employee_id == 7

    @pytest.mark.asyncio
    async def test_a_partially_paid_order_is_skipped_and_named(self) -> None:
        """It holds live payment applications. Reversing those is somebody's decision."""
        db = _db([_order(1), _order(2)])
        refusal = HTTPException(status_code=409, detail='Order still has payment applications (5)')

        with patch(
            f'{SERVICE}.sales_order_service.cancel_order',
            AsyncMock(side_effect=[refusal, None]),
        ):
            report = await expire_unpaid_orders(db, days=2, employee=7, now=NOW)

        assert report.cancelled == [2]
        assert report.skipped[0][0] == 1
        assert 'payment applications' in report.skipped[0][1]

    @pytest.mark.asyncio
    async def test_a_refusal_does_not_stop_the_rest_of_the_sweep(self) -> None:
        db = _db([_order(1), _order(2), _order(3)])
        refusal = HTTPException(status_code=409, detail='nope')

        with patch(
            f'{SERVICE}.sales_order_service.cancel_order',
            AsyncMock(side_effect=[refusal, None, refusal]),
        ):
            report = await expire_unpaid_orders(db, days=2, employee=7, now=NOW)

        assert report.cancelled == [2]
        assert len(report.skipped) == 2
        assert report.total == 3


class TestGuards:
    @pytest.mark.asyncio
    async def test_zero_days_disables_the_sweep_entirely(self) -> None:
        db = _db([_order(1)])

        report = await expire_unpaid_orders(db, days=0, employee=7, now=NOW)

        assert report == ExpiryReport(cancelled=[], skipped=[])
        db.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_refuses_when_the_system_employee_does_not_exist(self) -> None:
        """`sales_order.updater` is an enforced FK, so a missing employee is error 1452 partway
        through the sweep — after some orders are cancelled and others are not. Checked once up
        front so a run is all-or-nothing."""  # noqa: D401
        db = AsyncMock()
        db.execute = AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: None)
        )

        with pytest.raises(RuntimeError, match='does not exist'):
            await expire_unpaid_orders(db, days=2, employee=999, now=NOW)

    @pytest.mark.asyncio
    async def test_the_check_happens_before_any_cancellation(self) -> None:
        db = AsyncMock()
        db.execute = AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: None)
        )

        with patch(f'{SERVICE}.sales_order_service.cancel_order', AsyncMock()) as cancel:
            with pytest.raises(RuntimeError):
                await expire_unpaid_orders(db, days=2, employee=999, now=NOW)

        cancel.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dry_run_reports_without_cancelling(self) -> None:
        db = _db([_order(1), _order(2)])

        with patch(f'{SERVICE}.sales_order_service.cancel_order', AsyncMock()) as cancel:
            report = await expire_unpaid_orders(db, days=2, employee=7, now=NOW, dry_run=True)

        assert report.cancelled == [1, 2]
        cancel.assert_not_awaited()


class TestTheActorIsAConstant:
    """Not a setting: there is exactly one correct value, and a wrong one is not a preference.

    `sales_order.updater` is an enforced foreign key, so a misconfigured id is a run that fails
    partway through with error 1452 after some orders have already been cancelled. Nothing is
    gained by letting a deployment choose it.
    """

    def test_the_sweep_defaults_to_the_constant(self) -> None:
        from app.core.constants import SYSTEM_EMPLOYEE_ID

        source = inspect.getsource(expire_unpaid_orders)

        assert 'SYSTEM_EMPLOYEE_ID if employee is None' in source
        assert SYSTEM_EMPLOYEE_ID == -1

    def test_it_is_not_a_configurable_setting(self) -> None:
        from app.core.config import Settings

        assert 'system_employee_id' not in Settings.model_fields

    def test_it_is_negative_so_normal_numbering_is_untouched(self) -> None:
        """InnoDB only advances AUTO_INCREMENT for values above it; a high id would push every
        real employee thereafter past it."""
        from app.core.constants import SYSTEM_EMPLOYEE_ID

        assert SYSTEM_EMPLOYEE_ID < 0
