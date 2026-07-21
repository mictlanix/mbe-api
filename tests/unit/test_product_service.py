from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from app.models.product import Product
from app.services.product_service import _apply_product_filters, get_label_facets


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
