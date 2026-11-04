"""Pure helpers of the physical count (spec 030, T006): batches, differences, entries, warnings.

Research R2 (confirm) and R3 (batches). Nothing here touches a database.
"""

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

from app.enums import TransactionType
from app.services import inventory_count_service as svc

NOW = datetime(2026, 10, 25, 12, 0, 0)


def _item(product: int | None = None, code: str | None = None, quantity: str = '1'):
    return SimpleNamespace(product=product, product_code=code, quantity=Decimal(quantity))


def _product(product_id: int, code: str, *, stockable: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        product_id=product_id, code=code, name=f'Producto {code}', stockable=stockable
    )


def _line(line_id: int, product: int, quantity: str, code: str | None = None):
    return SimpleNamespace(
        count_detail_id=line_id,
        product=product,
        product_code=code or f'P{product}',
        product_name=f'Producto {product}',
        quantity=Decimal(quantity),
    )


A = _product(1, 'A')
B = _product(2, 'B')
SRV = _product(3, 'SRV', stockable=False)
BY_ID = {p.product_id: p for p in (A, B, SRV)}
BY_CODE = {svc.code_key(p.code): p for p in (A, B, SRV)}


# ── validate_batch (R3) ───────────────────────────────────────────────────────


class TestValidateBatch:
    def test_a_clean_batch_gives_no_error(self):
        items = [_item(product=1, quantity='3'), _item(code='B', quantity='0')]
        assert svc.validate_batch(items, BY_ID, BY_CODE) == []

    def test_unknown_id_and_unknown_code_each_name_their_index(self):
        items = [_item(product=1), _item(product=99), _item(code='NOPE')]
        errors = svc.validate_batch(items, BY_ID, BY_CODE)
        assert errors == [
            {
                'index': 1,
                'product': 99,
                'reason': 'unknown product',
                'code': 'inventory_count_line.product_unknown',
            },
            {
                'index': 2,
                'product_code': 'NOPE',
                'reason': 'unknown product',
                'code': 'inventory_count_line.product_unknown',
            },
        ]

    def test_not_stockable(self):
        errors = svc.validate_batch([_item(code='SRV')], BY_ID, BY_CODE)
        assert errors == [
            {
                'index': 0,
                'product_code': 'SRV',
                'reason': 'not stockable',
                'code': 'inventory_count_line.not_stockable',
            }
        ]

    def test_repeated_by_id_or_by_code_flags_each_later_entry(self):
        items = [_item(product=1), _item(code='A'), _item(product=2), _item(product=1)]
        errors = svc.validate_batch(items, BY_ID, BY_CODE)
        assert errors == [
            {
                'index': 1,
                'product_code': 'A',
                'reason': 'repeated in the batch',
                'code': 'inventory_count_line.repeated',
            },
            {
                'index': 3,
                'product': 1,
                'reason': 'repeated in the batch',
                'code': 'inventory_count_line.repeated',
            },
        ]

    def test_both_or_neither_of_id_and_code(self):
        items = [_item(product=1, code='A'), _item()]
        errors = svc.validate_batch(items, BY_ID, BY_CODE)
        both = 'give exactly one of product and product_code'
        ref = 'inventory_count_line.product_reference'
        assert [(e['index'], e['reason'], e['code']) for e in errors] == [
            (0, both, ref),
            (1, both, ref),
        ]

    def test_every_bad_entry_is_listed(self):
        items = [_item(product=99), _item(code='SRV'), _item(product=1), _item(product=1)]
        assert [e['index'] for e in svc.validate_batch(items, BY_ID, BY_CODE)] == [0, 1, 3]

    def test_codes_match_regardless_of_case(self):
        # MariaDB's collation finds 'a' for 'A'; the lookup must agree with it.
        assert svc.validate_batch([_item(code='a')], BY_ID, BY_CODE) == []


# ── apply_batch (FR-002, R3) ──────────────────────────────────────────────────


class TestApplyBatch:
    def test_replaces_existing_quantities_and_returns_the_new_lines(self):
        existing = [_line(10, 1, '5')]
        new = svc.apply_batch(existing, [(A, Decimal('2')), (B, Decimal('0'))])

        assert existing[0].quantity == Decimal('2')
        assert new == [
            {'product': 2, 'product_code': 'B', 'product_name': 'Producto B', 'quantity': 0}
        ]

    def test_repeating_a_batch_changes_nothing(self):
        batch = [(A, Decimal('2')), (B, Decimal('7'))]
        existing = [_line(10, 1, '5')]
        for values in svc.apply_batch(existing, batch):
            existing.append(_line(11, values['product'], str(values['quantity'])))

        assert svc.apply_batch(existing, batch) == []
        assert [line.quantity for line in existing] == [Decimal('2'), Decimal('7')]


