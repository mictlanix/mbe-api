"""Pure rules of purchase orders (spec 031, US2 and US3): pending lines, groups, line values, the
lines a PO takes, confirm, the receipts it generates, and receiving progress."""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.enums import CurrencyCode
from app.schemas.purchases import RequestStatus
from app.schemas.sales_order import DocumentStatus
from app.services.purchase_order_service import (
    confirm_refusal,
    group_key,
    group_lines,
    is_pending,
    line_values,
    order_totals,
    progress,
    receipts_to_generate,
    split_taken,
    supplier_mismatch_warning,
    zero_price_warnings,
)

# ── Pending lines (FR-023, FR-024) ────────────────────────────────────────────


def test_a_marked_line_of_an_approved_request_off_any_live_po_is_pending():
    assert is_pending(RequestStatus.APPROVED, True, None)


@pytest.mark.parametrize(
    'state, marked, live',
    [
        (RequestStatus.DRAFT, True, None),
        (RequestStatus.SUBMITTED, True, None),
        (RequestStatus.CANCELLED, True, None),
        (RequestStatus.APPROVED, False, None),
        (RequestStatus.APPROVED, True, 17),
    ],
)
def test_anything_else_is_not_pending(state, marked, live):  # noqa: ANN001
    assert not is_pending(state, marked, live)


# ── Groups (FR-024, R8) ───────────────────────────────────────────────────────


def test_a_group_is_the_supplier_and_the_line_warehouse_facility():
    assert group_key(5, 2, 1) == (5, 2)
    assert group_key(None, 2, 1) == (None, 2)


def test_a_legacy_line_without_warehouse_uses_the_header_facility():
    assert group_key(5, None, 1) == (5, 1)


def _row(supplier, name, facility, quantity, cost, line=1):  # noqa: ANN001, ANN202
    return SimpleNamespace(
        supplier=supplier,
        supplier_name=name,
        facility=facility,
        quantity=Decimal(quantity),
        cost=None if cost is None else Decimal(cost),
        purchase_request_detail_id=line,
    )


def test_groups_count_lines_and_total_at_cost_with_missing_cost_as_zero():
    groups = group_lines(
        [
            _row(5, 'Beta', 1, '2', '10', 1),
            _row(5, 'Beta', 1, '3', None, 2),
            _row(4, 'Alfa', 1, '1', '7.5', 3),
            _row(None, None, 1, '1', '1', 4),
            _row(5, 'Beta', 2, '1', '10', 5),
        ]
    )
    summary = [
        (g['facility'], g['supplier'], g['line_count'], g['estimated_total']) for g in groups
    ]
    assert summary == [
        (1, 4, 1, Decimal('7.5')),
        (1, 5, 2, Decimal('20')),
        (1, None, 1, Decimal('1')),
        (2, 5, 1, Decimal('10')),
    ]
    assert [line.purchase_request_detail_id for line in groups[1]['lines']] == [1, 2]
    assert groups[3]['lines'][0].cost == Decimal('10')
    assert groups[1]['lines'][1].cost == Decimal(0)


# ── Line values (FR-021, FR-023, FR-025) ──────────────────────────────────────


def _product(**overrides) -> SimpleNamespace:
    base = dict(product_id=3, code='P3', name='Tornillo', tax_rate=Decimal('0.160000'))
    base.update(overrides)
    return SimpleNamespace(**base)


def test_a_line_is_priced_from_cost_with_the_fixed_defaults():
    values, warning = line_values(_product(), Decimal('12.5'), Decimal('4'), 9)
    assert warning is None
    assert values == {
        'product': 3,
        'product_code': 'P3',
        'product_name': 'Tornillo',
        'warehouse': 9,
        'quantity': Decimal('4'),
        'price': Decimal('12.5'),
        'tax_rate': Decimal('0.1600'),
        'discount': Decimal(0),
        'currency': CurrencyCode.MXN,
        'exchange_rate': Decimal(1),
        'tax_included': False,
    }


def test_a_product_without_cost_is_priced_at_zero_with_a_warning():
    values, warning = line_values(_product(), None, Decimal('1'), 9)
    assert values['price'] == Decimal(0)
    assert warning == 'P3 has no cost; priced at 0'


