from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class InventoryReceipt(Base):
    __tablename__ = "inventory_receipt"

    inventory_receipt_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    facility: Mapped[int] = mapped_column(Integer, ForeignKey("facility.facility_id"))
    serial: Mapped[int | None] = mapped_column(Integer)
    creation_time: Mapped[datetime] = mapped_column(DateTime)
    modification_time: Mapped[datetime] = mapped_column(DateTime)
    creator: Mapped[int] = mapped_column(Integer, ForeignKey("employee.employee_id"))
    updater: Mapped[int] = mapped_column(Integer, ForeignKey("employee.employee_id"))
    warehouse: Mapped[int] = mapped_column(Integer, ForeignKey("warehouse.warehouse_id"))
    purchase_order: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("purchase_order.purchase_order_id")
    )
    completed: Mapped[bool] = mapped_column(Boolean)
    cancelled: Mapped[bool] = mapped_column(Boolean)
    comment: Mapped[str | None] = mapped_column(String(500))


class InventoryReceiptDetail(Base):
    __tablename__ = "inventory_receipt_detail"

    receipt_detail_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    receipt: Mapped[int] = mapped_column(
        Integer, ForeignKey("inventory_receipt.inventory_receipt_id")
    )
    product: Mapped[int] = mapped_column(Integer, ForeignKey("product.product_id"))
    purchase_order_detail: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("purchase_order_detail.purchase_order_detail_id")
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    product_code: Mapped[str | None] = mapped_column(String(25))
    product_name: Mapped[str | None] = mapped_column(String(250))
    quantity_ordered: Mapped[Decimal] = mapped_column(Numeric(18, 4))


class InventoryIssue(Base):
    __tablename__ = "inventory_issue"

    inventory_issue_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    facility: Mapped[int] = mapped_column(Integer, ForeignKey("facility.facility_id"))
    serial: Mapped[int | None] = mapped_column(Integer)
    creation_time: Mapped[datetime] = mapped_column(DateTime)
    modification_time: Mapped[datetime] = mapped_column(DateTime)
    creator: Mapped[int] = mapped_column(Integer, ForeignKey("employee.employee_id"))
    updater: Mapped[int] = mapped_column(Integer, ForeignKey("employee.employee_id"))
    warehouse: Mapped[int] = mapped_column(Integer, ForeignKey("warehouse.warehouse_id"))
    completed: Mapped[bool] = mapped_column(Boolean)
    cancelled: Mapped[bool] = mapped_column(Boolean)
    comment: Mapped[str | None] = mapped_column(String(500))
    supplier_return: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("supplier_return.supplier_return_id")
    )


class InventoryIssueDetail(Base):
    __tablename__ = "inventory_issue_detail"

    issue_detail_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    issue: Mapped[int] = mapped_column(Integer, ForeignKey("inventory_issue.inventory_issue_id"))
    product: Mapped[int] = mapped_column(Integer, ForeignKey("product.product_id"))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    product_code: Mapped[str | None] = mapped_column(String(25))
    product_name: Mapped[str | None] = mapped_column(String(250))


class InventoryTransfer(Base):
    __tablename__ = "inventory_transfer"

    inventory_transfer_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    facility: Mapped[int] = mapped_column(Integer, ForeignKey("facility.facility_id"))
    serial: Mapped[int | None] = mapped_column(Integer)
    creation_time: Mapped[datetime] = mapped_column(DateTime)
    modification_time: Mapped[datetime] = mapped_column(DateTime)
    creator: Mapped[int] = mapped_column(Integer, ForeignKey("employee.employee_id"))
    updater: Mapped[int] = mapped_column(Integer, ForeignKey("employee.employee_id"))
    warehouse: Mapped[int] = mapped_column(Integer, ForeignKey("warehouse.warehouse_id"))
    warehouse_to: Mapped[int] = mapped_column(Integer, ForeignKey("warehouse.warehouse_id"))
    completed: Mapped[bool] = mapped_column(Boolean)
    cancelled: Mapped[bool] = mapped_column(Boolean)
    comment: Mapped[str | None] = mapped_column(String(500))


class InventoryTransferDetail(Base):
    __tablename__ = "inventory_transfer_detail"

    transfer_detail_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    transfer: Mapped[int] = mapped_column(
        Integer, ForeignKey("inventory_transfer.inventory_transfer_id")
    )
    product: Mapped[int] = mapped_column(Integer, ForeignKey("product.product_id"))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    product_code: Mapped[str | None] = mapped_column(String(25))
    product_name: Mapped[str | None] = mapped_column(String(250))


class LotSerialTracking(Base):
    __tablename__ = "lot_serial_tracking"

    lot_serial_tracking_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[int] = mapped_column(Integer)
    reference: Mapped[int] = mapped_column(Integer)
    date: Mapped[datetime] = mapped_column(DateTime)
    warehouse: Mapped[int] = mapped_column(Integer, ForeignKey("warehouse.warehouse_id"))
    product: Mapped[int] = mapped_column(Integer, ForeignKey("product.product_id"))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    lot_number: Mapped[str | None] = mapped_column(String(50))
    expiration_date: Mapped[date | None] = mapped_column(Date)
    serial_number: Mapped[str | None] = mapped_column(String(50))


class LotSerialRqmt(Base):
    __tablename__ = "lot_serial_rqmt"

    lot_serial_rqmt_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[int] = mapped_column(Integer)
    reference: Mapped[int] = mapped_column(Integer)
    warehouse: Mapped[int] = mapped_column(Integer, ForeignKey("warehouse.warehouse_id"))
    product: Mapped[int] = mapped_column(Integer, ForeignKey("product.product_id"))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4))
