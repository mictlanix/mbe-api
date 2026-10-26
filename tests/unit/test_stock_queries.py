"""Pure helpers of the stock queries (spec 030, US3, T013): merging, filtering, transit, paging."""

from decimal import Decimal

from app.services import stock_query_service as q

D = Decimal


def test_merge_stock_unions_the_products_and_derives_available() -> None:
    merged = q.merge_stock(
        on_hand={1: D('10'), 2: D('0')},
        reserved={1: D('4'), 3: D('2')},
        incoming={2: D('5')},
    )

    assert merged[1] == q.Figures(D('10'), D('4'), D('6'), D('0'))
    assert merged[2] == q.Figures(D('0'), D('0'), D('0'), D('5'))
    assert merged[3] == q.Figures(D('0'), D('2'), D('-2'), D('0'))


def test_keep_row_drops_all_zero_rows_unless_asked() -> None:
    zero = q.Figures(D('0'), D('0'), D('0'), D('0'))
    incoming_only = q.Figures(D('0'), D('0'), D('0'), D('3'))

    assert q.keep_row(zero, include_zero=False) is False
    assert q.keep_row(zero, include_zero=True) is True
    assert q.keep_row(incoming_only, include_zero=False) is True


def test_split_transit_separates_transit_warehouses_and_drops_zero_balances() -> None:
    on_hand = {1: D('5'), 2: D('3'), 4: D('0'), 5: D('-1')}

    regular, transit = q.split_transit(on_hand, {2: 10, 4: 11, 5: 12})

    assert regular == {1: D('5')}
    assert [(t.facility, t.warehouse, t.on_hand) for t in transit] == [
        (10, 2, D('3')),
        (12, 5, D('-1')),
    ]


def test_sort_and_page_orders_by_code_then_slices() -> None:
    codes = {1: 'C', 2: 'A', 3: 'B', 4: 'D'}

    items, total = q.sort_and_page([1, 2, 3, 4], codes, skip=1, limit=2)

    assert items == [3, 1]
    assert total == 4


def test_sort_and_page_breaks_code_ties_by_product_id() -> None:
    items, _ = q.sort_and_page([9, 3], {9: 'X', 3: 'X'}, skip=0, limit=10)

    assert items == [3, 9]
