"""Spec 037, T004: the date grain, weekday and hour expressions, on both dialects (R7).

Each expression is evaluated on an in-memory SQLite engine for fixed datetimes, and compiled for
MariaDB, whose functions the SQLite tests can't run.
"""

from datetime import date, datetime

import pytest
from sqlalchemy import Column, DateTime, MetaData, Table, insert, select
from sqlalchemy.dialects import mysql
from sqlalchemy.ext.asyncio import create_async_engine

from app.schemas.analytics import Grain
from app.services.analytics.sql import hour_of, period_bounds, period_start, weekday

_meta = MetaData()
_moments = Table('moments', _meta, Column('at', DateTime))

SUNDAY = datetime(2026, 3, 15, 23, 59, 59)  # a Sunday, a minute before midnight
QUARTER_START = datetime(2026, 4, 1, 0, 0, 0)  # a Wednesday, the first day of Q2
MIDWEEK = datetime(2026, 8, 19, 9, 5, 0)  # a Wednesday in Q3


async def _evaluate(expression, moment: datetime):  # noqa: ANN001, ANN202
    engine = create_async_engine('sqlite+aiosqlite://')
    async with engine.begin() as connection:
        await connection.run_sync(_meta.create_all)
        await connection.execute(insert(_moments).values(at=moment))
        value = (await connection.execute(select(expression))).scalar_one()
    await engine.dispose()
    return value


@pytest.mark.parametrize(
    ('grain', 'moment', 'expected'),
    [
        (Grain.DAY, SUNDAY, date(2026, 3, 15)),
        (Grain.WEEK, SUNDAY, date(2026, 3, 9)),
        (Grain.WEEK, QUARTER_START, date(2026, 3, 30)),
        (Grain.MONTH, SUNDAY, date(2026, 3, 1)),
        (Grain.QUARTER, SUNDAY, date(2026, 1, 1)),
        (Grain.QUARTER, QUARTER_START, date(2026, 4, 1)),
        (Grain.QUARTER, MIDWEEK, date(2026, 7, 1)),
        (Grain.YEAR, MIDWEEK, date(2026, 1, 1)),
    ],
)
async def test_period_start_on_sqlite(grain: Grain, moment: datetime, expected: date) -> None:
    assert await _evaluate(period_start(grain, _moments.c.at), moment) == expected


@pytest.mark.parametrize(
    ('moment', 'expected'), [(SUNDAY, 7), (QUARTER_START, 3), (datetime(2026, 3, 9, 8), 1)]
)
async def test_weekday_is_monday_one(moment: datetime, expected: int) -> None:
    assert await _evaluate(weekday(_moments.c.at), moment) == expected


@pytest.mark.parametrize(('moment', 'expected'), [(SUNDAY, 23), (QUARTER_START, 0), (MIDWEEK, 9)])
async def test_hour_of(moment: datetime, expected: int) -> None:
    assert await _evaluate(hour_of(_moments.c.at), moment) == expected


def _mysql(expression) -> str:  # noqa: ANN001
    return str(expression.compile(dialect=mysql.dialect())).upper()


@pytest.mark.parametrize(
    ('grain', 'functions'),
    [
        (Grain.DAY, ['DATE(']),
        (Grain.WEEK, ['WEEKDAY(', 'DATE_SUB(']),
        (Grain.MONTH, ['DATE_FORMAT(']),
        (Grain.QUARTER, ['QUARTER(', 'MAKEDATE(']),
        (Grain.YEAR, ['MAKEDATE(', 'YEAR(']),
    ],
)
def test_period_start_compiles_for_mariadb(grain: Grain, functions: list[str]) -> None:
    sql = _mysql(period_start(grain, _moments.c.at))

    for function in functions:
        assert function in sql
    assert 'STRFTIME' not in sql


def test_weekday_and_hour_compile_for_mariadb() -> None:
    assert 'WEEKDAY(MOMENTS.AT) + 1' in _mysql(weekday(_moments.c.at))
    assert 'HOUR(MOMENTS.AT)' in _mysql(hour_of(_moments.c.at))


def test_period_bounds_cover_whole_days() -> None:
    assert period_bounds(date(2026, 3, 1), date(2026, 3, 31)) == (
        datetime(2026, 3, 1),
        datetime(2026, 4, 1),
    )
