"""Audit entries for money that is unwound or questioned.

Reversing a payment application and rejecting a payment both have to leave evidence: who did it,
when, and why (FR-045a, FR-072). `sales_order_payment` carries a `cancelled` flag but no canceller
and no timestamp, so the evidence goes in the `incidence` log rather than in a schema migration
this feature does not otherwise need.

`incidence` is keyed by `(source, instance_id)` and `SourceType` has no value for an application,
so an entry keys to the owning payment and names the application in `content`.
"""

from datetime import datetime

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import SourceType
from app.models.incidence import Incidence


def record(
    db: AsyncSession,
    *,
    source: SourceType,
    instance_id: int,
    updater: int,
    reason: str,
    context: str | None = None,
) -> Incidence:
    """Stage an audit entry. The caller commits it with the transaction it belongs to.

    The reason is required and refused when blank: SC-009 allows no anonymous or unexplained
    reversal, and an empty string would satisfy a merely-present check while explaining nothing.
    """
    if not reason or not reason.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail='A reason is required and cannot be blank',
        )

    entry = Incidence(
        source=int(source),
        instance_id=instance_id,
        modification_time=datetime.now(),
        updater=updater,
        content=context,
        comment=reason.strip(),
    )
    db.add(entry)
    return entry
