"""How a credit note's untraced amount is shared out to the orders on its invoice (spec 033, R2)."""

from decimal import Decimal

from app.services.sales_order_service import credit_note_shares


def test_the_amount_is_split_by_each_orders_part() -> None:
    shares = credit_note_shares(Decimal('300'), {1: Decimal('100'), 2: Decimal('200')})

    assert shares == {1: Decimal('100.00'), 2: Decimal('200.00')}


def test_the_highest_order_takes_the_rounding_remainder() -> None:
    parts = {7: Decimal('1'), 3: Decimal('1'), 5: Decimal('1')}

    shares = credit_note_shares(Decimal('100'), parts)

    assert shares == {3: Decimal('33.33'), 5: Decimal('33.33'), 7: Decimal('33.34')}
    assert sum(shares.values()) == Decimal('100')


def test_a_single_order_takes_the_whole_amount() -> None:
    assert credit_note_shares(Decimal('12.34'), {9: Decimal('500')}) == {9: Decimal('12.34')}


def test_parts_that_sum_to_nothing_share_nothing() -> None:
    assert credit_note_shares(Decimal('50'), {1: Decimal('0'), 2: Decimal('0')}) == {}
    assert credit_note_shares(Decimal('50'), {}) == {}


def test_a_zero_amount_gives_every_order_zero() -> None:
    shares = credit_note_shares(Decimal('0'), {1: Decimal('10'), 2: Decimal('30')})

    assert shares == {1: Decimal('0.00'), 2: Decimal('0.00')}
