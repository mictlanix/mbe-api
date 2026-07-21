from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.core import Label
from app.schemas.core import LabelCreate, LabelUpdate


async def list_labels(
    db: AsyncSession,
    *,
    search: str | None = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[Sequence[Label], int]:
    base = select(Label)
    count_q = select(func.count()).select_from(Label)

    if search:
        term = f'%{search}%'
        base = base.where(Label.name.ilike(term))
        count_q = count_q.where(Label.name.ilike(term))

    total: int = (await db.execute(count_q)).scalar_one()
    items = (await db.execute(base.offset(skip).limit(limit))).scalars().all()
    return items, total


async def get_label(db: AsyncSession, label_id: int) -> Label | None:
    return await db.get(Label, label_id)


async def create_label(db: AsyncSession, data: LabelCreate) -> Label:
    label = Label(name=data.name, comment=data.comment)
    db.add(label)
    await db.commit()
    await db.refresh(label)
    return label


async def update_label(db: AsyncSession, label: Label, data: LabelUpdate) -> Label:
    if data.name is not None:
        label.name = data.name
    if data.comment is not None:
        label.comment = data.comment
    await db.commit()
    await db.refresh(label)
    return label


async def delete_label(db: AsyncSession, label: Label) -> None:
    await db.delete(label)
    await db.commit()
