"""The pure helpers behind the delivery documents (spec 026, research R4-R6)."""

from datetime import date, datetime
from types import SimpleNamespace

import pytest

from app.enums import PaymentTerms
from app.services.print_contexts import _note_groups, _order_status_label, _unit_label

TODAY = date(2026, 10, 25)


@pytest.mark.parametrize(
    ('terms', 'paid', 'due', 'expected'),
    [
        (PaymentTerms.IMMEDIATE, True, datetime(2026, 1, 1), None),
        (PaymentTerms.NET_D, True, datetime(2026, 1, 1), None),
        (PaymentTerms.IMMEDIATE, False, datetime(2026, 12, 1), 'No Pagado'),
        (PaymentTerms.IMMEDIATE, False, datetime(2026, 1, 1), 'No Pagado'),
        (PaymentTerms.NET_D, False, datetime(2026, 10, 24, 23, 59), 'Vencido'),
        (PaymentTerms.NET_D, False, datetime(2026, 10, 25, 0, 0), 'Por Cobrar'),
        (PaymentTerms.NET_D, False, datetime(2026, 10, 26), 'Por Cobrar'),
        (PaymentTerms.NET_D, False, date(2026, 10, 24), 'Vencido'),
    ],
)
def test_order_status_label(terms, paid, due, expected) -> None:  # noqa: ANN001
    assert _order_status_label(terms, paid, due, TODAY) == expected


def test_unit_label_prefers_the_symbol_where_asked() -> None:
    unit = SimpleNamespace(name='Pieza', symbol='pz')
    assert _unit_label(unit, symbol=True) == 'pz'
    assert _unit_label(unit, symbol=False) == 'Pieza'


def test_unit_label_falls_back_to_the_name_without_a_symbol() -> None:
    for symbol in (None, '', '  '):
        assert _unit_label(SimpleNamespace(name='Pieza', symbol=symbol), symbol=True) == 'Pieza'


def test_unit_label_of_no_unit_is_empty() -> None:
    assert _unit_label(None, symbol=True) == ''
    assert _unit_label(None, symbol=False) == ''


def _line(name: str, customer: int, ship_to=None, contact=None, comment=None):  # noqa: ANN001, ANN202
    return SimpleNamespace(
        name=name, customer=customer, ship_to=ship_to, contact=contact, comment=comment
    )


def test_note_groups_keep_the_order_of_first_appearance() -> None:
    lines = [
        _line('a', 1, 10, 5),
        _line('b', 2, 20, 6),
        _line('c', 1, 10, 5),
        _line('d', 3, None, None),
    ]
    groups = _note_groups(lines)

    assert [key for key, _ in groups] == [(1, 10, 5, None), (2, 20, 6, None), (3, None, None, None)]
    assert [[line.name for line in group] for _, group in groups] == [['a', 'c'], ['b'], ['d']]


def test_note_groups_split_on_any_key_part() -> None:
    lines = [
        _line('a', 1, 10, 5, 'x'),
        _line('b', 1, 10, 5, 'y'),
        _line('c', 1, 11, 5, 'x'),
        _line('d', 1, 10, 6, 'x'),
    ]
    assert len(_note_groups(lines)) == 4


def test_note_groups_of_nothing_is_empty() -> None:
    assert _note_groups([]) == []
