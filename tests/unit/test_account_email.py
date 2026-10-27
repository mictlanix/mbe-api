"""Who an account statement goes to (spec 033, FR-024): contacts and typed addresses, merged."""

import pytest
from fastapi import HTTPException

from app.services.account_email import merge_recipients


def test_contacts_come_first_and_duplicates_go() -> None:
    merged = merge_recipients(['ana@cliente.mx', 'Cobranza@cliente.mx'], ['cobranza@cliente.mx'])

    assert merged == ['ana@cliente.mx', 'Cobranza@cliente.mx']


def test_typed_addresses_alone_are_enough() -> None:
    assert merge_recipients([], ['a@x.mx']) == ['a@x.mx']


def test_ten_after_deduplication_is_the_limit() -> None:
    contacts = [f'c{i}@x.mx' for i in range(6)]
    typed = [f'C{i}@x.mx' for i in range(6)] + [f't{i}@x.mx' for i in range(4)]

    assert len(merge_recipients(contacts, typed)) == 10

    with pytest.raises(HTTPException) as refusal:
        merge_recipients(contacts, [*typed, 'one-more@x.mx'])
    assert refusal.value.status_code == 422


def test_no_one_to_send_to_is_refused() -> None:
    with pytest.raises(HTTPException) as refusal:
        merge_recipients([], [])

    assert refusal.value.status_code == 422
