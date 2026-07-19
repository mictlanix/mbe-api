from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.core import Expense
from app.schemas.core import ExpenseCreate, ExpenseUpdate


async def list_expenses(
    db: AsyncSession, *, search: str | None = None, skip: int = 0, limit: int = 20
) -> tuple[Sequence[Expense], int]:
    base = select(Expense)
    count_q = select(func.count()).select_from(Expense)

    if search:
        term = f"%{search}%"
        condition = Expense.expense.ilike(term)
        base = base.where(condition)
        count_q = count_q.where(condition)

    total: int = (await db.execute(count_q)).scalar_one()
    items = (await db.execute(base.offset(skip).limit(limit))).scalars().all()
    return items, total


async def get_expense(db: AsyncSession, expense_id: int) -> Expense | None:
    return await db.get(Expense, expense_id)


async def create_expense(db: AsyncSession, data: ExpenseCreate) -> Expense:
    expense = Expense(expense=data.name, comment=data.comment)
    db.add(expense)
    await db.commit()
    await db.refresh(expense)
    return expense


async def update_expense(db: AsyncSession, expense: Expense, data: ExpenseUpdate) -> Expense:
    if data.name is not None:
        expense.expense = data.name
    if data.comment is not None:
        expense.comment = data.comment
    await db.commit()
    await db.refresh(expense)
    return expense


async def delete_expense(db: AsyncSession, expense: Expense) -> None:
    await db.delete(expense)
    await db.commit()
