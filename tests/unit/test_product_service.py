from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.product import Product
from app.schemas.product import ProductMergeRequest
from app.services.product_service import (
    _apply_product_filters,
    get_label_facets,
    merge_products,
    preview_merge,
)
from app.services.references import referencing_columns


def _no_filters() -> dict:
    return dict(
        search=None,
        label=None,
        status=None,
        stockable=None,
        salable=None,
        purchasable=None,
        supplier=None,
    )


def test_apply_product_filters_search_matches_multiple_columns() -> None:
    query = _apply_product_filters(select(Product), **{**_no_filters(), 'search': 'widget'})
    compiled = str(query.compile(compile_kwargs={'literal_binds': True}))
    assert 'widget' in compiled
    assert 'lower(product.code)' in compiled
    assert 'lower(product.name)' in compiled


def test_apply_product_filters_label_requires_all_labels() -> None:
    query = _apply_product_filters(select(Product), **{**_no_filters(), 'label': [2, 5]})
    compiled = str(query.compile(compile_kwargs={'literal_binds': True}))
    assert 'product_label' in compiled
    assert 'count(distinct(product_label.label)) = 2' in compiled


def test_apply_product_filters_no_filters_leaves_query_unchanged() -> None:
    query = _apply_product_filters(select(Product), **_no_filters())
    assert query.whereclause is None


@pytest.mark.asyncio
async def test_get_label_facets_returns_rows_from_db() -> None:
    result = MagicMock()
    result.all.return_value = [
        SimpleNamespace(label_id=3, count=42),
        SimpleNamespace(label_id=7, count=12),
    ]
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)

    rows = await get_label_facets(db, label=[3])

    assert [(r.label_id, r.count) for r in rows] == [(3, 42), (7, 12)]
    facet_query = db.execute.call_args.args[0]
    compiled = str(facet_query.compile(compile_kwargs={'literal_binds': True}))
    assert 'GROUP BY product_label.label' in compiled
    assert 'product_label.product IN' in compiled


# ── Merge preview (#111) ──────────────────────────────────────────────────────


def _merge_db(canonical: Product | None, duplicate: Product | None) -> AsyncMock:
    db = AsyncMock()
    db.get = AsyncMock(side_effect=[canonical, duplicate])
    return db


@pytest.mark.asyncio
async def test_preview_merge_reports_what_rides_on_the_duplicate() -> None:
    db = _merge_db(Product(product_id=1), Product(product_id=2))
    counts = [('sales_order_detail.product', 9), ('product_price.product', 3)]
    with patch(
        'app.services.product_service.find_blocking_references',
        new=AsyncMock(return_value=counts),
    ) as refs:
        assert await preview_merge(db, ProductMergeRequest(product_id=1, duplicate_id=2)) == counts

    assert refs.await_args.args[1].product_id == 2  # the duplicate, not the canonical
    assert 'exempt' not in refs.await_args.kwargs  # merge touches product_price too


@pytest.mark.asyncio
async def test_preview_merge_changes_nothing() -> None:
    db = _merge_db(Product(product_id=1), Product(product_id=2))
    with patch(
        'app.services.product_service.find_blocking_references', new=AsyncMock(return_value=[])
    ):
        await preview_merge(db, ProductMergeRequest(product_id=1, duplicate_id=2))

    db.commit.assert_not_awaited()
    db.delete.assert_not_awaited()
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_preview_merge_refuses_the_pairs_a_merge_refuses() -> None:
    same = ProductMergeRequest(product_id=1, duplicate_id=1)
    with pytest.raises(HTTPException) as exc:
        await preview_merge(_merge_db(Product(product_id=1), Product(product_id=1)), same)
    assert exc.value.status_code == 400

    pair = ProductMergeRequest(product_id=1, duplicate_id=2)
    with pytest.raises(HTTPException) as exc:
        await preview_merge(_merge_db(None, Product(product_id=2)), pair)
    assert (exc.value.status_code, exc.value.detail) == (404, 'Canonical product not found')

    with pytest.raises(HTTPException) as exc:
        await preview_merge(_merge_db(Product(product_id=1), None), pair)
    assert (exc.value.status_code, exc.value.detail) == (404, 'Duplicate product not found')


# ── Merge (#112) ──────────────────────────────────────────────────────────────


