"""A customer's linked addresses and contacts (#132, #133).

`customer_address` and `customer_contact` are real junction tables with real rows that nothing
exposed. The link semantics are the interesting part: replace-all, but only for a collection the
caller actually sent — otherwise an ordinary `PUT` that says nothing about addresses would silently
unlink every one of them.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.customer_service import _get_links, _set_links


def _db(*result_sets) -> AsyncMock:
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=list(result_sets) or None)
    return db


def _rows(rows: list) -> SimpleNamespace:
    return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows))


class TestSetLinks:
    @pytest.mark.asyncio
    async def test_omitting_both_collections_touches_nothing(self) -> None:
        """The guard that makes an unrelated `PUT` safe."""
        db = _db()

        await _set_links(db, 1, addresses=None, contacts=None)

        assert db.execute.await_count == 0

    @pytest.mark.asyncio
    async def test_an_empty_list_unlinks_everything(self) -> None:
        """`[]` is a real instruction, distinct from omitting the field."""
        db = _db(None)

        await _set_links(db, 1, addresses=[], contacts=None)

        # The delete, and no insert to follow it.
        assert db.execute.await_count == 1

    @pytest.mark.asyncio
    async def test_a_list_replaces_the_links_wholesale(self) -> None:
        db = _db(None, None)

        await _set_links(db, 1, addresses=[5, 6], contacts=None)

        assert db.execute.await_count == 2
        assert db.execute.await_args.args[1] == [
            {'customer': 1, 'address': 5},
            {'customer': 1, 'address': 6},
        ]

    @pytest.mark.asyncio
    async def test_contacts_are_set_independently_of_addresses(self) -> None:
        db = _db(None, None)

        await _set_links(db, 1, addresses=None, contacts=[9])

        assert db.execute.await_count == 2
        assert db.execute.await_args.args[1] == [{'customer': 1, 'contact': 9}]

    @pytest.mark.asyncio
    async def test_both_collections_cost_a_delete_and_an_insert_each(self) -> None:
        db = _db(None, None, None, None)

        await _set_links(db, 1, addresses=[5], contacts=[9])

        assert db.execute.await_count == 4


class TestGetLinks:
    @pytest.mark.asyncio
    async def test_it_returns_both_collections_in_two_queries(self) -> None:
        """Two joins, flat — not one lookup per link (the N+1 rule)."""
        addresses = [SimpleNamespace(address_id=n) for n in range(1, 11)]
        contacts = [SimpleNamespace(contact_id=n) for n in range(1, 6)]
        db = _db(_rows(addresses), _rows(contacts))

        found_addresses, found_contacts = await _get_links(db, 1)

        assert len(found_addresses) == 10
        assert len(found_contacts) == 5
        assert db.execute.await_count == 2

    @pytest.mark.asyncio
    async def test_a_customer_with_no_links_returns_empty_lists(self) -> None:
        db = _db(_rows([]), _rows([]))

        assert await _get_links(db, 1) == ([], [])
