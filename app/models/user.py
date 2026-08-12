from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.enums import EntityStatus
from app.models.core import CashDrawer, Facility, PointSale


class User(Base):
    __tablename__ = 'user'

    user_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    # varchar(40) in legacy DB (SHA1 hex); extended to 255 for bcrypt migration — see spec §5
    password: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(250))
    # NOT NULL since migration 012 (#127) — every user is an employee, or it cannot author
    # the documents it is going to be asked to author
    employee_id: Mapped[int] = mapped_column(
        'employee', Integer, ForeignKey('employee.employee_id')
    )
    # bit(1) in DB; SQLAlchemy Boolean maps correctly
    administrator: Mapped[bool] = mapped_column(Boolean, default=False, server_default='0')
    status: Mapped[EntityStatus] = mapped_column(
        Integer, default=EntityStatus.ACTIVE, server_default='0'
    )
    session_version: Mapped[int] = mapped_column(Integer, default=0, server_default='0')
    # The profile this account was last provisioned from — provenance only (spec 014, FR-022).
    # No authorization decision reads it, and hand-editing privileges does not clear it. Nullable
    # because every account predating spec 014 has no origin, as does any created without one.
    # A plain FK, deliberately: `assert_not_referenced` derives its blockers from FK metadata, so
    # this is what refuses to delete a profile users were provisioned from (FR-008).
    profile_id: Mapped[int | None] = mapped_column(
        'profile', Integer, ForeignKey('user_profile.user_profile_id')
    )

    privileges: Mapped[list['AccessPrivilege']] = relationship(
        back_populates='user', cascade='all, delete-orphan', lazy='selectin'
    )
    # Deliberately NO `profile` relationship. The FK column above is all this needs: the profile's
    # name is resolved for a whole page by `user_service.profile_names_for` via `batch_fetch`, and
    # `assert_not_referenced` reads FK metadata rather than ORM relationships. A `lazy='joined'`
    # relationship here was measured costing four queries per user list instead of one — the join
    # pulled in `UserProfile`, whose own `lazy='selectin'` privileges then loaded for a matrix
    # nobody reads. See test_profile_names_cost_one_query_for_the_whole_page.
    settings: Mapped['UserSettings | None'] = relationship(
        back_populates='user', cascade='all, delete-orphan', uselist=False, lazy='selectin'
    )


class AccessPrivilege(Base):
    __tablename__ = 'access_privilege'

    access_privilege_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column('user', String(20), ForeignKey('user.user_id'))
    # column name is "object" in DB — reserved Python builtin, aliased here
    system_object: Mapped[int] = mapped_column('object', Integer)
    privileges: Mapped[int] = mapped_column(Integer, default=0, server_default='0')

    user: Mapped['User'] = relationship(back_populates='privileges')

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


class UserProfile(Base):
    """A named, reusable permission template (spec 014).

    A template, not a live grouping: applying one copies its masks into `access_privilege` and the
    copy is the account's own. Editing a profile afterwards reaches nobody. Nothing here is
    consulted when a request is authorized.
    """

    __tablename__ = 'user_profile'

    user_profile_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # utf8mb3_unicode_ci in the DB makes uniqueness case-insensitive there, but the service
    # compares on func.lower() so SQLite agrees — see user_profile_service (research R4)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(String(250))
    status: Mapped[EntityStatus] = mapped_column(
        Integer, default=EntityStatus.ACTIVE, server_default='0'
    )

    privileges: Mapped[list['UserProfilePrivilege']] = relationship(
        back_populates='profile', cascade='all, delete-orphan', lazy='selectin'
    )


class UserProfilePrivilege(Base):
    """One entry in a profile: a system object and the mask granted on it.

    **Sparse, unlike `access_privilege`.** A row exists only for an object the profile grants
    something on; absence means denied. A user carries all 107 rows, a profile only what it grants,
    and the apply is the translation between the two shapes (spec 014, FR-003).
    """

    __tablename__ = 'user_profile_privilege'

    user_profile_privilege_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_profile_id: Mapped[int] = mapped_column(
        'user_profile', Integer, ForeignKey('user_profile.user_profile_id')
    )
    # column name is "object" in DB — reserved Python builtin, aliased as AccessPrivilege does
    system_object: Mapped[int] = mapped_column('object', Integer)
    privileges: Mapped[int] = mapped_column(Integer, default=0, server_default='0')

    profile: Mapped['UserProfile'] = relationship(back_populates='privileges')

    @property
    def allow_create(self) -> bool:
        return bool(self.privileges & 1)

    @property
    def allow_read(self) -> bool:
        # Read is bit 1 (value 2), NOT bit 0 — matches AccessPrivilege
        return bool(self.privileges & 2)

    @property
    def allow_update(self) -> bool:
        return bool(self.privileges & 4)

    @property
    def allow_delete(self) -> bool:
        return bool(self.privileges & 8)


class UserSettings(Base):
    __tablename__ = 'user_settings'

    user_id: Mapped[str] = mapped_column(
        'user', String(20), ForeignKey('user.user_id'), primary_key=True
    )
    # facility is NOT NULL per schema — a user must belong to a facility
    facility_id: Mapped[int] = mapped_column(
        'facility', Integer, ForeignKey('facility.facility_id')
    )
    point_sale_id: Mapped[int | None] = mapped_column(
        'point_sale', Integer, ForeignKey('point_sale.point_sale_id')
    )
    cash_drawer_id: Mapped[int | None] = mapped_column(
        'cash_drawer', Integer, ForeignKey('cash_drawer.cash_drawer_id')
    )

    user: Mapped['User'] = relationship(back_populates='settings')
    # Eager-loaded so /auth/me can expose location names without extra round-trips
    facility: Mapped['Facility'] = relationship(lazy='joined')
    point_sale: Mapped['PointSale | None'] = relationship(lazy='joined')
    cash_drawer: Mapped['CashDrawer | None'] = relationship(lazy='joined')

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
