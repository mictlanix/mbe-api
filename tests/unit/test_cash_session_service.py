"""Cash session state and the two open-session refusals.

The three-state classification is the whole point of FR-053: "no session" and "a session left open
from yesterday" need different remedies from the client, so they must not collapse into one falsy
answer.
"""

from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.core.deps import CurrentUser
from app.schemas.cash_session import SessionState
from app.services.cash_session_service import _drawer, _employee, session_state

TODAY = date(2026, 7, 25)


def _session(start: datetime) -> SimpleNamespace:
    return SimpleNamespace(start=start, end=None)


class TestSessionState:
    def test_no_session_is_none(self) -> None:
        assert session_state(None, today=TODAY) == SessionState.NONE

    def test_session_started_today_is_open(self) -> None:
        assert session_state(_session(datetime(2026, 7, 25, 9)), today=TODAY) == SessionState.OPEN

    def test_session_started_yesterday_is_stale(self) -> None:
        """FR-053 — a shift left open overnight must be closed before more money is taken."""
        assert (
            session_state(_session(datetime(2026, 7, 24, 22)), today=TODAY) == SessionState.STALE
        )

    def test_stale_is_distinguishable_from_none(self) -> None:
        stale = session_state(_session(datetime(2026, 7, 1)), today=TODAY)

        assert stale != SessionState.NONE
        assert stale == SessionState.STALE

    def test_session_started_at_midnight_today_is_open(self) -> None:
        assert (
            session_state(_session(datetime(2026, 7, 25, 0, 0)), today=TODAY) == SessionState.OPEN
        )

    def test_session_started_a_second_before_midnight_is_stale(self) -> None:
        just_before = datetime(2026, 7, 25) - timedelta(seconds=1)

        assert session_state(_session(just_before), today=TODAY) == SessionState.STALE


class TestContextResolution:
    def test_employee_is_required(self) -> None:
        current = CurrentUser(
            user_id='t', session_version=1, administrator=True, facility_id=1, employee_id=None
        )

        with pytest.raises(HTTPException) as exc:
            _employee(current)

        assert exc.value.status_code == 422
        assert 'employee' in exc.value.detail.lower()

    def test_employee_is_returned_when_present(self) -> None:
        current = CurrentUser(
            user_id='t', session_version=1, administrator=True, facility_id=1, employee_id=7
        )

        assert _employee(current) == 7

    def test_drawer_falls_back_to_the_users_setting(self) -> None:
        current = CurrentUser(
            user_id='t', session_version=1, administrator=True, facility_id=1, cash_drawer_id=5
        )

        assert _drawer(current, None) == 5

    def test_explicit_drawer_wins_over_the_setting(self) -> None:
        current = CurrentUser(
            user_id='t', session_version=1, administrator=True, facility_id=1, cash_drawer_id=5
        )

        assert _drawer(current, 9) == 9

    def test_no_drawer_anywhere_is_refused(self) -> None:
        current = CurrentUser(
            user_id='t', session_version=1, administrator=True, facility_id=1, cash_drawer_id=None
        )

        with pytest.raises(HTTPException) as exc:
            _drawer(current, None)

        assert exc.value.status_code == 422
        assert 'drawer' in exc.value.detail.lower()


class TestLegacyMultipleOpenSessions:
    """Reading a cashier's open session must tolerate legacy multiplicity.

    `cash_session.end IS NULL` is not unique in the production data — two cashiers have three and
    four sessions open at once. `scalar_one_or_none()` raised `MultipleResultsFound` on those,
    turning any payment they recorded into a 500. The query now orders by start and takes one.
    """

    @pytest.mark.asyncio
    async def test_returns_the_most_recent_when_several_are_open(self) -> None:
        from unittest.mock import AsyncMock

        from app.services.cash_session_service import open_session_for_cashier

        newest = SimpleNamespace(cash_session_id=9, start=datetime(2026, 7, 25))
        db = AsyncMock()
        db.execute = AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: newest)
        )

        assert await open_session_for_cashier(db, 17) is newest

    @pytest.mark.asyncio
    async def test_query_orders_and_limits_instead_of_asserting_uniqueness(self) -> None:
        from unittest.mock import AsyncMock

        from app.services.cash_session_service import open_session_for_cashier

        db = AsyncMock()
        db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None))

        await open_session_for_cashier(db, 17)

        sql = str(db.execute.await_args.args[0]).lower()
        assert 'order by' in sql
        assert 'limit' in sql

    @pytest.mark.asyncio
    async def test_drawer_lookup_is_guarded_the_same_way(self) -> None:
        from unittest.mock import AsyncMock

        from app.services.cash_session_service import open_session_for_drawer

        db = AsyncMock()
        db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None))

        await open_session_for_drawer(db, 5)

        sql = str(db.execute.await_args.args[0]).lower()
        assert 'order by' in sql
        assert 'limit' in sql
