from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DeliveryOrder(Base):
    __tablename__ = "delivery_order"

    delivery_order_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    creator: Mapped[int] = mapped_column(Integer, ForeignKey("employee.employee_id"))
    updater: Mapped[int] = mapped_column(Integer, ForeignKey("employee.employee_id"))
    creation_time: Mapped[datetime] = mapped_column(DateTime)
    modification_time: Mapped[datetime] = mapped_column(DateTime)
    facility: Mapped[int] = mapped_column(Integer, ForeignKey("facility.facility_id"))
    serial: Mapped[int] = mapped_column(Integer)
    customer: Mapped[int] = mapped_column(Integer, ForeignKey("customer.customer_id"))
    ship_to: Mapped[int | None] = mapped_column(Integer, ForeignKey("address.address_id"))
    contact: Mapped[int | None] = mapped_column(Integer, ForeignKey("contact.contact_id"))
    date: Mapped[datetime | None] = mapped_column(DateTime)
    priority: Mapped[int] = mapped_column(SmallInteger)
    completed: Mapped[bool] = mapped_column(Boolean)
    cancelled: Mapped[bool] = mapped_column(Boolean)
    comment: Mapped[str | None] = mapped_column(String(500))
    delivered: Mapped[bool | None] = mapped_column(Boolean)
    confirmed: Mapped[bool | None] = mapped_column(Boolean)
    picked_up: Mapped[bool] = mapped_column(Boolean)


class DeliveryOrderDetail(Base):
    __tablename__ = "delivery_order_detail"

    delivery_order_detail_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    delivery_order: Mapped[int] = mapped_column(
        Integer, ForeignKey("delivery_order.delivery_order_id")
    )
    sales_order_detail: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("sales_order_detail.sales_order_detail_id")
    )
    product: Mapped[int] = mapped_column(Integer, ForeignKey("product.product_id"))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    product_code: Mapped[str] = mapped_column(String(425))
    product_name: Mapped[str] = mapped_column(String(250))


class DeliveriesItinerary(Base):
    __tablename__ = "deliveries_itinerary"

    deliveries_itinerary_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vehicle: Mapped[int | None] = mapped_column(Integer, ForeignKey("vehicle.vehicle_id"))
    vehicle_operator: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("vehicle_operator.vehicle_operator_id")
    )
    date: Mapped[date] = mapped_column(Date)
    creator: Mapped[int] = mapped_column(Integer, ForeignKey("employee.employee_id"))
    updater: Mapped[int] = mapped_column(Integer, ForeignKey("employee.employee_id"))
    creation_time: Mapped[datetime] = mapped_column(DateTime)
    modification_time: Mapped[datetime] = mapped_column(DateTime)
    cancelled: Mapped[bool] = mapped_column(Boolean)
    completed: Mapped[bool] = mapped_column(Boolean)
    comment: Mapped[str | None] = mapped_column(String(500))
    warehouse: Mapped[int | None] = mapped_column(Integer, ForeignKey("warehouse.warehouse_id"))


class DeliveriesItineraryDetail(Base):
    __tablename__ = "deliveries_itinerary_detail"

    deliveries_itinerary_detail_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    deliveries_itinerary: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("deliveries_itinerary.deliveries_itinerary_id")
    )
    delivery_order_detail: Mapped[int] = mapped_column(
        Integer, ForeignKey("delivery_order_detail.delivery_order_detail_id")
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 6))
    comment: Mapped[str | None] = mapped_column(String(500))
