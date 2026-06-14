from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class User(Base):
    __tablename__ = "user"

    user_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    # varchar(40) in legacy DB for SHA1; extended to 255 to accommodate bcrypt hashes
    password: Mapped[str] = mapped_column(String(255))
    password_scheme: Mapped[str] = mapped_column(String(10), server_default="sha1")
    email: Mapped[str] = mapped_column(String(100))
    # FK to employee.id — constraint omitted until employee module is defined
    employee_id: Mapped[int | None] = mapped_column("employee", Integer, nullable=True)
    administrator: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    session_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    privileges: Mapped[list["AccessPrivilege"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )
    settings: Mapped["UserSettings | None"] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False, lazy="selectin"
    )


class AccessPrivilege(Base):
    __tablename__ = "access_privilege"

    user_id: Mapped[str] = mapped_column(
        "user", String(20), ForeignKey("user.user_id"), primary_key=True
    )
    # column named "object" in DB; "object" is a Python builtin so we alias it
    system_object: Mapped[int] = mapped_column("object", Integer, primary_key=True)
    privileges: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    user: Mapped["User"] = relationship(back_populates="privileges")

    @property
    def allow_create(self) -> bool:
        return bool(self.privileges & 1)

    @property
    def allow_read(self) -> bool:
        # Read is bit 1 (value 2), NOT bit 0 — see spec §2
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
    # FKs to store/point_of_sale/cash_drawer omitted until those modules are defined
    store_id: Mapped[int | None] = mapped_column("store", Integer, nullable=True)
    point_sale_id: Mapped[int | None] = mapped_column("point_sale", Integer, nullable=True)
    cash_drawer_id: Mapped[int | None] = mapped_column("cash_drawer", Integer, nullable=True)

    user: Mapped["User"] = relationship(back_populates="settings")
