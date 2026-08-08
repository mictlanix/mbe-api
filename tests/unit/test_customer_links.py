"""A customer's linked addresses, contacts and taxpayers (#132, #133, #150).

`customer_address`, `customer_contact` and `customer_taxpayer` are real junction tables with real
rows that nothing exposed. The link semantics are the interesting part: replace-all, but only for a
collection the caller actually sent — otherwise an ordinary `PUT` that says nothing about addresses
would silently unlink every one of them.
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
    async def test_omitting_every_collection_touches_nothing(self) -> None:
        """The guard that makes an unrelated `PUT` safe."""
        db = _db()

        await _set_links(db, 1, addresses=None, contacts=None, taxpayers=None)

        assert db.execute.await_count == 0

    @pytest.mark.asyncio
    async def test_an_empty_list_unlinks_everything(self) -> None:
        """`[]` is a real instruction, distinct from omitting the field."""
        db = _db(None)

        await _set_links(db, 1, addresses=[], contacts=None, taxpayers=None)

        # The delete, and no insert to follow it.
        assert db.execute.await_count == 1

    @pytest.mark.asyncio
    async def test_a_list_replaces_the_links_wholesale(self) -> None:
        db = _db(None, None)

        await _set_links(db, 1, addresses=[5, 6], contacts=None, taxpayers=None)

        assert db.execute.await_count == 2
        assert db.execute.await_args.args[1] == [
            {'customer': 1, 'address': 5},
            {'customer': 1, 'address': 6},
        ]

    @pytest.mark.asyncio
    async def test_contacts_are_set_independently_of_addresses(self) -> None:
        db = _db(None, None)

        await _set_links(db, 1, addresses=None, contacts=[9], taxpayers=None)

        assert db.execute.await_count == 2
        assert db.execute.await_args.args[1] == [{'customer': 1, 'contact': 9}]

    @pytest.mark.asyncio
    async def test_taxpayers_are_set_independently_too(self) -> None:
        """Keyed on `taxpayer`, the junction's own column, holding the RFC (#154).

        This assertion said `taxpayer_recipient` and passed, because a mocked session accepts any
        key — it pinned the wrong column name rather than catching it. `tests/unit/
        test_model_schema.py` now checks the mapping against the schema, which is the level at
        which that is checkable at all.
        """
        db = _db(None, None)

        await _set_links(db, 1, addresses=None, contacts=None, taxpayers=['AAA010101AAA'])

        assert db.execute.await_count == 2
        assert db.execute.await_args.args[1] == [{'customer': 1, 'taxpayer': 'AAA010101AAA'}]

    @pytest.mark.asyncio
    async def test_more_than_one_rfc_can_be_linked(self) -> None:
        """`customer_taxpayer` is many-to-many, and the legacy data uses it that way (#150)."""
        db = _db(None, None)

        await _set_links(
            db, 1, addresses=None, contacts=None, taxpayers=['AAA010101AAA', 'BBB020202BB1']
        )

        assert db.execute.await_args.args[1] == [
            {'customer': 1, 'taxpayer': 'AAA010101AAA'},
            {'customer': 1, 'taxpayer': 'BBB020202BB1'},
        ]

    @pytest.mark.asyncio
    async def test_every_collection_costs_a_delete_and_an_insert_each(self) -> None:
        db = _db(None, None, None, None, None, None)

        await _set_links(db, 1, addresses=[5], contacts=[9], taxpayers=['AAA010101AAA'])

        assert db.execute.await_count == 6


class TestGetLinks:
    @pytest.mark.asyncio
    async def test_it_returns_every_collection_in_three_queries(self) -> None:
        """Three joins, flat — not one lookup per link (the N+1 rule)."""
        addresses = [SimpleNamespace(address_id=n) for n in range(1, 11)]
        contacts = [SimpleNamespace(contact_id=n) for n in range(1, 6)]
        taxpayers = [SimpleNamespace(taxpayer_recipient_id='AAA010101AAA')]
        db = _db(_rows(addresses), _rows(contacts), _rows(taxpayers))

        found_addresses, found_contacts, found_taxpayers = await _get_links(db, 1)

        assert len(found_addresses) == 10
        assert len(found_contacts) == 5
        assert len(found_taxpayers) == 1
        assert db.execute.await_count == 3

    @pytest.mark.asyncio
    async def test_a_customer_with_no_links_returns_empty_lists(self) -> None:
        db = _db(_rows([]), _rows([]), _rows([]))

        assert await _get_links(db, 1) == ([], [], [])


class TestAttachLinks:
    """The taxpayers have to arrive expanded, or the customer detail cannot be serialised at all.

    `TaxpayerRecipientResponse` reads `postal_code` and `regime` as SAT catalog objects, so a
    recipient that has not been through `taxpayer_recipient_service.attach_relations` fails
    validation rather than degrading — which makes this an end-to-end check with no database (#150).
    """

    @pytest.mark.asyncio
    async def test_a_linked_taxpayer_validates_into_the_customer_response(self) -> None:
        from decimal import Decimal

        from app.models.customer import Customer, TaxpayerRecipient
        from app.models.product import PriceList
        from app.models.sat_catalog import SatPostalCode, SatTaxRegime
        from app.schemas.customer import CustomerResponse
        from app.services.customer_service import _attach_links

        customer = Customer(
            customer_id=1,
            code='CUST1',
            name='Acme',
            zone=None,
            credit_limit=0,
            credit_days=0,
            price_list=1,
            shipping=False,
            shipping_required_document=False,
            salesperson=None,
            status=0,
            comment=None,
        )
        recipient = TaxpayerRecipient(
            taxpayer_recipient_id='AAA010101AAA',
            name='Acme SA de CV',
            email='facturas@acme.mx',
            postal_code='06000',
            regime='601',
        )
        db = _db(
            _rows([]),                      # addresses
            _rows([]),                      # contacts
            _rows([recipient]),             # taxpayers
            _rows([SatPostalCode(sat_postal_code_id='06000', state='CDMX')]),
            _rows([SatTaxRegime(sat_tax_regime_id='601', description='General')]),
        )
        # What `_attach_customer_relations` would have written; not the subject of this test.
        customer.__dict__['price_list_detail'] = PriceList(
            price_list_id=1,
            name='General',
            high_profit_margin=Decimal('0.5'),
            low_profit_margin=Decimal('0.1'),
        )
        customer.__dict__['salesperson_detail'] = None

        await _attach_links(db, customer)

        # The raw FKs survive the expansion, as everywhere else (#95, #104).
        assert recipient.postal_code == '06000'
        body = CustomerResponse.model_validate(customer)
        assert body.taxpayers[0].taxpayer_recipient_id == 'AAA010101AAA'
        assert body.taxpayers[0].regime is not None
        assert body.taxpayers[0].regime.description == 'General'