# ── differences and entry_rows (FR-004, R2) ───────────────────────────────────


class TestDifferences:
    def test_on_hand_and_difference_per_line(self):
        lines = [_line(10, 1, '7'), _line(11, 2, '5'), _line(12, 3, '0')]
        on_hand = {1: Decimal('10'), 2: Decimal('5'), 3: Decimal('-2')}

        assert svc.differences(lines, on_hand) == [
            svc.Difference(line_id=10, product=1, on_hand=Decimal('10'), difference=Decimal(-3)),
            svc.Difference(line_id=11, product=2, on_hand=Decimal('5'), difference=Decimal(0)),
            svc.Difference(line_id=12, product=3, on_hand=Decimal('-2'), difference=Decimal(2)),
        ]

    def test_a_product_never_moved_has_nothing_on_hand(self):
        [only] = svc.differences([_line(10, 1, '4')], {})
        assert (only.on_hand, only.difference) == (Decimal(0), Decimal(4))


class TestEntryRows:
    def test_signs_each_row_with_source_12_and_skips_zero(self):
        differences = [
            svc.Difference(10, 1, Decimal('10'), Decimal('-3')),
            svc.Difference(11, 2, Decimal('5'), Decimal('0')),
            svc.Difference(12, 3, Decimal('-2'), Decimal('2')),
        ]

        rows = svc.entry_rows(40, 6, differences, NOW)

        assert rows == [
            {
                'source': int(TransactionType.PHYSICAL_COUNT),
                'reference': 40,
                'date': NOW,
                'warehouse': 6,
                'product': product,
                'quantity': quantity,
                'lot_number': None,
                'expiration_date': None,
                'serial_number': None,
            }
            for product, quantity in [(1, Decimal('-3')), (3, Decimal('2'))]
        ]
        assert int(TransactionType.PHYSICAL_COUNT) == 12

    def test_no_difference_posts_nothing(self):
        assert svc.entry_rows(40, 6, [svc.Difference(10, 1, Decimal(1), Decimal(0))], NOW) == []


# ── uncounted_lines (FR-005) ──────────────────────────────────────────────────


class TestUncountedLines:
    def test_lists_non_zero_balances_not_on_the_count_including_negative(self):
        on_hand = {1: Decimal('4'), 2: Decimal('-3'), 4: Decimal('0'), 5: Decimal('8')}
        stockable = {2: _product(2, 'B'), 1: _product(1, 'A')}

        lines = svc.uncounted_lines(on_hand, counted_products={5}, stockable=stockable)

        assert lines == [
            {'product': 1, 'product_code': 'A', 'product_name': 'Producto A', 'quantity': 0},
            {'product': 2, 'product_code': 'B', 'product_name': 'Producto B', 'quantity': 0},
        ]

    def test_skips_products_that_are_not_stockable(self):
        on_hand = {3: Decimal('2')}
        assert svc.uncounted_lines(on_hand, counted_products=set(), stockable={}) == []


# ── Warnings (FR-010, FR-010a) ────────────────────────────────────────────────


class TestNegativeAvailable:
    def test_reports_counted_below_reserved(self):
        lines = [_line(10, 1, '2', 'A'), _line(11, 2, '5', 'B'), _line(12, 3, '0', 'C')]
        reserved = {1: Decimal('3'), 2: Decimal('5')}

        assert svc.negative_available(lines, reserved) == [
            {
                'product': 1,
                'product_code': 'A',
                'counted': Decimal('2'),
                'reserved': Decimal('3'),
                'available': Decimal('-1'),
            }
        ]


class TestOverlaps:
    CREATED = datetime(2026, 10, 20, 9, 0, 0)

    def _other(self, count_id: int, serial: int, confirmed: datetime, product: int):
        return svc.OtherCountLine(
            inventory_count_id=count_id, serial=serial, confirmed=confirmed, product=product
        )

    def test_keeps_counts_confirmed_at_or_after_creation_on_this_counts_products(self):
        others = [
            self._other(7, 3, datetime(2026, 10, 20, 8, 59, 59), 1),  # before: dropped
            self._other(8, 4, self.CREATED, 1),  # same second: kept (>=)
            self._other(9, 5, datetime(2026, 10, 21), 2),
            self._other(9, 5, datetime(2026, 10, 21), 6),  # not on this count
        ]

        found = svc.overlaps({1: 'A', 2: 'B'}, others, since=self.CREATED)

        assert found == [
            {'product': 1, 'product_code': 'A', 'inventory_count_id': 8, 'serial': 4},
            {'product': 2, 'product_code': 'B', 'inventory_count_id': 9, 'serial': 5},
        ]

    def test_nothing_overlapping(self):
        assert svc.overlaps({1: 'A'}, [], since=self.CREATED) == []
