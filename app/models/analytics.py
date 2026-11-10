"""Analytics tables: background exports (spec 037, FR-054), receivable month-ends, saved views and
scheduled reports (spec 038, data-model.md "New tables")."""

from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    Time,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AnalyticsExport(Base):
    __tablename__ = 'analytics_export'

    analytics_export_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: The owner; only they can read or download it.
    user: Mapped[str] = mapped_column(String(20), ForeignKey('user.user_id'))
    #: 0 query, 1 dashboard, 2 detail, 3 kardex, 4 customer.
    source: Mapped[int] = mapped_column(SmallInteger)
    format: Mapped[int] = mapped_column(SmallInteger)  # 0 xlsx, 1 pdf, 2 csv
    #: The normalized request as JSON, with the scope and visibility it was checked against.
    definition: Mapped[str] = mapped_column(Text)
    status: Mapped[int] = mapped_column(SmallInteger)  # 0 pending, 1 running, 2 ready, 3 failed
    file_name: Mapped[str | None] = mapped_column(String(100))
    row_count: Mapped[int | None] = mapped_column(Integer)
    creation_time: Mapped[datetime] = mapped_column(DateTime)
    completion_time: Mapped[datetime | None] = mapped_column(DateTime)
    expiry_time: Mapped[datetime | None] = mapped_column(DateTime)


class ReceivableSnapshot(Base):
    """One open credit order at one month-end, with spec 033's balance as of 23:59:59 (R8)."""

    __tablename__ = 'receivable_snapshot'

    month_end: Mapped[date] = mapped_column(Date, primary_key=True)
    sales_order: Mapped[int] = mapped_column(
        Integer, ForeignKey('sales_order.sales_order_id'), primary_key=True
    )
    customer: Mapped[int] = mapped_column(Integer, ForeignKey('customer.customer_id'))
    facility: Mapped[int] = mapped_column(Integer)
    salesperson: Mapped[int | None] = mapped_column(Integer)
    #: The customer's agent when the row was written.
    sales_agent: Mapped[int | None] = mapped_column(Integer)
    due_date: Mapped[datetime | None] = mapped_column(DateTime)
    currency: Mapped[int] = mapped_column(Integer)
    exchange_rate: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    total: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    #: In the order's currency.
    balance: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    credit_limit: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    #: True when written at that month-end; False for a backfilled month.
    limit_historical: Mapped[bool] = mapped_column(Boolean)
    written_at: Mapped[datetime] = mapped_column(DateTime)


class ReceivableSnapshotMonth(Base):
    """A written month-end, even one with no open orders, so the job doesn't redo it."""

    __tablename__ = 'receivable_snapshot_month'

    month_end: Mapped[date] = mapped_column(Date, primary_key=True)
    written_at: Mapped[datetime] = mapped_column(DateTime)
    orders: Mapped[int] = mapped_column(Integer)


class AnalyticsView(Base):
    __tablename__ = 'analytics_view'

    analytics_view_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: The owner.
    user: Mapped[str] = mapped_column(String(20), ForeignKey('user.user_id'))
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[int] = mapped_column(SmallInteger)  # 0 query, 1 dashboard, 2 detail, 3 customer
    #: JSON of the request without `start` and `end`.
    definition: Mapped[str] = mapped_column(Text)
    #: JSON `{key, days?, start?, end?}` (R14).
    period: Mapped[str] = mapped_column(Text)
    creation_time: Mapped[datetime] = mapped_column(DateTime)
    modification_time: Mapped[datetime] = mapped_column(DateTime)


class AnalyticsViewShare(Base):
    __tablename__ = 'analytics_view_share'

    analytics_view: Mapped[int] = mapped_column(
        Integer,
        ForeignKey('analytics_view.analytics_view_id', ondelete='CASCADE'),
        primary_key=True,
    )
    user: Mapped[str] = mapped_column(
        String(20), ForeignKey('user.user_id', ondelete='CASCADE'), primary_key=True
    )


class AnalyticsSchedule(Base):
    __tablename__ = 'analytics_schedule'

    analytics_schedule_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: The owner is the view's.
    analytics_view: Mapped[int] = mapped_column(
        Integer, ForeignKey('analytics_view.analytics_view_id', ondelete='CASCADE')
    )
    cadence: Mapped[int] = mapped_column(SmallInteger)  # 0 daily, 1 weekly, 2 monthly
    #: Bitmask, Monday = 1 … Sunday = 64; weekly only.
    weekdays: Mapped[int] = mapped_column(SmallInteger, default=0)
    #: 1–31, monthly only; past the month's end it means the last day.
    month_day: Mapped[int | None] = mapped_column(SmallInteger)
    time_of_day: Mapped[time] = mapped_column(Time)
    format: Mapped[int] = mapped_column(SmallInteger)  # 0 xlsx, 1 pdf, 2 csv
    subject: Mapped[str] = mapped_column(String(150))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    #: The next occurrence, in business time. The claim compares it (R15).
    next_run_time: Mapped[datetime] = mapped_column(DateTime)
    creation_time: Mapped[datetime] = mapped_column(DateTime)
    modification_time: Mapped[datetime] = mapped_column(DateTime)


class AnalyticsScheduleRecipient(Base):
    __tablename__ = 'analytics_schedule_recipient'

    analytics_schedule: Mapped[int] = mapped_column(
        Integer,
        ForeignKey('analytics_schedule.analytics_schedule_id', ondelete='CASCADE'),
        primary_key=True,
    )
    user: Mapped[str] = mapped_column(
        String(20), ForeignKey('user.user_id', ondelete='CASCADE'), primary_key=True
    )


class AnalyticsSend(Base):
    """One attempt to send a schedule's occurrence."""

    __tablename__ = 'analytics_send'

    analytics_send_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    analytics_schedule: Mapped[int] = mapped_column(
        Integer, ForeignKey('analytics_schedule.analytics_schedule_id', ondelete='CASCADE')
    )
    #: The scheduled moment this send is for.
    occurrence: Mapped[datetime] = mapped_column(DateTime)
    started_at: Mapped[datetime] = mapped_column(DateTime)
    #: Null while running; a row left running over 10 minutes reads as failed.
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    outcome: Mapped[int] = mapped_column(SmallInteger)  # 0 running, 1 sent, 2 failed, 3 skipped
    #: The spec 036 code of a failure or skip.
    code: Mapped[str | None] = mapped_column(String(64))
    #: JSON: the user ids sent to, and those dropped and why.
    recipients: Mapped[str] = mapped_column(Text)
    format: Mapped[int] = mapped_column(SmallInteger)  # 0 xlsx, 1 pdf, 2 csv
    size: Mapped[int | None] = mapped_column(Integer)
    row_count: Mapped[int | None] = mapped_column(Integer)
