from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.enums import AddressType, CurrencyCode, EntityStatus, FacilityType


class Address(Base):
    __tablename__ = "address"

    address_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nickname: Mapped[str | None] = mapped_column(String(100))
    type: Mapped[AddressType] = mapped_column(Integer)
    street: Mapped[str] = mapped_column(String(150))
    exterior_number: Mapped[str] = mapped_column(String(25))
    interior_number: Mapped[str | None] = mapped_column(String(25))
    postal_code: Mapped[str] = mapped_column(String(5))
    neighborhood: Mapped[str] = mapped_column(String(100))
    locality: Mapped[str | None] = mapped_column(String(100))
    borough: Mapped[str] = mapped_column(String(50))
    state: Mapped[str] = mapped_column(String(50))
    city: Mapped[str | None] = mapped_column(String(50))
    country: Mapped[str] = mapped_column(String(50))
    url_address: Mapped[str | None] = mapped_column(String(200))
    comment: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[EntityStatus] = mapped_column(
        Integer, default=EntityStatus.ACTIVE, server_default="0"
    )


class Contact(Base):
    __tablename__ = "contact"

    contact_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(250))
    job_title: Mapped[str | None] = mapped_column(String(100))
    phone: Mapped[str | None] = mapped_column(String(25))
    phone_ext: Mapped[str | None] = mapped_column(String(5))
    mobile: Mapped[str] = mapped_column(String(25))
    fax: Mapped[str | None] = mapped_column(String(25))
    website: Mapped[str | None] = mapped_column(String(80))
    email: Mapped[str | None] = mapped_column(String(80))
    im: Mapped[str | None] = mapped_column(String(80))
    sip: Mapped[str | None] = mapped_column(String(80))
    birthday: Mapped[date | None] = mapped_column(Date)
    comment: Mapped[str | None] = mapped_column(String(500))


class Employee(Base):
    __tablename__ = "employee"

    employee_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100))
    nickname: Mapped[str] = mapped_column(String(50))
    gender: Mapped[int] = mapped_column(SmallInteger)
    birthday: Mapped[date] = mapped_column(Date)
    taxpayer_id: Mapped[str | None] = mapped_column(String(13))
    sales_person: Mapped[bool] = mapped_column(Boolean)
    status: Mapped[EntityStatus] = mapped_column(
        Integer, default=EntityStatus.ACTIVE, server_default="0"
    )
    personal_id: Mapped[str | None] = mapped_column(String(18))
    start_job_date: Mapped[date] = mapped_column(Date)
    comment: Mapped[str | None] = mapped_column(String(500))
    enroll_number: Mapped[int | None] = mapped_column(Integer)


class Facility(Base):
    __tablename__ = "facility"

    facility_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(25))
    name: Mapped[str] = mapped_column(String(250))
    type: Mapped[FacilityType] = mapped_column(Integer)
    location: Mapped[str] = mapped_column(
        String(5), ForeignKey("sat_postal_code.sat_postal_code_id")
    )
    address: Mapped[int] = mapped_column(Integer, ForeignKey("address.address_id"))
    taxpayer: Mapped[str] = mapped_column(
        String(13), ForeignKey("taxpayer_issuer.taxpayer_issuer_id")
    )
    logo: Mapped[str | None] = mapped_column(String(255))
    receipt_message: Mapped[str | None] = mapped_column(String(250))
    default_batch: Mapped[str | None] = mapped_column(String(10))
    status: Mapped[EntityStatus] = mapped_column(
        Integer, default=EntityStatus.ACTIVE, server_default="0"
    )


class Warehouse(Base):
    __tablename__ = "warehouse"

    warehouse_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    facility: Mapped[int] = mapped_column(Integer, ForeignKey("facility.facility_id"))
    code: Mapped[str] = mapped_column(String(25))
    name: Mapped[str] = mapped_column(String(250))
    comment: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[EntityStatus] = mapped_column(
        Integer, default=EntityStatus.ACTIVE, server_default="0"
    )


class PointSale(Base):
    __tablename__ = "point_sale"

    point_sale_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    facility: Mapped[int] = mapped_column(Integer, ForeignKey("facility.facility_id"))
    code: Mapped[str] = mapped_column(String(25))
    name: Mapped[str] = mapped_column(String(250))
    warehouse: Mapped[int] = mapped_column(Integer, ForeignKey("warehouse.warehouse_id"))
    comment: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[EntityStatus] = mapped_column(
        Integer, default=EntityStatus.ACTIVE, server_default="0"
    )


class CashDrawer(Base):
    __tablename__ = "cash_drawer"

    cash_drawer_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    facility: Mapped[int] = mapped_column(Integer, ForeignKey("facility.facility_id"))
    code: Mapped[str] = mapped_column(String(25))
    name: Mapped[str] = mapped_column(String(250))
    comment: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[EntityStatus] = mapped_column(
        Integer, default=EntityStatus.ACTIVE, server_default="0"
    )


