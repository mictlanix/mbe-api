from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Commission(Base):
    __tablename__ = "commission"

    commission_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    commission_rate: Mapped[Decimal] = mapped_column(Numeric(20, 6))
    comment: Mapped[str | None] = mapped_column(String(50))


class CommissionAgent(Base):
    """Marks an employee as a commission-eligible sales agent (PK = FK → employee)."""

    __tablename__ = "commission_agent"

    employee: Mapped[int] = mapped_column(Integer, ForeignKey("employee.employee_id"), primary_key=True)


class CommissionParticipation(Base):
    __tablename__ = "commission_participation"

    commission_participation_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(50))


class CommissionProduct(Base):
    __tablename__ = "commission_product"

    commission_product_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product: Mapped[int] = mapped_column(Integer, ForeignKey("product.product_id"))
    commission: Mapped[int] = mapped_column(Integer, ForeignKey("commission.commission_id"))


class CommissionSalesperson(Base):
    __tablename__ = "commission_salesperson"

    commission_salesperson_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    salesperson: Mapped[int] = mapped_column(Integer, ForeignKey("commission_agent.employee"))
    commission: Mapped[int] = mapped_column(Integer, ForeignKey("commission.commission_id"))
    commission_participation: Mapped[int] = mapped_column(
        Integer, ForeignKey("commission_participation.commission_participation_id")
    )
    participation_rate: Mapped[Decimal] = mapped_column(Numeric(20, 6))


class CommissionsHistory(Base):
    __tablename__ = "commissions_history"

    commissions_history_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sales_order: Mapped[int] = mapped_column(Integer, ForeignKey("sales_order.sales_order_id"))
    sales_order_detail: Mapped[int] = mapped_column(
        Integer, ForeignKey("sales_order_detail.sales_order_detail_id")
    )
    salesperson: Mapped[int | None] = mapped_column(Integer, ForeignKey("employee.employee_id"))
    customer: Mapped[str] = mapped_column(String(250))
    paid: Mapped[int] = mapped_column(Integer)
    date: Mapped[datetime | None] = mapped_column(DateTime)
    product: Mapped[int] = mapped_column(Integer, ForeignKey("product.product_id"))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    price: Mapped[Decimal] = mapped_column(Numeric(22, 2))
    total_detail: Mapped[Decimal] = mapped_column(Numeric(37, 2))
    commission_rate: Mapped[Decimal | None] = mapped_column(Numeric(40, 12))
    commission: Mapped[Decimal | None] = mapped_column(Numeric(50, 2))
    participation_rate: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    confirmed: Mapped[bool] = mapped_column(Boolean)
