"""Cash session state and the two open-session refusals.

The three-state classification is the whole point of FR-053: "no session" and "a session left open
from yesterday" need different remedies from the client, so they must not collapse into one falsy
answer.
"""

from datetime import date, datetime, time, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.core.deps import CurrentUser
from app.schemas.cash_session import CashSessionSort, CashSessionStatus, SessionState
from app.services.cash_session_service import (
    _drawer,
    _status_clause,
    list_sessions,
    session_state,
)

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
    def test_drawer_falls_back_to_the_users_setting(self) -> None:
        current = CurrentUser(
            user_id='t',
            session_version=1,
            administrator=True,
            facility_id=1,
            employee_id=7,
            cash_drawer_id=5,
        )

        assert _drawer(current, None) == 5

    def test_explicit_drawer_wins_over_the_setting(self) -> None:
        current = CurrentUser(
            user_id='t',
            session_version=1,
            administrator=True,
            facility_id=1,
            employee_id=7,
            cash_drawer_id=5,
        )

        assert _drawer(current, 9) == 9

    def test_no_drawer_anywhere_is_refused(self) -> None:
        current = CurrentUser(
            user_id='t',
            session_version=1,
            administrator=True,
            facility_id=1,
            employee_id=7,
            cash_drawer_id=None,
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


class TestStatusFacet:
    """The list facet must classify a row exactly as `session_state` classifies one (#142).

    The two are written against different things — one against a loaded object, one against the
    columns — so a paged "show me open sessions" would otherwise disagree with the badge the
    current-session endpoint puts on the very same row.
    """

    @pytest.mark.parametrize(
        ('wanted', 'expected'),
        [
            (CashSessionStatus.CLOSED, 'is not null'),
            (CashSessionStatus.STALE, 'start < '),
            (CashSessionStatus.OPEN, 'start >= '),
        ],
    )
    def test_each_status_narrows_on_the_expected_columns(
        self, wanted: CashSessionStatus, expected: str
    ) -> None:
        sql = str(_status_clause(wanted, today=TODAY)).lower()

        assert expected in sql

    @pytest.mark.parametrize('wanted', [CashSessionStatus.OPEN, CashSessionStatus.STALE])
    def test_open_and_stale_both_require_an_unset_end(self, wanted: CashSessionStatus) -> None:
        # `end` is a reserved word, so SQLAlchemy quotes it.
        assert '"end" is null' in str(_status_clause(wanted, today=TODAY)).lower()

    def test_closed_does_not_look_at_start_at_all(self) -> None:
        """A session closed yesterday is closed, not stale."""
        assert 'start' not in str(_status_clause(CashSessionStatus.CLOSED, today=TODAY)).lower()

    def test_the_stale_boundary_is_midnight_today(self) -> None:
        """The boundary `session_state` applies — 00:00 today is open, a second earlier is stale."""
        stale = _status_clause(CashSessionStatus.STALE, today=TODAY)

        assert str(datetime.combine(TODAY, time.min)) in str(
            stale.compile(compile_kwargs={'literal_binds': True})
        )


class TestListFacets:
    @staticmethod
    def _db() -> AsyncMock:
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                SimpleNamespace(scalar_one=lambda: 0),
                SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [])),
            ]
        )
        return db

    @staticmethod
    def _current() -> CurrentUser:
        return CurrentUser(
            user_id='t',
            session_version=1,
            administrator=True,
            facility_id=1,
            employee_id=7,
            cash_drawer_id=5,
        )

    async def _page_sql(self, **kwargs) -> str:
        db = self._db()
        await list_sessions(db, current=self._current(), **kwargs)
        return str(db.execute.await_args_list[1].args[0]).lower()

    @pytest.mark.asyncio
    async def test_no_facets_scans_unfiltered(self) -> None:
        assert 'where' not in await self._page_sql()

    @pytest.mark.asyncio
    async def test_facility_is_not_defaulted_to_the_callers_own(self) -> None:
        """#142 — reconciling a day is a cross-facility job, so nothing is scoped implicitly."""
        assert 'facility' not in await self._page_sql()

    @pytest.mark.asyncio
    async def test_facility_narrows_through_the_drawer(self) -> None:
        sql = await self._page_sql(facility=3)

        assert 'facility' in sql
        assert 'cash_drawer' in sql

    @pytest.mark.asyncio
    async def test_cashier_narrows_on_the_cashier_column(self) -> None:
        assert 'cashier' in await self._page_sql(cashier=17)

    @pytest.mark.asyncio
    async def test_a_date_range_narrows_on_start(self) -> None:
        sql = await self._page_sql(
            date_from=datetime(2026, 7, 1), date_to=datetime(2026, 7, 31, 23, 59)
        )

        assert sql.count('start') >= 2

    @pytest.mark.asyncio
    async def test_status_narrows_on_end(self) -> None:
        assert 'end' in await self._page_sql(session_status=CashSessionStatus.CLOSED)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ('sort', 'expected'),
        [
            (CashSessionSort.ID_DESC, 'cash_session_id desc'),
            (CashSessionSort.START_ASC, 'start asc'),
            (CashSessionSort.START_DESC, 'start desc'),
        ],
    )
    async def test_sort_drives_the_ordering(
        self, sort: CashSessionSort, expected: str
    ) -> None:
        assert expected in await self._page_sql(sort=sort)

    @pytest.mark.asyncio
    async def test_the_default_ordering_is_unchanged(self) -> None:
        """Newest id first, as the list has always returned."""
        assert 'cash_session_id desc' in await self._page_sql()
