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
def _period_start_sqlite(element: period_start, compiler, **kw) -> str:  # noqa: ANN001
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
def _period_start_mysql(element: period_start, compiler, **kw) -> str:  # noqa: ANN001
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
def _weekday_sqlite(element: weekday, compiler, **kw) -> str:  # noqa: ANN001
    column = _column(element)
    expression = (cast(func.strftime('%w', column), Integer) + 6) % 7 + 1
    return compiler.process(expression, **kw)


@compiles(weekday, 'mysql')
def _weekday_mysql(element: weekday, compiler, **kw) -> str:  # noqa: ANN001
    return f'WEEKDAY({compiler.process(_column(element), **kw)}) + 1'


@compiles(hour_of)
def _hour_sqlite(element: hour_of, compiler, **kw) -> str:  # noqa: ANN001
    return compiler.process(cast(func.strftime('%H', _column(element)), Integer), **kw)


@compiles(hour_of, 'mysql')
def _hour_mysql(element: hour_of, compiler, **kw) -> str:  # noqa: ANN001
    return f'HOUR({compiler.process(_column(element), **kw)})'


def period_bounds(start: date, end: date) -> tuple[datetime, datetime]:
    """`>= start 00:00` and `< end + 1 day`: the inclusive period as an index-friendly range."""
    return datetime.combine(start, time()), datetime.combine(end + timedelta(days=1), time())
