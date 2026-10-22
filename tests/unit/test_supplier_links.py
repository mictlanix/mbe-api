"""A supplier's linked addresses, contacts and bank accounts, and its agreements (#13).

`supplier_address`, `supplier_contact` and `supplier_bank_account` are real junction tables with
real rows that nothing exposed. The link semantics are the same as a customer's: replace-all, but
only for a collection the caller actually sent, so an ordinary `PUT` that says nothing about
contacts does not unlink them.

The inserted keys are read off the `Table()` definitions rather than retyped. A mocked session
accepts any key, so a retyped column name pins the test to itself instead of to the schema (#154).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models.supplier import supplier_address, supplier_bank_account, supplier_contact
from app.services.supplier_service import _get_links, _set_links

_OWNER = 'supplier'


def _other(table) -> str:  # noqa: ANN001
    """The junction's column that is not the supplier, as the table defines it."""
    (name,) = [c.name for c in table.columns if c.name != _OWNER]
    return name


def _db(*result_sets) -> AsyncMock:
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=list(result_sets) or None)
    return db


def _rows(rows: list) -> SimpleNamespace:
    return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows))


class TestSetLinks:
    @pytest.mark.asyncio
    async def test_omitting_every_collection_touches_nothing(self) -> None:
        """The guard that makes an unrelated `PUT` safe."""
        db = _db()

        await _set_links(db, 1, addresses=None, contacts=None, bank_accounts=None)

        assert db.execute.await_count == 0

    @pytest.mark.asyncio
    async def test_an_empty_list_unlinks_everything_and_inserts_nothing(self) -> None:
        db = _db(None)

        await _set_links(db, 1, addresses=None, contacts=[], bank_accounts=None)

        # The delete, and no insert to follow it.
        assert db.execute.await_count == 1
        statement = db.execute.await_args.args[0]
        assert statement.table is supplier_contact

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ('field', 'table'),
        [
            ('addresses', supplier_address),
            ('contacts', supplier_contact),
            ('bank_accounts', supplier_bank_account),
        ],
    )
    async def test_a_list_replaces_that_collection_wholesale(self, field: str, table) -> None:  # noqa: ANN001
        db = _db(None, None)
        sent = {'addresses': None, 'contacts': None, 'bank_accounts': None, field: [5, 6]}

        await _set_links(db, 1, **sent)

        assert db.execute.await_count == 2
        delete_call, insert_call = db.execute.await_args_list
        assert delete_call.args[0].table is table
        assert insert_call.args[0].table is table
        column = _other(table)
        assert insert_call.args[1] == [{_OWNER: 1, column: 5}, {_OWNER: 1, column: 6}]

    @pytest.mark.asyncio
    async def test_every_collection_costs_a_delete_and_an_insert_each(self) -> None:
        db = _db(None, None, None, None, None, None)

        await _set_links(db, 1, addresses=[5], contacts=[9], bank_accounts=[2])

        assert db.execute.await_count == 6


class TestGetLinks:
    @pytest.mark.asyncio
    async def test_it_returns_every_collection_in_four_queries(self) -> None:
        """Four joins, flat — not one lookup per link (the N+1 rule)."""
        addresses = [SimpleNamespace(address_id=n) for n in range(1, 4)]
        contacts = [SimpleNamespace(contact_id=n) for n in range(1, 3)]
        accounts = [SimpleNamespace(bank_account_id=1)]
        agreements = [SimpleNamespace(supplier_agreement_id=1)]
        db = _db(_rows(addresses), _rows(contacts), _rows(accounts), _rows(agreements))

        found = await _get_links(db, 1)

        assert [len(x) for x in found] == [3, 2, 1, 1]
        assert db.execute.await_count == 4

    @pytest.mark.asyncio
    async def test_a_supplier_with_no_links_returns_empty_lists(self) -> None:
        db = _db(_rows([]), _rows([]), _rows([]), _rows([]))

        assert await _get_links(db, 1) == ([], [], [], [])

    @pytest.mark.asyncio
    async def test_agreements_are_read_ordered_by_start_then_id(self) -> None:
        db = _db(_rows([]), _rows([]), _rows([]), _rows([]))

        await _get_links(db, 1)

        sql = str(db.execute.await_args_list[3].args[0].compile())
        assert 'ORDER BY supplier_agreement.start, supplier_agreement.supplier_agreement_id' in sql
