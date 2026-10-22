"""Bank accounts — the account a supplier is paid into.

`bank_account` has been a real table with real rows, linked to suppliers through
`supplier_bank_account`, but nothing produced one: a client could not create an account to link,
nor read one it had linked (#13). This is the missing half.

There is no `status` column on `bank_account`, so unlike the other catalogs there is nothing to
filter on but the search term.
"""

from collections.abc import Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.core import BankAccount
from app.schemas.core import BankAccountCreate, BankAccountUpdate
from app.services.references import assert_not_referenced


async def list_bank_accounts(
    db: AsyncSession,
    *,
    search: str | None = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[Sequence[BankAccount], int]:
    base = select(BankAccount)
    count_q = select(func.count()).select_from(BankAccount)

    if search:
        term = f'%{search}%'
        condition = or_(
            BankAccount.bank_name.ilike(term),
            BankAccount.account_number.ilike(term),
            BankAccount.reference.ilike(term),
        )
        base = base.where(condition)
        count_q = count_q.where(condition)

    total: int = (await db.execute(count_q)).scalar_one()
    items = (await db.execute(base.offset(skip).limit(limit))).scalars().all()
    return items, total


async def get_bank_account(db: AsyncSession, bank_account_id: int) -> BankAccount | None:
    return await db.get(BankAccount, bank_account_id)


async def create_bank_account(db: AsyncSession, data: BankAccountCreate) -> BankAccount:
    account = BankAccount(
        bank_name=data.bank_name,
        account_number=data.account_number,
        reference=data.reference,
        routing_number=data.routing_number,
        comment=data.comment,
    )
    db.add(account)
    await db.commit()
    await db.refresh(account)
    return account


async def update_bank_account(
    db: AsyncSession, account: BankAccount, data: BankAccountUpdate
) -> BankAccount:
    if data.bank_name is not None:
        account.bank_name = data.bank_name
    if data.account_number is not None:
        account.account_number = data.account_number
    if data.reference is not None:
        account.reference = data.reference
    if data.routing_number is not None:
        account.routing_number = data.routing_number
    if data.comment is not None:
        account.comment = data.comment
    await db.commit()
    await db.refresh(account)
    return account


async def delete_bank_account(db: AsyncSession, account: BankAccount) -> None:
    await assert_not_referenced(db, account)
    await db.delete(account)
    await db.commit()
