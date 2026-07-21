from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TranslationRequest(Base):
    __tablename__ = 'translation_request'

    translation_request_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    requester: Mapped[int] = mapped_column(Integer, ForeignKey('employee.employee_id'))
    date: Mapped[datetime] = mapped_column(DateTime)
    agency: Mapped[str] = mapped_column(String(256))
    document_name: Mapped[str] = mapped_column(String(128))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    delivery_date: Mapped[datetime] = mapped_column(DateTime)
    comment: Mapped[str | None] = mapped_column(String(1024))


class Notarization(Base):
    __tablename__ = 'notarization'

    notarization_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    requester: Mapped[int] = mapped_column(Integer, ForeignKey('employee.employee_id'))
    notary_office: Mapped[str] = mapped_column(String(256))
    date: Mapped[datetime] = mapped_column(DateTime)
    document_description: Mapped[str] = mapped_column(String(512))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    payment_date: Mapped[datetime] = mapped_column(DateTime)
    delivery_date: Mapped[datetime] = mapped_column(DateTime)
    comment: Mapped[str | None] = mapped_column(String(1024))
