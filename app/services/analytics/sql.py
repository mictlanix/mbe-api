"""Date grain, weekday and hour expressions for both dialects (spec 037, research R7).

MariaDB runs the real queries; SQLite runs the tests. Each expression compiles to its own SQL per
dialect, so neither dialect sees a function it lacks. Period filters never wrap the column: they
compare it with Python-computed bounds (`period_bounds`), so the date indexes are used.
"""

from datetime import date, datetime, time, timedelta

from sqlalchemy import Date, Integer, String, func, literal
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.expression import ColumnElement, FunctionElement, cast, literal_column

from app.schemas.analytics import Grain


class period_start(FunctionElement):  # noqa: N801  (a SQL function, named like one)
    """The first day of the grain's period holding the column's value, as a date."""

    type = Date()
    inherit_cache = True

    def __init__(self, grain: Grain, column: ColumnElement) -> None:
        self.grain = Grain(grain)
        super().__init__(column)


class weekday(FunctionElement):  # noqa: N801
    """The day of the week, Monday = 1 … Sunday = 7."""

    type = Integer()
    inherit_cache = True


class hour_of(FunctionElement):  # noqa: N801
    """The hour of the day, 0–23."""

    type = Integer()
    inherit_cache = True


def _column(element: FunctionElement) -> ColumnElement:
    return next(iter(element.clauses))


@compiles(period_start)
def _period_start_sqlite(element: period_start, compiler, **kw) -> str:
    column = _column(element)
    weekday_offset = (cast(func.strftime('%w', column), Integer) + 6) % 7
    quarter_offset = (cast(func.strftime('%m', column), Integer) - 1) % 3
    expression = {
        Grain.DAY: func.date(column),
        Grain.WEEK: func.date(column, literal('-') + cast(weekday_offset, String) + ' days'),
        Grain.MONTH: func.date(column, 'start of month'),
        Grain.QUARTER: func.date(
            column, 'start of month', literal('-') + cast(quarter_offset, String) + ' months'
        ),
        Grain.YEAR: func.date(column, 'start of year'),
    }[element.grain]
    return compiler.process(expression, **kw)


@compiles(period_start, 'mysql')
def _period_start_mysql(element: period_start, compiler, **kw) -> str:
    column = _column(element)
    sql = compiler.process(column, **kw)
    day = func.date(column)
    first_of_year = func.makedate(func.year(column), 1)
    expression = {
        Grain.DAY: day,
        Grain.WEEK: func.date_sub(day, literal_column(f'INTERVAL WEEKDAY({sql}) DAY')),
        Grain.MONTH: cast(func.date_format(column, '%Y-%m-01'), Date),
        Grain.QUARTER: func.date_add(
            first_of_year, literal_column(f'INTERVAL (QUARTER({sql}) - 1) QUARTER')
        ),
        Grain.YEAR: first_of_year,
    }[element.grain]
    return compiler.process(expression, **kw)


@compiles(weekday)
def _weekday_sqlite(element: weekday, compiler, **kw) -> str:
    column = _column(element)
    expression = (cast(func.strftime('%w', column), Integer) + 6) % 7 + 1
    return compiler.process(expression, **kw)


@compiles(weekday, 'mysql')
def _weekday_mysql(element: weekday, compiler, **kw) -> str:
    return f'WEEKDAY({compiler.process(_column(element), **kw)}) + 1'


@compiles(hour_of)
def _hour_sqlite(element: hour_of, compiler, **kw) -> str:
    return compiler.process(cast(func.strftime('%H', _column(element)), Integer), **kw)


@compiles(hour_of, 'mysql')
def _hour_mysql(element: hour_of, compiler, **kw) -> str:
    return f'HOUR({compiler.process(_column(element), **kw)})'


def period_bounds(start: date, end: date) -> tuple[datetime, datetime]:
    """`>= start 00:00` and `< end + 1 day`: the inclusive period as an index-friendly range."""
    return datetime.combine(start, time()), datetime.combine(end + timedelta(days=1), time())


class cents(FunctionElement):  # noqa: N801
    """An amount rounded half away from zero to whole cents, as an integer (research R3).

    MariaDB computes on exact DECIMALs, so `ROUND` is the Python `ROUND_HALF_UP` the totals use.
    SQLite computes on binary floats, where 1.005 is stored as 1.00499…; the tests' dialect nudges
    the value by far less than a cent before rounding, so it rounds as the DECIMAL would.
    """

    type = Integer()
    inherit_cache = True


@compiles(cents)
def _cents_sqlite(element: cents, compiler, **kw) -> str:
    amount = compiler.process(_column(element), **kw)
    return (
        f'CAST(ROUND(({amount}) * 100 + CASE WHEN ({amount}) >= 0 THEN 1e-7 ELSE -1e-7 END)'
        ' AS INTEGER)'
    )


@compiles(cents, 'mysql')
def _cents_mysql(element: cents, compiler, **kw) -> str:
    return f'CAST(ROUND({compiler.process(_column(element), **kw)}, 2) * 100 AS SIGNED)'


class days_between(FunctionElement):  # noqa: N801
    """Whole calendar days from the first column's day to the second's (spec 038, R12)."""

    type = Integer()
    inherit_cache = True


@compiles(days_between)
def _days_between_sqlite(element: days_between, compiler, **kw) -> str:
    start, end = (compiler.process(c, **kw) for c in element.clauses)
    return f'CAST(julianday(date({end})) - julianday(date({start})) AS INTEGER)'


@compiles(days_between, 'mysql')
def _days_between_mysql(element: days_between, compiler, **kw) -> str:
    start, end = (compiler.process(c, **kw) for c in element.clauses)
    return f'DATEDIFF({end}, {start})'