async def _merge_statements(db: AsyncMock | None = None) -> list[str]:
    """The SQL a merge of 2 into 1 issues, one statement per line."""
    db = db or _merge_db(Product(product_id=1), Product(product_id=2))
    with patch(
        'app.services.product_service.product_price_service.delete_for_product', new=AsyncMock()
    ):
        await merge_products(db, ProductMergeRequest(product_id=1, duplicate_id=2))
    return [str(call.args[0]) for call in db.execute.await_args_list]


def _remapped_tables(statements: list[str]) -> set[str]:
    """The table each `UPDATE` / `UPDATE IGNORE` moves onto the canonical."""
    words = [s.split() for s in statements if s.startswith('UPDATE')]
    return {w[2] if w[1] == 'IGNORE' else w[1] for w in words}


@pytest.mark.asyncio
async def test_merge_remaps_every_mapped_reference() -> None:
    """The set comes from the metadata, so the relations #112 reported missing are covered
    without a list to keep in step — and so is the next foreign key someone adds."""
    remapped = _remapped_tables(await _merge_statements())

    expected = {
        table.name for table, _ in referencing_columns(Product, exempt=frozenset({'product_price'}))
    }
    assert remapped == expected
    assert {  # the eleven left behind before #112
        'commission_product',
        'commissions_history',
        'customer_discount',
        'customer_refund_detail',
        'delivery_order_detail',
        'fiscal_document_detail',
        'lot_serial_rqmt',
        'purchase_request_detail',
        'sales_quote_detail',
        'service_order_detail',
        'supplier_return_detail',
    } <= remapped


@pytest.mark.asyncio
async def test_merge_remaps_the_referencing_column_not_a_column_named_product() -> None:
    """`service_order_detail` points at a product through `spare_part`."""
    statements = await _merge_statements()

    assert (
        'UPDATE service_order_detail SET spare_part = :canonical WHERE spare_part = :duplicate'
        in statements
    )


@pytest.mark.asyncio
async def test_merge_drops_the_duplicate_row_where_a_unique_key_forbids_two() -> None:
    """One row per product (or per customer/label and product), so the canonical's wins."""
    statements = await _merge_statements()

    for table in ('product_label', 'commission_product', 'customer_discount'):
        assert f'UPDATE IGNORE {table} SET' in ' '.join(statements)
        assert f'DELETE FROM {table} WHERE' in ' '.join(statements)


@pytest.mark.asyncio
async def test_merge_does_not_ignore_errors_on_transactional_tables() -> None:
    """`UPDATE IGNORE` plus a blanket `DELETE` would silently destroy order history if a
    statement failed for any reason, so it is confined to the unique-key tables."""
    statements = await _merge_statements()

    assert not [s for s in statements if s.startswith('DELETE FROM sales_order_detail')]
    assert not [s for s in statements if s.startswith('UPDATE IGNORE sales_order_detail')]


@pytest.mark.asyncio
async def test_merge_deletes_the_duplicates_prices_rather_than_moving_them() -> None:
    db = _merge_db(Product(product_id=1), Product(product_id=2))
    delete_prices = AsyncMock()
    with patch(
        'app.services.product_service.product_price_service.delete_for_product', new=delete_prices
    ):
        await merge_products(db, ProductMergeRequest(product_id=1, duplicate_id=2))

    assert delete_prices.await_args.args[1] == 2  # the duplicate's rows, not the canonical's
    assert not [
        s for s in (str(c.args[0]) for c in db.execute.await_args_list) if 'product_price' in s
    ]


@pytest.mark.asyncio
async def test_merge_deletes_the_duplicate_and_commits_once() -> None:
    db = _merge_db(Product(product_id=1), duplicate := Product(product_id=2))
    with patch(
        'app.services.product_service.product_price_service.delete_for_product', new=AsyncMock()
    ):
        await merge_products(db, ProductMergeRequest(product_id=1, duplicate_id=2))

    assert db.delete.await_args.args[0] is duplicate
    assert db.commit.await_count == 1


@pytest.mark.asyncio
async def test_preview_counts_exactly_what_a_merge_touches() -> None:
    """The invariant behind both features: the preview's categories are the merge's remaps
    plus the prices it deletes. If they can drift, the preview is a lie."""
    touched = _remapped_tables(await _merge_statements())
    counted = {table.name for table, _ in referencing_columns(Product)}

    assert counted == touched | {'product_price'}
