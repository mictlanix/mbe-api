"""Spec 037, T012: the catalog defines every measure and dimension once (FR-001, FR-004–FR-006)."""

import pytest

from app.schemas.analytics import DimensionKey, MeasureKey
from app.services.analytics import catalog
from app.services.analytics.catalog import Fact

PAYMENT_MEASURES = {
    MeasureKey.COLLECTED,
    MeasureKey.REFUNDS_PAID_OUT,
    MeasureKey.STORE_CREDIT_ISSUED,
    MeasureKey.PAYMENTS,
}
SALE_MEASURES = set(MeasureKey) - PAYMENT_MEASURES


def test_every_key_has_an_entry() -> None:
    assert set(catalog.MEASURES) == set(MeasureKey)
    assert set(catalog.DIMENSIONS) == set(DimensionKey)
    for measure in catalog.MEASURES.values():
        assert measure.label and measure.unit in {'money', 'quantity', 'count', 'ratio'}
        assert measure.facts
    for dimension in catalog.DIMENSIONS.values():
        assert dimension.label
        assert set(dimension.facts) == set(Fact)


def test_the_sensitive_measures_are_exactly_cost_and_margin() -> None:
    assert {m.key for m in catalog.MEASURES.values() if m.sensitive} == {
        MeasureKey.COSTED_NET_SALES,
        MeasureKey.COST_COVERAGE,
        MeasureKey.COST_OF_SALES,
        MeasureKey.GROSS_MARGIN,
        MeasureKey.MARGIN_PCT,
    }


def test_the_non_additive_measures_are_the_ratios() -> None:
    assert {m.key for m in catalog.MEASURES.values() if not m.additive} == {
        MeasureKey.COST_COVERAGE,
        MeasureKey.MARGIN_PCT,
        MeasureKey.AVERAGE_TICKET,
    }


@pytest.mark.parametrize(
    'dimension',
    [
        DimensionKey.PRODUCT,
        DimensionKey.BRAND,
        DimensionKey.MODEL,
        DimensionKey.LABEL,
        DimensionKey.POINT_OF_SALE,
        DimensionKey.PAYMENT_TERMS,
    ],
)
@pytest.mark.parametrize('measure', sorted(PAYMENT_MEASURES))
def test_payment_measures_refuse_order_and_product_dimensions(
    measure: MeasureKey, dimension: DimensionKey
) -> None:
    assert not catalog.applicable(measure, dimension)


@pytest.mark.parametrize('dimension', [DimensionKey.CASH_SESSION, DimensionKey.PAYMENT_METHOD])
@pytest.mark.parametrize('measure', sorted(SALE_MEASURES))
def test_sale_measures_refuse_payment_dimensions(
    measure: MeasureKey, dimension: DimensionKey
) -> None:
    assert not catalog.applicable(measure, dimension)


@pytest.mark.parametrize(
    'dimension',
    [
        DimensionKey.DATE,
        DimensionKey.WEEKDAY,
        DimensionKey.HOUR,
        DimensionKey.FACILITY,
        DimensionKey.SALESPERSON,
        DimensionKey.CUSTOMER,
        DimensionKey.CUSTOMER_TYPE,
    ],
)
def test_shared_dimensions_apply_to_both_families(dimension: DimensionKey) -> None:
    assert catalog.applicable(MeasureKey.NET_SALES, dimension)
    assert catalog.applicable(MeasureKey.COLLECTED, dimension)


def test_every_pair_is_decided() -> None:
    """No pair is left to chance: each is either supported or refused, by the fact table."""
    for measure in MeasureKey:
        for dimension in DimensionKey:
            assert isinstance(catalog.applicable(measure, dimension), bool)


def test_credit_notes_have_no_product_but_still_group_by_it() -> None:
    """Credit notes carry no product; grouped by product they fall under "none" (FR-006)."""
    assert catalog.DIMENSIONS[DimensionKey.PRODUCT].facts[Fact.CREDIT_NOTE] is catalog.NONE
    assert catalog.applicable(MeasureKey.NET_SALES, DimensionKey.PRODUCT)


def test_only_the_cost_privilege_shows_sensitive_measures() -> None:
    hidden = catalog.visible_measures(cost=False, sales=True, payments=True)
    shown = catalog.visible_measures(cost=True, sales=True, payments=True)

    assert not any(m.sensitive for m in hidden)
    assert {m.key for m in shown} == set(MeasureKey)


def test_each_family_shows_only_its_own_measures() -> None:
    sales_only = {m.key for m in catalog.visible_measures(cost=True, sales=True, payments=False)}
    payments_only = {m.key for m in catalog.visible_measures(cost=True, sales=False, payments=True)}

    assert sales_only == SALE_MEASURES
    assert payments_only == PAYMENT_MEASURES


def test_facts_for_derived_measures_include_their_parts() -> None:
    assert catalog.facts_for([MeasureKey.NET_SALES]) == {
        Fact.SALE_LINE,
        Fact.REFUND_LINE,
        Fact.CREDIT_NOTE,
    }
    assert catalog.facts_for([MeasureKey.COLLECTED, MeasureKey.ORDERS]) == {
        Fact.PAYMENT,
        Fact.SALE_LINE,
    }
