"""Background analytics exports (spec 037, FR-054, data-model.md `analytics_export`)."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, SmallInteger, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AnalyticsExport(Base):
    __tablename__ = 'analytics_export'

    analytics_export_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: The owner; only they can read or download it.
    user: Mapped[str] = mapped_column(String(20), ForeignKey('user.user_id'))
    source: Mapped[int] = mapped_column(SmallInteger)  # 0 query, 1 dashboard, 2 detail
    format: Mapped[int] = mapped_column(SmallInteger)  # 0 xlsx, 1 pdf, 2 csv
    #: The normalized request as JSON, with the scope and visibility it was checked against.
    definition: Mapped[str] = mapped_column(Text)
    status: Mapped[int] = mapped_column(SmallInteger)  # 0 pending, 1 running, 2 ready, 3 failed
    file_name: Mapped[str | None] = mapped_column(String(100))
    row_count: Mapped[int | None] = mapped_column(Integer)
    creation_time: Mapped[datetime] = mapped_column(DateTime)
    completion_time: Mapped[datetime | None] = mapped_column(DateTime)
    expiry_time: Mapped[datetime | None] = mapped_column(DateTime)
