from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Commission(Base):
    __tablename__ = 'commission'

    commission_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    commission_rate: Mapped[Decimal] = mapped_column(Numeric(20, 6))
    comment: Mapped[str | None] = mapped_column(String(50))


class CommissionAgent(Base):
    """Marks an employee as a commission-eligible sales agent (PK = FK → employee)."""

    __tablename__ = 'commission_agent'

    employee: Mapped[int] = mapped_column(
        Integer, ForeignKey('employee.employee_id'), primary_key=True
    )
    # `CommissionScheme` (spec 025). Added by migration 023 with a default of 1, the current
    # system's fully-paid rule, which that system never reads.
    scheme: Mapped[int] = mapped_column(SmallInteger, default=1)


class CommissionParticipation(Base):
    __tablename__ = 'commission_participation'

    commission_participation_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(50))


class CommissionProduct(Base):
    __tablename__ = 'commission_product'

    commission_product_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product: Mapped[int] = mapped_column(Integer, ForeignKey('product.product_id'))
    commission: Mapped[int] = mapped_column(Integer, ForeignKey('commission.commission_id'))


class CommissionSalesperson(Base):
    __tablename__ = 'commission_salesperson'

    commission_salesperson_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    salesperson: Mapped[int] = mapped_column(Integer, ForeignKey('commission_agent.employee'))
    commission: Mapped[int] = mapped_column(Integer, ForeignKey('commission.commission_id'))
    commission_participation: Mapped[int] = mapped_column(
        Integer, ForeignKey('commission_participation.commission_participation_id')
    )
    participation_rate: Mapped[Decimal] = mapped_column(Numeric(20, 6))


class CommissionsHistory(Base):
    __tablename__ = 'commissions_history'

    commissions_history_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sales_order: Mapped[int] = mapped_column(Integer, ForeignKey('sales_order.sales_order_id'))
    sales_order_detail: Mapped[int] = mapped_column(
        Integer, ForeignKey('sales_order_detail.sales_order_detail_id')
    )
    salesperson: Mapped[int | None] = mapped_column(Integer, ForeignKey('employee.employee_id'))
    customer: Mapped[str] = mapped_column(String(250))
    paid: Mapped[int] = mapped_column(Integer)
    date: Mapped[datetime | None] = mapped_column(DateTime)
    product: Mapped[int] = mapped_column(Integer, ForeignKey('product.product_id'))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    price: Mapped[Decimal] = mapped_column(Numeric(22, 2))
    total_detail: Mapped[Decimal] = mapped_column(Numeric(37, 2))
    commission_rate: Mapped[Decimal | None] = mapped_column(Numeric(40, 12))
    commission: Mapped[Decimal | None] = mapped_column(Numeric(50, 2))
    participation_rate: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    confirmed: Mapped[bool] = mapped_column(Boolean)


class CommissionPeriod(Base):
    """A calendar month of commissions: open, closed or paid (spec 025).

    A month with no row is open. Months close in sequence and only the latest closed one reopens
    (FR-010a), so what earlier months stored is always exactly what a later computation subtracts.
    """

    __tablename__ = 'commission_period'
    __table_args__ = (UniqueConstraint('year', 'month', name='commission_period_year_month_uq'),)

    commission_period_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    year: Mapped[int] = mapped_column(SmallInteger)
    month: Mapped[int] = mapped_column(SmallInteger)
    status: Mapped[int] = mapped_column(SmallInteger, default=0)
    closed_by: Mapped[int | None] = mapped_column(Integer, ForeignKey('employee.employee_id'))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)
    reopened_by: Mapped[int | None] = mapped_column(Integer, ForeignKey('employee.employee_id'))
    reopened_at: Mapped[datetime | None] = mapped_column(DateTime)
    paid_by: Mapped[int | None] = mapped_column(Integer, ForeignKey('employee.employee_id'))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime)


class CommissionPeriodLine(Base):
    """A commission line frozen when its month closed (spec 025 FR-007).

    Names and rates are copies, and the order, product and customer ids are not foreign keys: a
    closed month must read the same whatever later happens to them. These rows are also the ledger
    the cap (FR-017) and the key freeze (FR-009a) read.
    """

    __tablename__ = 'commission_period_line'
    __table_args__ = (
        Index('commission_period_line_period_salesperson', 'commission_period', 'salesperson'),
        Index('commission_period_line_key', 'sales_order_detail', 'salesperson', 'participation'),
    )

    commission_period_line_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    commission_period: Mapped[int] = mapped_column(
        Integer, ForeignKey('commission_period.commission_period_id')
    )
    scheme: Mapped[int] = mapped_column(SmallInteger)
    movement: Mapped[int] = mapped_column(SmallInteger)
    sales_order: Mapped[int] = mapped_column(Integer)
    sales_order_detail: Mapped[int] = mapped_column(Integer)
    customer_refund_detail: Mapped[int | None] = mapped_column(Integer)
    order_date: Mapped[datetime] = mapped_column(DateTime)
    paid_date: Mapped[datetime] = mapped_column(DateTime)
    customer: Mapped[int] = mapped_column(Integer)
    customer_name: Mapped[str] = mapped_column(String(250))
    product: Mapped[int] = mapped_column(Integer)
    product_name: Mapped[str] = mapped_column(String(250))
    label: Mapped[int | None] = mapped_column(Integer)
    label_name: Mapped[str | None] = mapped_column(String(50))
    label_rate: Mapped[Decimal] = mapped_column(Numeric(20, 6))
    salesperson: Mapped[int] = mapped_column(Integer, ForeignKey('employee.employee_id'))
    order_salesperson: Mapped[int] = mapped_column(Integer)
    customer_salesperson: Mapped[int] = mapped_column(Integer)
    participation: Mapped[int] = mapped_column(Integer)
    participation_name: Mapped[str] = mapped_column(String(50))
    participation_rate: Mapped[Decimal] = mapped_column(Numeric(20, 6))
    rate: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    price: Mapped[Decimal] = mapped_column(Numeric(22, 2))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    total: Mapped[Decimal] = mapped_column(Numeric(22, 2))
    full_commission: Mapped[Decimal] = mapped_column(Numeric(22, 2))
    share: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    commission: Mapped[Decimal] = mapped_column(Numeric(22, 2))

