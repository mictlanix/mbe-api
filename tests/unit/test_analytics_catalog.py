"""Spec 037, T012: the catalog defines every measure and dimension once (FR-001, FR-004–FR-006).

Spec 038, T004: the operations families, their measures and dimensions, kinds and definitions.
"""

import pytest

from app.enums import SystemObject
from app.schemas.analytics import DimensionKey, MeasureKey, MeasureKind
from app.services.analytics import catalog
from app.services.analytics.catalog import Fact, Family

PAYMENT_MEASURES = {
    MeasureKey.COLLECTED,
    MeasureKey.REFUNDS_PAID_OUT,
    MeasureKey.STORE_CREDIT_ISSUED,
    MeasureKey.PAYMENTS,
}
SPEC_037_MEASURES = set(list(MeasureKey)[: list(MeasureKey).index(MeasureKey.PAYMENTS) + 1])
SALE_MEASURES = SPEC_037_MEASURES - PAYMENT_MEASURES


def test_every_key_has_an_entry() -> None:
    assert set(catalog.MEASURES) == set(MeasureKey)
    assert set(catalog.DIMENSIONS) == set(DimensionKey)
    for measure in catalog.MEASURES.values():
        assert measure.label and measure.unit in {'money', 'quantity', 'count', 'ratio', 'days'}
        assert measure.facts
        assert measure.family in catalog.MEASURE_FAMILIES
        assert measure.kind in MeasureKind
        assert measure.definition
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
        # Spec 038: value at current cost, and what is derived from it.
        MeasureKey.STOCK_VALUE,
        MeasureKey.STOCK_COST_COVERAGE,
        MeasureKey.DEAD_STOCK_VALUE,
        MeasureKey.SLOW_STOCK_VALUE,
        MeasureKey.TURNOVER,
        MeasureKey.DAYS_OF_INVENTORY,
        MeasureKey.PURCHASE_SALE_SPREAD,
        MeasureKey.PURCHASE_SALE_SPREAD_PCT,
    }


