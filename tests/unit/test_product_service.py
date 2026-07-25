from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.product import Product
from app.schemas.product import ProductMergeRequest
from app.services.product_service import _apply_product_filters, get_label_facets, preview_merge


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
