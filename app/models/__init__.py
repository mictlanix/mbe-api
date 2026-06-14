# Import all model modules so they register with Base.metadata.
# Required for Alembic autogenerate and SQLAlchemy relationship resolution.
from app.models import (  # noqa: F401
    commission,
    core,
    customer,
    fiscal,
    front_desk,
    incidence,
    inventory,
    logistics,
    product,
    purchases,
    sales,
    sat_catalog,
    supplier,
    technical_service,
    user,
)
