"""The payments editor — correcting a misapplied payment without destroying history (FR-073).

This story adds almost no new machinery, and that is the design: correcting a mistake is reversing
one application (US2) and applying the freed amount elsewhere. What it does need is a listing that
includes **cancelled** applications, because a supervisor investigating a discrepancy has to see
what was undone, not only what currently stands.
"""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.deps import CurrentUser
from app.services.customer_payment_service import list_applications, list_payments


def _current() -> CurrentUser:
    return CurrentUser(
        user_id='super', session_version=1, administrator=True, facility_id=1, employee_id=7
    )


def _application(app_id: int, *, cancelled: bool) -> SimpleNamespace:
    return SimpleNamespace(
        sales_order_payment_id=app_id,
        sales_order=5,
        customer_payment=1,
        amount=Decimal('100.00'),
        amount_change=Decimal('0'),
        applier=7,
        cancelled=cancelled,
    )


def _db(rows: list) -> AsyncMock:
    db = AsyncMock()
    db.execute = AsyncMock(
        return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows))
    )
    return db


def _list_db(rows: list) -> AsyncMock:
    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            SimpleNamespace(scalar_one=lambda: len(rows)),
            SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows)),
        ]
    )
    return db


class TestApplicationsListing:
    @pytest.mark.asyncio
    async def test_includes_cancelled_applications(self) -> None:
        """The whole history, not the live subset — a reversal must remain visible."""
        rows = [_application(1, cancelled=False), _application(2, cancelled=True)]
        db = _db(rows)

        result = await list_applications(db, 1)

        assert [a.cancelled for a in result] == [False, True]

    @pytest.mark.asyncio
    async def test_does_not_filter_on_cancelled(self) -> None:
        db = _db([])

        await list_applications(db, 1)

        sql = str(db.execute.await_args.args[0]).lower()
        assert 'cancelled' not in sql.split('where')[-1] if 'where' in sql else True

    @pytest.mark.asyncio
    async def test_each_application_names_its_order_amount_and_applier(self) -> None:
        db = _db([_application(1, cancelled=False)])

        result = await list_applications(db, 1)

        assert result[0].sales_order == 5
        assert result[0].amount == Decimal('100.00')
        assert result[0].applier == 7


class TestCrossFacilitySearch:
    @pytest.mark.asyncio
    async def test_cross_facility_search_drops_the_facility_filter(self) -> None:
        """Gated by PaymentsEditor (100) at the route; the query itself widens."""
        db = _list_db([])

        await list_payments(db, current=_current(), cross_facility=True)

        sql = str(db.execute.await_args_list[0].args[0]).lower()
        assert 'facility' not in sql

    @pytest.mark.asyncio
    async def test_normal_search_keeps_the_facility_filter(self) -> None:
        db = _list_db([])

        await list_payments(db, current=_current(), cross_facility=False)

        assert 'facility' in str(db.execute.await_args_list[0].args[0]).lower()

    @pytest.mark.asyncio
    async def test_cross_facility_search_can_still_target_one_facility(self) -> None:
        db = _list_db([])

        await list_payments(db, current=_current(), cross_facility=True, facility=9)

        assert 'facility' in str(db.execute.await_args_list[0].args[0]).lower()

    @pytest.mark.asyncio
    async def test_searches_by_reference(self) -> None:
        db = _list_db([])

        await list_payments(db, current=_current(), reference='SPEI-123')

        assert 'reference' in str(db.execute.await_args_list[0].args[0]).lower()
