from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from app.db.base import Base


class MySQLBit(TypeDecorator):
    """bit(1) columns: PyMySQL/aiomysql return raw bytes (b'\\x00'/b'\\x01') for
    these instead of an int, since their BIT converter is a no-op. Plain Boolean
    then does bool(value), and bool(b'\\x00') is True — every row reads back as
    True. Override result_processor (not process_result_value) so we see the raw
    driver value before Boolean's own processor coerces it."""

    impl = Boolean
    cache_ok = True

    def result_processor(self, dialect, coltype):
        def process(value):
            if isinstance(value, bytes):
                return value != b"\x00"
            return value

        return process


class User(Base):
    __tablename__ = "user"

    user_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    # varchar(40) in legacy DB (SHA1 hex); extended to 255 for bcrypt migration — see spec §5
    password: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(250))
    employee_id: Mapped[int | None] = mapped_column(
        "employee", Integer, ForeignKey("employee.employee_id")
    )
    administrator: Mapped[bool] = mapped_column(MySQLBit, default=False, server_default="0")
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
    # store is NOT NULL per schema — a user must belong to a store
    store_id: Mapped[int] = mapped_column("store", Integer, ForeignKey("store.store_id"))
    point_sale_id: Mapped[int | None] = mapped_column(
        "point_sale", Integer, ForeignKey("point_sale.point_sale_id")
    )
    cash_drawer_id: Mapped[int | None] = mapped_column(
        "cash_drawer", Integer, ForeignKey("cash_drawer.cash_drawer_id")
    )

    user: Mapped["User"] = relationship(back_populates="settings")
