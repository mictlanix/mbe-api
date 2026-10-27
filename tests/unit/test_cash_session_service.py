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
from sqlalchemy import update
from sqlalchemy.dialects import mysql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.deps import CurrentUser
from app.db.base import Base
from app.models.core import CashSession
from app.schemas.cash_session import (
    CashSessionClose,
    CashSessionSort,
    CashSessionStatus,
    SessionState,
)
from app.services.cash_session_service import (
    _drawer,
    _status_clause,
    close_session,
    list_sessions,
    lock_open_session,
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


# ── The session-row lock (spec 032, research R2) ──────────────────────────────


@pytest.fixture
async def db():  # noqa: ANN201
    """An in-memory database with the one table these tests touch.

    SQLite renders no `FOR UPDATE`, so what this proves is the re-read; the lock itself is pinned
    on the compiled MySQL statement.
    """
    engine = create_async_engine('sqlite+aiosqlite://', poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all, tables=[CashSession.__table__])
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


async def _open_session(db: AsyncSession) -> CashSession:
    session = CashSession(
        start=datetime(2026, 7, 25, 9), end=None, cashier=7, cash_drawer=5, cash_supervisor=None
    )
    db.add(session)
    await db.commit()
    return session


async def _close_behind_its_back(db: AsyncSession, session: CashSession) -> None:
    """Set `end` the way a concurrent close would: without touching the loaded object."""
    await db.execute(
        update(CashSession)
        .where(CashSession.cash_session_id == session.cash_session_id)
        .values(end=datetime(2026, 7, 25, 18), cash_supervisor=7)
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    assert session.end is None


def _cashier() -> CurrentUser:
    return CurrentUser(
        user_id='t',
        session_version=1,
        administrator=False,
        facility_id=1,
        employee_id=7,
        cash_drawer_id=5,
    )


def _locked_sql(db: AsyncMock) -> tuple[str, dict]:
    statement = db.execute.await_args_list[0].args[0]
    return str(statement.compile(dialect=mysql.dialect())), statement.get_execution_options()


class TestCloseRereadsUnderTheLock:
    """A close must decide on the row it holds locked, not on the one the endpoint read (R2)."""

    @pytest.mark.asyncio
    async def test_a_row_closed_since_it_was_read_is_refused(self, db: AsyncSession) -> None:
        session = await _open_session(db)
        await _close_behind_its_back(db, session)

        with pytest.raises(HTTPException) as exc:
            await close_session(db, session, CashSessionClose(), current=_cashier())

        assert exc.value.status_code == 409
        assert exc.value.detail == 'Session is already closed'
        assert session.end == datetime(2026, 7, 25, 18)

    @pytest.mark.asyncio
    async def test_the_reread_locks_the_row_and_refreshes_it(self) -> None:
        closed = SimpleNamespace(cash_session_id=3, end=datetime(2026, 7, 25, 18))
        db = AsyncMock()
        db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one=lambda: closed))

        with pytest.raises(HTTPException):
            await close_session(
                db,
                SimpleNamespace(cash_session_id=3, end=None),
                CashSessionClose(),
                current=_cashier(),
            )

        sql, options = _locked_sql(db)
        assert sql.rstrip().upper().endswith('FOR UPDATE')
        assert options.get('populate_existing') is True


class TestLockOpenSession:
    """The shared gate for vouchers and cash supplier payments: lock, re-read, then check `end`."""

    @pytest.mark.asyncio
    async def test_an_open_session_is_returned_locked(self, db: AsyncSession) -> None:
        session = await _open_session(db)

        locked = await lock_open_session(db, session.cash_session_id)

        assert locked is session
        assert locked.end is None

    @pytest.mark.asyncio
    async def test_a_session_closed_since_it_was_read_is_refused(self, db: AsyncSession) -> None:
        session = await _open_session(db)
        await _close_behind_its_back(db, session)

        with pytest.raises(HTTPException) as exc:
            await lock_open_session(db, session.cash_session_id)

        assert exc.value.status_code == 409
        assert exc.value.detail == 'The cash session is closed'

    @pytest.mark.asyncio
    async def test_an_unknown_session_is_404(self, db: AsyncSession) -> None:
        with pytest.raises(HTTPException) as exc:
            await lock_open_session(db, 999)

        assert exc.value.status_code == 404
        assert exc.value.detail == 'Cash session not found'

    @pytest.mark.asyncio
    async def test_the_row_is_locked_and_refreshed(self) -> None:
        db = AsyncMock()
        db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None))

        with pytest.raises(HTTPException):
            await lock_open_session(db, 3)

        sql, options = _locked_sql(db)
        assert sql.rstrip().upper().endswith('FOR UPDATE')
        assert options.get('populate_existing') is True
