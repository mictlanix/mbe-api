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
        missing_price_list=None,
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
    """The SQL a merge of 2 into 1 issues, one statement per line.

    Nothing is stubbed: every relation goes through the one loop, so the coverage below is what
    the merge does, not what it was mocked into doing.
    """
    db = db or _merge_db(Product(product_id=1), Product(product_id=2))
    await merge_products(db, ProductMergeRequest(product_id=1, duplicate_id=2))
    return [str(call.args[0]) for call in db.execute.await_args_list]


def _remapped_tables(statements: list[str]) -> set[str]:
    """The table each `UPDATE` moves onto the canonical."""
    return {s.split()[1] for s in statements if s.startswith('UPDATE')}


def _deleted_tables(statements: list[str]) -> set[str]:
    """The table each `DELETE` clears of the duplicate's rows."""
    return {s.split()[2] for s in statements if s.startswith('DELETE FROM')}


@pytest.mark.asyncio
async def test_merge_handles_every_mapped_reference() -> None:
    """The set comes from the metadata, so the relations #112 reported missing are covered
    without a list to keep in step — and so is the next foreign key someone adds."""
    statements = await _merge_statements()

    expected = {table.name for table, _ in referencing_columns(Product)}
    assert _remapped_tables(statements) | _deleted_tables(statements) == expected
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
    } <= _remapped_tables(statements) | _deleted_tables(statements)


@pytest.mark.asyncio
async def test_merge_remaps_history_and_nothing_else() -> None:
    """Every relation is either history that follows the product or configuration that dies
    with the duplicate — never both, and the split is exhaustive."""
    statements = await _merge_statements()

    assert _remapped_tables(statements) & _deleted_tables(statements) == set()
    assert _deleted_tables(statements) == {
        'product_label',
        'commission_product',
        'customer_discount',
        'product_price',
    }


@pytest.mark.asyncio
async def test_merge_remaps_the_referencing_column_not_a_column_named_product() -> None:
    """`service_order_detail` points at a product through `spare_part`."""
    statements = await _merge_statements()

    assert (
        'UPDATE service_order_detail SET spare_part = :canonical WHERE spare_part = :duplicate'
        in statements
    )


@pytest.mark.asyncio
async def test_merge_discards_the_duplicates_configuration_without_moving_any_of_it() -> None:
    """The canonical is the row being kept, so its labels, commission and discounts stand and
    the duplicate's are dropped whole — not partially moved depending on what collided."""
    statements = await _merge_statements()

    for table in ('product_label', 'commission_product', 'customer_discount'):
        assert f'DELETE FROM {table} WHERE' in ' '.join(statements)
        assert not [s for s in statements if s.startswith(f'UPDATE {table} ')]


@pytest.mark.asyncio
async def test_merge_never_ignores_a_failed_statement() -> None:
    """Nothing is moved past a unique key any more, so no statement suppresses its errors —
    `UPDATE IGNORE` would hide a failure that should roll the merge back."""
    statements = await _merge_statements()

    assert not [s for s in statements if s.startswith('UPDATE IGNORE')]


@pytest.mark.asyncio
async def test_merge_does_not_delete_transactional_history() -> None:
    """Only configuration is dropped; a `DELETE` against order history would destroy it."""
    statements = await _merge_statements()

    for table in ('sales_order_detail', 'fiscal_document_detail', 'commissions_history'):
        assert not [s for s in statements if s.startswith(f'DELETE FROM {table} ')]
        assert f'UPDATE {table} SET' in ' '.join(statements)


@pytest.mark.asyncio
async def test_merge_deletes_the_duplicates_prices_rather_than_moving_them() -> None:
    """Prices are discarded by the same loop as the rest of the configuration, not by a
    separate call the coverage above would not see."""
    statements = await _merge_statements()

    assert 'DELETE FROM product_price WHERE product = :duplicate' in statements
    assert 'product_price' not in _remapped_tables(statements)


@pytest.mark.asyncio
async def test_merge_deletes_the_duplicate_and_commits_once() -> None:
    db = _merge_db(Product(product_id=1), duplicate := Product(product_id=2))
    await merge_products(db, ProductMergeRequest(product_id=1, duplicate_id=2))

    assert db.delete.await_args.args[0] is duplicate
    assert db.commit.await_count == 1


@pytest.mark.asyncio
async def test_preview_counts_exactly_what_a_merge_touches() -> None:
    """The invariant behind both features: the preview's categories are what the merge remaps
    plus what it deletes. If they can drift, the preview is a lie."""
    statements = await _merge_statements()
    touched = _remapped_tables(statements) | _deleted_tables(statements)
    counted = {table.name for table, _ in referencing_columns(Product)}

    assert counted == touched


# ── Missing-price filter (#184) ──────────────────────────────────────────────


def test_missing_price_list_renders_a_correlated_not_exists() -> None:
    """`NOT EXISTS`, not `NOT IN`: the form that stays correct and that the index can serve."""
    query = _apply_product_filters(select(Product), **{**_no_filters(), 'missing_price_list': 2})
    compiled = str(query.compile(compile_kwargs={'literal_binds': True}))

    assert 'NOT (EXISTS' in compiled
    assert 'product_price.product = product.product_id' in compiled
    assert 'product_price.list = 2' in compiled


def test_missing_price_list_compiles_against_mariadb() -> None:
    """The integration tests run on SQLite; this is the clause the deployment will actually run."""
    from sqlalchemy.dialects import mysql

    query = _apply_product_filters(select(Product), **{**_no_filters(), 'missing_price_list': 2})
    assert 'NOT (EXISTS' in str(query.compile(dialect=mysql.dialect()))


def test_missing_price_list_composes_with_the_other_filters() -> None:
    """ "Unpriced *and* salable" is what #184 asked to be expressible in one query."""
    query = _apply_product_filters(
        select(Product), **{**_no_filters(), 'missing_price_list': 2, 'salable': True}
    )
    compiled = str(query.compile(compile_kwargs={'literal_binds': True}))

    assert 'NOT (EXISTS' in compiled
    assert 'product.salable' in compiled


def test_omitting_missing_price_list_adds_no_clause() -> None:
    """Zero is a real price list id in the deployment, so this cannot key off truthiness."""
    without = str(_apply_product_filters(select(Product), **_no_filters()).compile())
    assert 'EXISTS' not in without

    zero = _apply_product_filters(select(Product), **{**_no_filters(), 'missing_price_list': 0})
    assert 'NOT (EXISTS' in str(zero.compile(compile_kwargs={'literal_binds': True}))
