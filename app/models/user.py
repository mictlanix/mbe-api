from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.enums import EntityStatus
from app.models.core import CashDrawer, Facility, PointSale


class User(Base):
    __tablename__ = "user"

    user_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    # varchar(40) in legacy DB (SHA1 hex); extended to 255 for bcrypt migration — see spec §5
    password: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(250))
    employee_id: Mapped[int | None] = mapped_column(
        "employee", Integer, ForeignKey("employee.employee_id")
    )
    # bit(1) in DB; SQLAlchemy Boolean maps correctly
    administrator: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    status: Mapped[EntityStatus] = mapped_column(
        Integer, default=EntityStatus.ACTIVE, server_default="0"
    )
    session_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    privileges: Mapped[list["AccessPrivilege"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )
    settings: Mapped["UserSettings | None"] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False, lazy="selectin"
    )


class AccessPrivilege(Base):
    __tablename__ = "access_privilege"

    access_privilege_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column("user", String(20), ForeignKey("user.user_id"))
    # column name is "object" in DB — reserved Python builtin, aliased here
    system_object: Mapped[int] = mapped_column("object", Integer)
    privileges: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    user: Mapped["User"] = relationship(back_populates="privileges")

    @property
    def allow_create(self) -> bool:
        return bool(self.privileges & 1)

    @property
    def allow_read(self) -> bool:
        # Read is bit 1 (value 2), NOT bit 0 — spec §2 note
        return bool(self.privileges & 2)

    @property
    def allow_update(self) -> bool:
        return bool(self.privileges & 4)

    @property
    def allow_delete(self) -> bool:
        return bool(self.privileges & 8)


class UserSettings(Base):
    __tablename__ = "user_settings"

    user_id: Mapped[str] = mapped_column(
        "user", String(20), ForeignKey("user.user_id"), primary_key=True
    )
    # facility is NOT NULL per schema — a user must belong to a facility
    facility_id: Mapped[int] = mapped_column(
        "facility", Integer, ForeignKey("facility.facility_id")
    )
    point_sale_id: Mapped[int | None] = mapped_column(
        "point_sale", Integer, ForeignKey("point_sale.point_sale_id")
    )
    cash_drawer_id: Mapped[int | None] = mapped_column(
        "cash_drawer", Integer, ForeignKey("cash_drawer.cash_drawer_id")
    )

    user: Mapped["User"] = relationship(back_populates="settings")
    # Eager-loaded so /auth/me can expose location names without extra round-trips
    facility: Mapped["Facility"] = relationship(lazy="joined")
    point_sale: Mapped["PointSale | None"] = relationship(lazy="joined")
    cash_drawer: Mapped["CashDrawer | None"] = relationship(lazy="joined")

    @property
    def facility_code(self) -> str | None:
        return self.facility.code if self.facility else None

    @property
    def facility_name(self) -> str | None:
        return self.facility.name if self.facility else None

    @property
    def point_sale_code(self) -> str | None:
        return self.point_sale.code if self.point_sale else None

    @property
    def point_sale_name(self) -> str | None:
        return self.point_sale.name if self.point_sale else None

    @property
    def cash_drawer_code(self) -> str | None:
        return self.cash_drawer.code if self.cash_drawer else None

    @property
    def cash_drawer_name(self) -> str | None:
        return self.cash_drawer.name if self.cash_drawer else None
