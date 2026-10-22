"""`list_bank_accounts` builds its search over the three text columns a clerk would type (#13)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.bank_account_service import list_bank_accounts


def _db() -> AsyncMock:
    count = SimpleNamespace(scalar_one=lambda: 0)
    rows = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[count, rows])
    return db


def _compiled(db: AsyncMock, call: int) -> str:
    statement = db.execute.await_args_list[call].args[0]
    return str(statement.compile(compile_kwargs={'literal_binds': True}))


@pytest.mark.asyncio
async def test_search_filters_on_name_number_and_reference() -> None:
    db = _db()

    await list_bank_accounts(db, search='bbva')

    for call in (0, 1):
        sql = _compiled(db, call)
        assert 'lower(bank_account.bank_name)' in sql
        assert 'lower(bank_account.account_number)' in sql
        assert 'lower(bank_account.reference)' in sql
        assert 'bbva' in sql


@pytest.mark.asyncio
async def test_without_search_no_filter_is_applied() -> None:
    db = _db()

    await list_bank_accounts(db)

    assert 'WHERE' not in _compiled(db, 0)
    assert 'WHERE' not in _compiled(db, 1)


@pytest.mark.asyncio
async def test_skip_and_limit_are_applied() -> None:
    db = _db()

    await list_bank_accounts(db, skip=10, limit=5)

    sql = _compiled(db, 1)
    assert 'LIMIT 5' in sql
    assert 'OFFSET 10' in sql
