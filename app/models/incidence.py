from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Incidence(Base):
    """Generic event/incident log — polymorphic on (source, instance_id)."""

    __tablename__ = 'incidence'

    incidence_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[int] = mapped_column(Integer)
    instance_id: Mapped[int] = mapped_column(Integer)
    modification_time: Mapped[datetime | None] = mapped_column(DateTime)
    updater: Mapped[int] = mapped_column(Integer, ForeignKey('employee.employee_id'))
    content: Mapped[str | None] = mapped_column(String(1000))
    comment: Mapped[str | None] = mapped_column(String(500))