def test_the_non_additive_measures_are_the_ratios() -> None:
    assert {m.key for m in catalog.MEASURES.values() if not m.additive} == {
        MeasureKey.COST_COVERAGE,
        MeasureKey.MARGIN_PCT,
        MeasureKey.AVERAGE_TICKET,
        # Spec 038: ratios, days and averages, recomputed from their parts.
        MeasureKey.STOCK_COST_COVERAGE,
        MeasureKey.DAYS_OF_COVER,
        MeasureKey.TURNOVER,
        MeasureKey.DAYS_OF_INVENTORY,
        MeasureKey.DSO,
        MeasureKey.CEI,
        MeasureKey.CREDIT_UTILIZATION,
        MeasureKey.INVOICE_COVERAGE,
        MeasureKey.INVOICING_LAG,
        MeasureKey.AVERAGE_PURCHASE_PRICE,
        MeasureKey.AVERAGE_SALE_PRICE,
        MeasureKey.PURCHASE_SALE_SPREAD,
        MeasureKey.PURCHASE_SALE_SPREAD_PCT,
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
    hidden = catalog.visible_measures(set(Family) - {Family.COST})
    shown = catalog.visible_measures(set(Family))

    assert not any(m.sensitive for m in hidden)
    assert {m.key for m in shown} == set(MeasureKey)


def test_each_family_shows_only_its_own_measures() -> None:
    sales_only = {m.key for m in catalog.visible_measures({Family.SALES, Family.COST})}
    payments_only = {m.key for m in catalog.visible_measures({Family.PAYMENTS, Family.COST})}

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


# ── Spec 038, T004 ────────────────────────────────────────────────────────────

#: data-model.md "Catalog additions": each new measure's family and kind.
NEW_MEASURES: dict[Family, dict[MeasureKind, set[MeasureKey]]] = {
    Family.INVENTORY: {
        MeasureKind.BALANCE: {
            MeasureKey.ON_HAND, MeasureKey.RESERVED, MeasureKey.AVAILABLE, MeasureKey.IN_TRANSIT,
            MeasureKey.STOCK_VALUE, MeasureKey.STOCK_COST_COVERAGE, MeasureKey.UNCOSTED_QUANTITY,
            MeasureKey.SALES_VELOCITY, MeasureKey.DAYS_OF_COVER, MeasureKey.STOCK_OUT_PAIRS,
            MeasureKey.BELOW_REORDER_PAIRS, MeasureKey.SUGGESTED_QUANTITY,
            MeasureKey.DEAD_STOCK_QUANTITY, MeasureKey.DEAD_STOCK_VALUE,
            MeasureKey.SLOW_STOCK_QUANTITY, MeasureKey.SLOW_STOCK_VALUE,
        },
        MeasureKind.FLOW: {
            MeasureKey.UNITS_IN, MeasureKey.UNITS_OUT, MeasureKey.MOVEMENT_DOCUMENTS,
            MeasureKey.TURNOVER, MeasureKey.DAYS_OF_INVENTORY,
        },
    },
    Family.RECEIVABLES: {
        MeasureKind.BALANCE: {
            MeasureKey.RECEIVABLE, MeasureKey.RECEIVABLE_CURRENT, MeasureKey.RECEIVABLE_OVERDUE,
            MeasureKey.OPEN_ORDERS, MeasureKey.CREDIT_LIMIT, MeasureKey.CREDIT_UTILIZATION,
        },
        # DSO and CEI are ratios of a balance and a flow over the period: a period's figure.
        MeasureKind.FLOW: {
            MeasureKey.CREDIT_SALES, MeasureKey.COLLECTED_ON_CREDIT, MeasureKey.DUE_IN_PERIOD,
            MeasureKey.DUE_COLLECTED, MeasureKey.DSO, MeasureKey.CEI,
        },
    },
    Family.PAYABLES: {
        MeasureKind.BALANCE: {
            MeasureKey.PAYABLE, MeasureKey.PAYABLE_CURRENT, MeasureKey.PAYABLE_OVERDUE,
            MeasureKey.OPEN_PURCHASE_ORDERS,
        },
        MeasureKind.FLOW: set(),
    },
    Family.COMMISSIONS: {
        MeasureKind.BALANCE: set(),
        MeasureKind.FLOW: {
            MeasureKey.COMMISSION, MeasureKey.COMMISSION_BASE, MeasureKey.COMMISSION_LINES,
        },
    },
    Family.FISCAL: {
        MeasureKind.BALANCE: set(),
        MeasureKind.FLOW: {
            MeasureKey.INVOICED, MeasureKey.INVOICES, MeasureKey.STANDALONE_INVOICED,
            MeasureKey.PAYMENT_RECEIPTS, MeasureKey.OUTPUT_VAT, MeasureKey.INVOICED_SALES,
            MeasureKey.UNINVOICED_SALES, MeasureKey.INVOICE_COVERAGE, MeasureKey.INVOICING_LAG,
        },
    },
    Family.PURCHASES: {
        MeasureKind.BALANCE: set(),
        MeasureKind.FLOW: {
            MeasureKey.PURCHASES, MeasureKey.PURCHASE_QUANTITY, MeasureKey.RECEIVED_QUANTITY,
            MeasureKey.PURCHASE_ORDERS, MeasureKey.AVERAGE_PURCHASE_PRICE,
            MeasureKey.AVERAGE_SALE_PRICE, MeasureKey.PURCHASE_SALE_SPREAD,
            MeasureKey.PURCHASE_SALE_SPREAD_PCT,
        },
    },
}  # fmt: skip


def test_every_new_measure_has_its_family_and_kind() -> None:
    for family, kinds in NEW_MEASURES.items():
        for kind, keys in kinds.items():
            for key in keys:
                assert (catalog.MEASURES[key].family, catalog.MEASURES[key].kind) == (family, kind)
    listed = {key for kinds in NEW_MEASURES.values() for keys in kinds.values() for key in keys}
    assert listed == set(MeasureKey) - SPEC_037_MEASURES


def test_spec_037_measures_are_flows_of_sales_or_payments() -> None:
    for key in SPEC_037_MEASURES:
        family = Family.PAYMENTS if key in PAYMENT_MEASURES else Family.SALES
        assert (catalog.MEASURES[key].family, catalog.MEASURES[key].kind) == (
            family,
            MeasureKind.FLOW,
        )


def test_the_families_open_on_exactly_research_r17s_privileges() -> None:
    assert catalog.FAMILY_PRIVILEGES[Family.INVENTORY] == (
        SystemObject.WAREHOUSE_STOCK_REPORT,
        SystemObject.KARDEX,
        SystemObject.STORE_MOVEMENTS_SUMMARY,
    )
    assert catalog.FAMILY_PRIVILEGES[Family.RECEIVABLES] == (
        SystemObject.ACCOUNTS_RECEIVABLE,
        SystemObject.CUSTOMER_DEBT_REPORT,
    )
    assert catalog.FAMILY_PRIVILEGES[Family.PAYABLES] == (SystemObject.ACCOUNTS_PAYABLE,)
    assert catalog.FAMILY_PRIVILEGES[Family.COMMISSIONS] == (
        SystemObject.COMMISSIONS_BY_SALES_PERSON,
    )
    assert catalog.FAMILY_PRIVILEGES[Family.FISCAL] == (
        SystemObject.FISCAL_DOCUMENTS,
        SystemObject.FISCAL_DOCUMENTS_REPORT,
    )
    assert catalog.FAMILY_PRIVILEGES[Family.PURCHASES] == (
        SystemObject.PURCHASES_ORDERS,
        SystemObject.PRODUCTS_BY_SUPPLIER_REPORT,
    )
    assert [int(o) for o in catalog.FAMILY_PRIVILEGES[Family.INVENTORY]] == [67, 32, 99]
    assert set(catalog.MEASURE_FAMILIES) == set(Family) - {Family.COST, Family.BULK}


#: data-model.md "New dimensions": the facts each one applies to.
NEW_DIMENSIONS = {
    DimensionKey.WAREHOUSE: {Fact.STOCK, Fact.STOCK_TODAY, Fact.MOVEMENT, Fact.PURCHASE_LINE},
    DimensionKey.MOVEMENT_TYPE: {Fact.MOVEMENT},
    DimensionKey.SUPPLIER: {
        Fact.STOCK, Fact.STOCK_TODAY, Fact.MOVEMENT, Fact.PURCHASE_LINE, Fact.PAYABLE,
    },
    DimensionKey.AGING_BUCKET: {Fact.RECEIVABLE, Fact.PAYABLE},
    DimensionKey.DUE_BUCKET: {Fact.RECEIVABLE, Fact.PAYABLE},
    DimensionKey.SALES_AGENT: {Fact.RECEIVABLE, Fact.CREDIT_FLOW},
    DimensionKey.COMMISSION_PARTICIPATION: {Fact.COMMISSION},
    DimensionKey.COMMISSION_STATUS: {Fact.COMMISSION},
    DimensionKey.ISSUER: {Fact.INVOICE_LINE},
    DimensionKey.DOCUMENT_STATUS: {Fact.INVOICE_LINE},
    DimensionKey.DOCUMENT_TYPE: {Fact.INVOICE_LINE},
}  # fmt: skip


@pytest.mark.parametrize('dimension', sorted(NEW_DIMENSIONS))
def test_each_new_dimension_applies_only_to_its_facts(dimension: DimensionKey) -> None:
    facts = catalog.DIMENSIONS[dimension].facts

    assert {f for f, a in facts.items() if a is catalog.COLUMN} == NEW_DIMENSIONS[dimension]
    assert {f for f, a in facts.items() if a is not catalog.COLUMN} == {
        f for f in Fact if f not in NEW_DIMENSIONS[dimension]
    }
    assert all(facts[f] is catalog.REFUSED for f in Fact if f not in NEW_DIMENSIONS[dimension])


def test_payment_terms_now_also_applies_to_receivables() -> None:
    facts = catalog.DIMENSIONS[DimensionKey.PAYMENT_TERMS].facts

    assert facts[Fact.RECEIVABLE] is facts[Fact.CREDIT_FLOW] is catalog.COLUMN
    assert catalog.applicable(MeasureKey.RECEIVABLE, DimensionKey.PAYMENT_TERMS)


def test_stock_groups_by_warehouse_and_supplier_but_not_by_customer() -> None:
    assert catalog.applicable(MeasureKey.ON_HAND, DimensionKey.WAREHOUSE)
    assert catalog.applicable(MeasureKey.AVAILABLE, DimensionKey.SUPPLIER)
    assert not catalog.applicable(MeasureKey.ON_HAND, DimensionKey.CUSTOMER)
    assert not catalog.applicable(MeasureKey.NET_SALES, DimensionKey.WAREHOUSE)


def test_the_balance_facts() -> None:
    assert catalog.BALANCE_FACTS == {Fact.STOCK, Fact.STOCK_TODAY, Fact.RECEIVABLE, Fact.PAYABLE}


def test_every_fact_names_its_facility_scope() -> None:
    assert set(catalog.SCOPE_COLUMNS) == set(Fact)
    assert catalog.SCOPE_COLUMNS[Fact.STOCK] == 'warehouse.facility'


@pytest.mark.parametrize(
    'key',
    [
        MeasureKey.STOCK_VALUE,
        MeasureKey.DEAD_STOCK_VALUE,
        MeasureKey.SLOW_STOCK_VALUE,
        MeasureKey.TURNOVER,
        MeasureKey.DAYS_OF_INVENTORY,
    ],
)
def test_value_turnover_and_days_of_inventory_say_at_current_cost(key: MeasureKey) -> None:
    assert 'at current cost' in catalog.MEASURES[key].definition.lower()


def test_every_receivable_balance_states_the_2025_caveat() -> None:
    """FR-121: legacy orders closed by the paid flag understate 2025 month-ends."""
    reads_balances = [
        m for m in catalog.MEASURES.values()
        if Fact.RECEIVABLE in m.facts and m.key is not MeasureKey.CREDIT_LIMIT
    ]  # fmt: skip

    assert len(reads_balances) == 7
    for measure in reads_balances:
        assert '$2.79M' in measure.definition and '2026-01' in measure.definition


def test_turnover_and_cei_read_the_opening_balance_too() -> None:
    assert {m.key for m in catalog.MEASURES.values() if m.opening} == {
        MeasureKey.TURNOVER,
        MeasureKey.DAYS_OF_INVENTORY,
        MeasureKey.CEI,
    }