class CashSession(Base):
    __tablename__ = "cash_session"

    cash_session_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    start: Mapped[datetime] = mapped_column(DateTime)
    end: Mapped[datetime | None] = mapped_column(DateTime)
    cashier: Mapped[int] = mapped_column(Integer, ForeignKey("employee.employee_id"))
    cash_drawer: Mapped[int] = mapped_column(Integer, ForeignKey("cash_drawer.cash_drawer_id"))
    cash_supervisor: Mapped[int | None] = mapped_column(Integer, ForeignKey("employee.employee_id"))


class CashCount(Base):
    __tablename__ = "cash_count"

    cash_count_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session: Mapped[int] = mapped_column(Integer, ForeignKey("cash_session.cash_session_id"))
    denomination: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    quantity: Mapped[int] = mapped_column(Integer)
    type: Mapped[int] = mapped_column(Integer)


class ExchangeRate(Base):
    __tablename__ = "exchange_rate"

    exchange_rate_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[date] = mapped_column(Date)
    rate: Mapped[Decimal] = mapped_column(Numeric(8, 4))
    base: Mapped[CurrencyCode] = mapped_column(Integer)
    target: Mapped[CurrencyCode] = mapped_column(Integer)


class Expense(Base):
    """Expense category catalog (table name: expenses)."""

    __tablename__ = "expenses"

    expense_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    expense: Mapped[str] = mapped_column(String(100))
    comment: Mapped[str | None] = mapped_column(String(500))


class PaymentMethodOption(Base):
    __tablename__ = "payment_method_option"

    payment_method_option_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    warehouse: Mapped[int | None] = mapped_column(Integer, ForeignKey("warehouse.warehouse_id"))
    facility: Mapped[int] = mapped_column(Integer, ForeignKey("facility.facility_id"))
    name: Mapped[str] = mapped_column(String(50))
    number_of_payments: Mapped[int] = mapped_column(SmallInteger)
    display_on_ticket: Mapped[bool] = mapped_column(Boolean)
    payment_method: Mapped[int] = mapped_column(Integer)
    commission: Mapped[Decimal] = mapped_column(Numeric(10, 3))
    status: Mapped[EntityStatus] = mapped_column(
        Integer, default=EntityStatus.ACTIVE, server_default="0"
    )


class BankAccount(Base):
    __tablename__ = "bank_account"

    bank_account_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bank_name: Mapped[str] = mapped_column(String(250))
    account_number: Mapped[str] = mapped_column(String(20))
    reference: Mapped[str | None] = mapped_column(String(20))
    routing_number: Mapped[str | None] = mapped_column(String(18))
    comment: Mapped[str | None] = mapped_column(String(500))


class Label(Base):
    __tablename__ = "label"

    label_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(250))
    comment: Mapped[str | None] = mapped_column(String(500))


class PostalCode(Base):
    __tablename__ = "postal_code"

    postal_code_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[int] = mapped_column(Integer)
    neighborhood: Mapped[str] = mapped_column(String(150))
    borough: Mapped[str] = mapped_column(String(50))
    state: Mapped[str] = mapped_column(String(50))
    city: Mapped[str | None] = mapped_column(String(50))
    country: Mapped[str] = mapped_column(String(50))


class Vehicle(Base):
    __tablename__ = "vehicle"

    vehicle_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    license_plate: Mapped[str] = mapped_column(String(8))
    name: Mapped[str] = mapped_column(String(50))
    nickname: Mapped[str] = mapped_column(String(30))
    tons_capacity: Mapped[int] = mapped_column(SmallInteger)
    status: Mapped[EntityStatus] = mapped_column(
        Integer, default=EntityStatus.ACTIVE, server_default="0"
    )


class VehicleOperator(Base):
    __tablename__ = "vehicle_operator"

    vehicle_operator_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    driver: Mapped[int] = mapped_column(Integer, ForeignKey("employee.employee_id"))
    license_type: Mapped[str] = mapped_column(String(3))
    driver_license_number: Mapped[str] = mapped_column(String(15))
    issue_date: Mapped[date] = mapped_column(Date)
    expiration_date: Mapped[date] = mapped_column(Date)
    issuing_location: Mapped[str] = mapped_column(String(30))
    creation_time: Mapped[datetime] = mapped_column(DateTime)
    modification_time: Mapped[datetime] = mapped_column(DateTime)
    creator: Mapped[int] = mapped_column(Integer, ForeignKey("employee.employee_id"))
    updater: Mapped[int] = mapped_column(Integer, ForeignKey("employee.employee_id"))
    status: Mapped[EntityStatus] = mapped_column(
        Integer, default=EntityStatus.ACTIVE, server_default="0"
    )
