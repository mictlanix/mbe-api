"""Guards that keep database constraint violations from surfacing as 500s (#107).

A delete is refused while anything still points at the row: the client removes the
references itself and retries. The blocking tables are named in the error, because a client
can act on "referenced by 3 warehouses" and cannot act on a bare conflict.

The referencing tables are derived from the mapped metadata rather than listed by hand, so a
new foreign key is covered the moment its model exists. Metadata does not cover the handful
of unmapped legacy tables — the `IntegrityError` handler in `app.main` is the backstop for
those, trading the named blocker for a generic 409.
"""

from typing import Any, cast

from fastapi import HTTPException, status
from sqlalchemy import Select, func, literal, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.inspection import inspect
from sqlalchemy.orm import Mapper

import app.models  # noqa: F401  — registers every table, so no FK is missed
from app.db.base import Base


async def find_blocking_references(
    db: AsyncSession, instance: object, *, exempt: frozenset[str] = frozenset()
) -> list[tuple[str, int]]:
    """Return `(table, rows)` for every mapped table still referencing `instance`.

    `exempt` names tables whose rows the delete legitimately cascades away, so they are not
    counted as blockers.
    """
    mapper = cast(Mapper[Any], inspect(type(instance)))
    pk_column = mapper.primary_key[0]
    (pk_value,) = mapper.primary_key_from_instance(instance)
    if pk_value is None:
        return []

    counts = [
        # Labelled by column, not just table: a table can reference the same target twice
        # (inventory_transfer's source and destination warehouse), and "inventory_transfer"
        # listed twice with different counts tells the client nothing about what to clear.
        select(literal(f'{table.name}.{fk.parent.name}').label('reference'), func.count())
        .select_from(table)
        .where(fk.parent == pk_value)
        for table in Base.metadata.tables.values()
        if table.name not in exempt
        for fk in table.foreign_keys
        if fk.column is pk_column
    ]
    if not counts:
        return []

    rows = (await db.execute(union_all(*counts))).all()
    blockers = [(name, n) for name, n in rows if n]
    blockers.sort(key=lambda b: (-b[1], b[0]))
    return blockers


async def assert_not_referenced(
    db: AsyncSession, instance: object, *, exempt: frozenset[str] = frozenset()
) -> None:
    """Raise 409 naming what still references `instance`, listing the largest blockers."""
    blockers = await find_blocking_references(db, instance, exempt=exempt)
    if not blockers:
        return
    shown = ', '.join(f'{name} ({rows})' for name, rows in blockers[:5])
    if len(blockers) > 5:
        shown += f', and {len(blockers) - 5} more'
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=f'Still referenced by {shown} — remove those records first',
    )


async def assert_unique(
    db: AsyncSession,
    model: type,
    column: Any,
    value: Any,
    *,
    exclude_pk: Any = None,
    label: str,
) -> None:
    """Raise 409 when `value` is already taken, before the unique index rejects it.

    `exclude_pk` skips the row being updated, so saving a record without changing its code
    is not a conflict with itself.
    """
    query: Select[Any] = select(model).where(column == value)
    if exclude_pk is not None:
        mapper = cast(Mapper[Any], inspect(model))
        query = query.where(mapper.primary_key[0] != exclude_pk)
    if (await db.execute(query)).first() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f'{label} already exists')
