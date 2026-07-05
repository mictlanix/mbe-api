from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute


async def batch_fetch(
    db: AsyncSession, model: type, pk_column: InstrumentedAttribute, ids: Iterable[Any]
) -> dict[Any, Any]:
    """Fetch all rows of `model` whose `pk_column` is in `ids`, keyed by that PK value.

    Used to batch-load FK targets for response embedding (FR-039) and avoid N+1 queries
    on list endpoints.
    """
    id_set = {i for i in ids if i is not None}
    if not id_set:
        return {}
    rows = (await db.execute(select(model).where(pk_column.in_(id_set)))).scalars().all()
    return {getattr(row, pk_column.key): row for row in rows}
