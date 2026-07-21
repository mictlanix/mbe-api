from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SatCfdiUsage(Base):
    __tablename__ = 'sat_cfdi_usage'

    sat_cfdi_usage_id: Mapped[str] = mapped_column(String(4), primary_key=True)
    description: Mapped[str] = mapped_column(String(256))


class SatCountry(Base):
    __tablename__ = 'sat_country'

    sat_country_id: Mapped[str] = mapped_column(String(3), primary_key=True)
    description: Mapped[str] = mapped_column(String(256))


class SatCurrency(Base):
    __tablename__ = 'sat_currency'

    sat_currency_id: Mapped[str] = mapped_column(String(3), primary_key=True)
    description: Mapped[str] = mapped_column(String(256))


class SatPostalCode(Base):
    __tablename__ = 'sat_postal_code'

    sat_postal_code_id: Mapped[str] = mapped_column(String(5), primary_key=True)
    state: Mapped[str] = mapped_column(String(4))
    borough: Mapped[str | None] = mapped_column(String(3))
    locality: Mapped[str | None] = mapped_column(String(2))


class SatProductService(Base):
    __tablename__ = 'sat_product_service'

    sat_product_service_id: Mapped[str] = mapped_column(String(8), primary_key=True)
    description: Mapped[str] = mapped_column(String(256))
    keywords: Mapped[str | None] = mapped_column(String(1024))


class SatReasonCancellation(Base):
    __tablename__ = 'sat_reason_cancellation'

    sat_reason_cancellation_id: Mapped[str] = mapped_column(String(2), primary_key=True)
    description: Mapped[str | None] = mapped_column(String(100))


class SatTaxRegime(Base):
    __tablename__ = 'sat_tax_regime'

    sat_tax_regime_id: Mapped[str] = mapped_column(String(3), primary_key=True)
    description: Mapped[str] = mapped_column(String(256))


class SatUnitOfMeasurement(Base):
    __tablename__ = 'sat_unit_of_measurement'

    sat_unit_of_measurement_id: Mapped[str] = mapped_column(String(3), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(String(1024))
    symbol: Mapped[str | None] = mapped_column(String(32))