@pytest.mark.parametrize(
    'product_supplier, order_supplier, expected',
    [
        (5, 5, None),
        (None, 5, None),
        (4, 5, 'P3 is supplied by another supplier'),
    ],
)
def test_supplier_mismatch_warning(product_supplier, order_supplier, expected):  # noqa: ANN001
    assert supplier_mismatch_warning('P3', product_supplier, order_supplier) == expected


def test_split_taken_keeps_the_requested_order():
    assert split_taken([4, 2, 9, 7], {7, 2}) == ([2, 7], [4, 9])


# ── Confirm (FR-027) ──────────────────────────────────────────────────────────


def _line(line_id, product, warehouse, quantity, price='10'):  # noqa: ANN001, ANN202
    return SimpleNamespace(
        purchase_order_detail_id=line_id,
        product=product,
        product_code=f'P{product}',
        warehouse=warehouse,
        quantity=Decimal(quantity),
        price=Decimal(price),
        discount=Decimal(0),
        tax_rate=Decimal('0.16'),
    )


def test_a_draft_with_supplier_and_quantities_confirms():
    assert confirm_refusal(DocumentStatus.DRAFT, 5, [_line(1, 1, 1, '1')]) is None


@pytest.mark.parametrize(
    'state, supplier, lines, reason',
    [
        (
            DocumentStatus.COMPLETED,
            5,
            [_line(1, 1, 1, '1')],
            'Only a draft purchase order can be confirmed',
        ),
        (
            DocumentStatus.CANCELLED,
            5,
            [_line(1, 1, 1, '1')],
            'Only a draft purchase order can be confirmed',
        ),
        (DocumentStatus.DRAFT, None, [_line(1, 1, 1, '1')], 'The purchase order has no supplier'),
        (DocumentStatus.DRAFT, 5, [], 'The purchase order has no lines'),
        (DocumentStatus.DRAFT, 5, [_line(1, 1, 1, '0')], 'Line P1 has no quantity'),
    ],
)
def test_confirm_refusals(state, supplier, lines, reason):  # noqa: ANN001
    assert confirm_refusal(state, supplier, lines) == reason


def test_lines_priced_at_zero_are_warned():
    lines = [_line(1, 1, 1, '1', '0'), _line(2, 2, 1, '1'), _line(3, 3, 1, '1', '0')]
    assert zero_price_warnings(lines) == ['P1 is priced at 0', 'P3 is priced at 0']


def test_receipts_per_warehouse_with_pending_quantities():
    lines = [
        _line(1, 1, 7, '5'),
        _line(2, 2, 3, '4'),
        _line(3, 3, 7, '2'),
        _line(4, 4, 7, '9'),  # non-stockable
        _line(5, 5, 8, '1'),  # fully held already
    ]
    generated = receipts_to_generate(
        lines, stockable={1, 2, 3, 5}, held={1: Decimal('2'), 5: Decimal('1')}
    )
    assert list(generated) == [3, 7]
    assert [(line.purchase_order_detail_id, pending) for line, pending in generated[7]] == [
        (1, Decimal('3')),
        (3, Decimal('2')),
    ]
    assert [(line.purchase_order_detail_id, pending) for line, pending in generated[3]] == [
        (2, Decimal('4'))
    ]


def test_a_po_of_only_non_stockable_lines_generates_nothing():
    assert receipts_to_generate([_line(1, 1, 7, '5')], stockable=set(), held={}) == {}


# ── Progress (FR-030) and totals ──────────────────────────────────────────────


def test_progress_floors_pending_at_zero():
    lines = [_line(1, 1, 7, '5'), _line(2, 2, 7, '4')]
    assert progress(lines, {1: Decimal('2'), 2: Decimal('6')}) == {
        1: (Decimal('2'), Decimal('3')),
        2: (Decimal('6'), Decimal(0)),
    }


def test_order_totals_add_tax_on_top_and_round_once():
    lines = [_line(1, 1, 7, '3', '0.333'), _line(2, 2, 7, '1', '10')]
    lines[1].tax_rate = Decimal(0)
    assert order_totals(lines) == (Decimal('11.00'), Decimal('0.16'), Decimal('11.16'))
