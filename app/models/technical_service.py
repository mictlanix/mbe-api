from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TechServiceReceipt(Base):
    __tablename__ = "tech_service_receipt"

    tech_service_receipt_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    brand: Mapped[str] = mapped_column(String(64))
    equipment: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(64))
    serial_number: Mapped[str | None] = mapped_column(String(64))
    date: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str | None] = mapped_column(String(64))
    location: Mapped[str | None] = mapped_column(String(128))
    checker: Mapped[str] = mapped_column(String(128))
    comment: Mapped[str | None] = mapped_column(String(1024))


class TechServiceReceiptComponent(Base):
    __tablename__ = "tech_service_receipt_component"

    tech_service_receipt_component_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    receipt: Mapped[int] = mapped_column(
        Integer, ForeignKey("tech_service_receipt.tech_service_receipt_id")
    )
    name: Mapped[str] = mapped_column(String(128))
    quantity: Mapped[int] = mapped_column(Integer)
    serial_number: Mapped[str | None] = mapped_column(String(64))
    comment: Mapped[str | None] = mapped_column(String(256))


class TechServiceReport(Base):
    __tablename__ = "tech_service_report"

    tech_service_report_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[datetime] = mapped_column(DateTime)
    location: Mapped[str] = mapped_column(String(128))
    type: Mapped[str] = mapped_column(String(128))
    equipment: Mapped[str] = mapped_column(String(64))
    brand: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(64))
    serial_number: Mapped[str | None] = mapped_column(String(64))
    user: Mapped[str | None] = mapped_column(String(128))
    technician: Mapped[str | None] = mapped_column(String(128))
    cost: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    user_report: Mapped[str | None] = mapped_column(String(1024))
    description: Mapped[str | None] = mapped_column(String(1024))
    comment: Mapped[str | None] = mapped_column(String(1024))


class TechServiceRequest(Base):
    __tablename__ = "tech_service_request"

    tech_service_request_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[int] = mapped_column(Integer)
    brand: Mapped[str] = mapped_column(String(64))
    equipment: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(64))
    serial_number: Mapped[str | None] = mapped_column(String(64))
    date: Mapped[datetime] = mapped_column(DateTime)
    end_date: Mapped[datetime | None] = mapped_column(DateTime)
    customer: Mapped[int] = mapped_column(Integer, ForeignKey("customer.customer_id"))
    responsible: Mapped[str] = mapped_column(String(128))
    location: Mapped[str] = mapped_column(String(128))
    payment_status: Mapped[str | None] = mapped_column(String(64))
    shipping_method: Mapped[str | None] = mapped_column(String(64))
    contact_name: Mapped[str | None] = mapped_column(String(128))
    contact_phone_number: Mapped[str | None] = mapped_column(String(64))
    address: Mapped[str | None] = mapped_column(String(256))
    remarks: Mapped[str | None] = mapped_column(String(1024))
    comment: Mapped[str | None] = mapped_column(String(1024))


class TechServiceRequestComponent(Base):
    """Mirrors TechServiceReceiptComponent structure, linked to TechServiceRequest."""

    __tablename__ = "tech_service_request_component"

    tech_service_request_component_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request: Mapped[int] = mapped_column(
        Integer, ForeignKey("tech_service_request.tech_service_request_id")
    )
    name: Mapped[str] = mapped_column(String(128))
    quantity: Mapped[int] = mapped_column(Integer)
    serial_number: Mapped[str | None] = mapped_column(String(64))
    comment: Mapped[str | None] = mapped_column(String(256))


class VehicleServiceOrder(Base):
    __tablename__ = "vehicle_service_order"

    service_order_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vehicle: Mapped[int] = mapped_column(Integer, ForeignKey("vehicle.vehicle_id"))
    problem_description: Mapped[str] = mapped_column(String(500))
    service_description: Mapped[str | None] = mapped_column(String(500))
    creator: Mapped[int] = mapped_column(Integer, ForeignKey("employee.employee_id"))
    updater: Mapped[int] = mapped_column(Integer, ForeignKey("employee.employee_id"))
    notifier: Mapped[int] = mapped_column(Integer, ForeignKey("employee.employee_id"))
    creation_time: Mapped[datetime] = mapped_column(DateTime)
    modification_time: Mapped[datetime] = mapped_column(DateTime)
    completed: Mapped[bool] = mapped_column(Boolean)
    cancelled: Mapped[bool] = mapped_column(Boolean)
    comment: Mapped[str | None] = mapped_column(String(250))
    date: Mapped[datetime | None] = mapped_column(DateTime)


class ServiceOrderDetail(Base):
    __tablename__ = "service_order_detail"

    service_order_detail_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vehicle_service_order: Mapped[int] = mapped_column(
        Integer, ForeignKey("vehicle_service_order.service_order_id")
    )
    spare_part: Mapped[int] = mapped_column(Integer, ForeignKey("product.product_id"))
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 6))
    comment: Mapped[str | None] = mapped_column(String(500))
    date: Mapped[datetime] = mapped_column(DateTime)
