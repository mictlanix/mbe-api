"""Pure rules of average cost (spec 031, US4): unit cost, the average, a receipt's lines in turn,
and the restore on cancel (research R5, R6)."""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.purchase_cost_service import apply_lines, average, restore_plan, unit_cost


@pytest.mark.parametrize(
    'price, discount, expected',
    [('120', '0', '120'), ('100', '0.1', '90.0'), ('12.3456789', '0', '12.3456789')],
)
def test_unit_cost_is_price_less_discount(price, discount, expected):  # noqa: ANN001
    assert unit_cost(Decimal(price), Decimal(discount)) == Decimal(expected)


@pytest.mark.parametrize(
    'q, c, r, u, expected',
    [
        ('10', '100', '10', '120', '110.0000'),
        # Negative on hand counts as none.
        ('-5', '100', '10', '120', '120.0000'),
        ('0', '0', '3', '7.5', '7.5000'),
        # Half up at the fourth decimal: (1×1 + 2×1.00005) / 3 = 1.0000333…
        ('1', '1', '2', '1.00005', '1.0000'),
        ('0', '0', '1', '0.00005', '0.0001'),
    ],
)
def test_average(q, c, r, u, expected):  # noqa: ANN001
    assert average(Decimal(q), Decimal(c), Decimal(r), Decimal(u)) == Decimal(expected)


def _line(line_id, product, quantity, order_line=1, price='120', discount='0'):  # noqa: ANN001, ANN202
    return SimpleNamespace(
        receipt_detail_id=line_id,
        product=product,
        product_code=f'P{product}',
        quantity=Decimal(quantity),
        purchase_order_detail=order_line,
        price=None if order_line is None else Decimal(price),
        discount=None if order_line is None else Decimal(discount),
    )


def test_one_line_averages_and_records_its_inputs():
    changes, final, warnings = apply_lines(
        [_line(1, 7, '10')], on_hand={7: Decimal('10')}, costs={7: Decimal('100')}
    )
    assert warnings == []
    assert final == {7: Decimal('110.0000')}
    assert changes == [
        {
            'product': 7,
            'purchase_order_detail': 1,
            'previous_cost': Decimal('100'),
            'new_cost': Decimal('110.0000'),
            'quantity_on_hand': Decimal('10'),
            'quantity_received': Decimal('10'),
            'unit_cost': Decimal('120'),
        }
    ]


def test_two_lines_of_one_product_average_one_after_the_other():
    changes, final, _ = apply_lines(
        [_line(2, 7, '10', 2, '130'), _line(1, 7, '10', 1, '120')],
        on_hand={7: Decimal('10')},
        costs={7: Decimal('100')},
    )
    # Line 1 first: (10×100 + 10×120)/20 = 110; then (20×110 + 10×130)/30 = 116.6667.
    assert [(c['quantity_on_hand'], c['previous_cost'], c['new_cost']) for c in changes] == [
        (Decimal('10'), Decimal('100'), Decimal('110.0000')),
        (Decimal('20'), Decimal('110.0000'), Decimal('116.6667')),
    ]
    assert final == {7: Decimal('116.6667')}


def test_a_product_without_cost_starts_from_zero():
    changes, final, _ = apply_lines([_line(1, 7, '4', price='5')], on_hand={}, costs={})
    assert (changes[0]['previous_cost'], changes[0]['quantity_on_hand']) == (Decimal(0), Decimal(0))
    assert final == {7: Decimal('5.0000')}


def test_negative_on_hand_is_recorded_as_zero():
    changes, _, _ = apply_lines(
        [_line(1, 7, '1')], on_hand={7: Decimal('-3')}, costs={7: Decimal('100')}
    )
    assert changes[0]['quantity_on_hand'] == Decimal(0)


def test_a_zero_price_line_changes_nothing_but_is_on_hand_for_the_next():
    changes, final, warnings = apply_lines(
        [_line(1, 7, '10', 1, '0'), _line(2, 7, '10', 2, '130')],
        on_hand={7: Decimal('10')},
        costs={7: Decimal('100')},
    )
    assert warnings == ['P7 is priced at 0; its cost was not changed']
    # The zero-priced 10 are on hand at the old cost: (20×100 + 10×130)/30 = 110.
    assert [(c['purchase_order_detail'], c['quantity_on_hand']) for c in changes] == [
        (2, Decimal('20'))
    ]
    assert final == {7: Decimal('110.0000')}


def test_lines_without_an_order_line_are_ignored():
    changes, final, warnings = apply_lines(
        [_line(1, 7, '10', None)], on_hand={7: Decimal('10')}, costs={7: Decimal('100')}
    )
    assert (changes, final, warnings) == ([], {}, [])


# ── Restore on cancel (R6) ────────────────────────────────────────────────────


def _change(change_id, product, receipt, previous='100', reverted=None):  # noqa: ANN001, ANN202
    return SimpleNamespace(
        product_cost_change_id=change_id,
        product=product,
        receipt=receipt,
        previous_cost=Decimal(previous),
        reverted_change=reverted,
    )


def test_restore_when_the_receipts_change_is_still_the_latest():
    mine = [_change(4, 7, 50, '100'), _change(5, 7, 50, '110'), _change(6, 8, 50, '3')]
    restores, warnings = restore_plan(
        mine, {7: _change(5, 7, 50, '110'), 8: _change(6, 8, 50, '3')}
    )
    assert restores == [
        {'product': 7, 'cost': Decimal('100'), 'reverted_change': 4},
        {'product': 8, 'cost': Decimal('3'), 'reverted_change': 6},
    ]
    assert warnings == []


def test_no_restore_after_a_later_change():
    later = _change(9, 7, 51, '110')
    restores, warnings = restore_plan([_change(4, 7, 50)], {7: later})
    assert restores == []
    assert warnings == [(7, later)]
