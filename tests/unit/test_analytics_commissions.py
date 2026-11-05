"""Spec 038, T038: the commission fact groups spec 025's lines in Python (research R11).

The lines come from `commission_rules.month_lines`, the rule itself, for a closed March and an open
April; `read_period` is replaced by those months, so no database is read. Agent 17 belongs to
facility 1 and agent 33 to facility 2.
"""

from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.enums import CommissionMovement, CommissionScheme
from app.schemas.analytics import DimensionKey, Grain, MeasureKey, VatTreatment
from app.services import commission_rules as rules
from app.services.analytics import commissions, engine, facts

D = Decimal
VITALICIO, CANALIZACION, CAMPO = 1, 2, 3
LABEL = 1
AGENT_A, AGENT_B = 17, 33
SETTINGS = rules.Settings(
    agents={AGENT_A: CommissionScheme.FULLY_PAID, AGENT_B: CommissionScheme.FULLY_PAID},
    rates={
        (agent, LABEL, participation): share
        for agent in (AGENT_A, AGENT_B)
        for participation, share in ((VITALICIO, D(1)), (CANALIZACION, D('0.5')), (CAMPO, D('0.5')))
    },
    participations={VITALICIO: 'CLIENTE VITALICIO', CANALIZACION: 'CANALIZACIÓN', CAMPO: 'CAMPO'},
)


def _candidate(**overrides: object) -> rules.CandidateLine:
    values = dict(
        sales_order=1, sales_order_detail=10, order_date=datetime(2026, 3, 2), customer=5,
        customer_name='Cliente', osp=AGENT_A, csp=AGENT_A, product=7, product_name='Concreto',
        price=D('10000.00'), quantity=D('1'), balance_zeroed_time=datetime(2026, 3, 9),
        is_paid=True, label=LABEL, label_name='CONCRETOS', label_rate=D('0.01'),
    )  # fmt: skip
    values.update(overrides)
    return rules.CandidateLine(**values)


def _month_lines(month: int, candidates: list, **kw: object) -> list[rules.ComputedLine]:
    return rules.month_lines(
        month_start=datetime(2026, month, 1),
        next_month=datetime(2026, month + 1, 1),
        chain_start=None,
        candidates=candidates,
        money={},
        settings=SETTINGS,
        **{'refunds': [], 'stored': [], **kw},
    )


MARCH = _month_lines(
    3,
    [
        _candidate(),
        # Agent 33 sold to agent 17's customer: the line splits between them.
        _candidate(
            sales_order=2,
            sales_order_detail=20,
            customer=6,
            customer_name='Otro',
            osp=AGENT_B,
            product=8,
            price=D('5000.00'),
            quantity=D('2'),
        ),  # fmt: skip
    ],
)
# Half of line 10 comes back in April: the refund belongs to April, and is negative.
APRIL = _month_lines(
    4,
    [_candidate()],
    refunds=[
        rules.RefundLine(
            customer_refund_detail=900,
            sales_order_detail=10,
            date=datetime(2026, 4, 5),
            price=D('-10000.00'),
            quantity=D('0.5'),
        )  # fmt: skip
    ],
    stored=[rules.StoredLine.from_computed(line) for line in MARCH],
)
VIEWS = {
    (2026, 3): SimpleNamespace(final=True, lines=MARCH),
    (2026, 4): SimpleNamespace(final=False, lines=APRIL),
}
ALL = [(date(2026, 3, 1), 'closed', line) for line in MARCH] + [
    (date(2026, 4, 1), 'provisional', line) for line in APRIL
]


@pytest.fixture(autouse=True)
def read_period() -> Iterator[AsyncMock]:
    async def _read(db: object, year: int, month: int, **kw: object) -> SimpleNamespace:
        return VIEWS.get((year, month), SimpleNamespace(final=False, lines=[]))

    reader = AsyncMock(side_effect=_read)
    places = AsyncMock(return_value={AGENT_A: 1, AGENT_B: 2})
    with (
        patch.object(commissions.commission_service, 'read_period', reader),
        patch.object(commissions, 'agent_facilities', places),
    ):
        yield reader


def _slice(*dimensions: DimensionKey, **kw: object) -> facts.Slice:
    period = {'start': date(2026, 3, 1), 'end': date(2026, 4, 30), 'grain': Grain.MONTH}
    return facts.Slice(dimensions=tuple(dimensions), **{**period, **kw})


def _cents(value: Decimal) -> int:
    return int(value * 100)


def test_the_fixture_has_a_sale_split_and_a_refund() -> None:
    assert {(x.salesperson, x.participation, x.commission) for x in MARCH} == {
        (AGENT_A, VITALICIO, D('100.00')),
        (AGENT_B, CANALIZACION, D('50.00')),
        (AGENT_A, CAMPO, D('50.00')),
    }
    assert [(x.movement, x.commission) for x in APRIL] == [(CommissionMovement.REFUND, D('-50.00'))]


async def test_grouped_rows_equal_the_sum_of_their_lines() -> None:
    dimensions = (
        DimensionKey.SALESPERSON, DimensionKey.LABEL, DimensionKey.COMMISSION_PARTICIPATION,
        DimensionKey.CUSTOMER, DimensionKey.DATE,
    )  # fmt: skip

    rows = await commissions.fact(None, _slice(*dimensions))

    expected: dict[tuple, dict[str, int]] = {}
    for month, _, x in ALL:
        key = (x.salesperson, x.label, x.participation, x.customer, month)
        sums = expected.setdefault(key, {'commission_c': 0, 'base_c': 0, 'lines': 0})
        sums['commission_c'] += _cents(x.commission)
        sums['base_c'] += _cents(x.price * x.quantity)
        sums['lines'] += 1
    found = {
        tuple(row[f'k{i}'] for i in range(5)): {
            name: row[name] for name in ('commission_c', 'base_c', 'lines')
        }
        for row in rows
    }
    assert found == expected
    assert sum(r['commission_c'] for r in rows) == 15000


async def test_the_base_is_price_times_quantity() -> None:
    rows = await commissions.fact(None, _slice(DimensionKey.SALESPERSON))

    by_agent = {row['k0']: row for row in rows}
    # Agent 17: line 10 (10000 × 1), its half of line 20 (5000 × 2), and the refund (-10000 × 0.5)
    assert by_agent[AGENT_A]['base_c'] == 1000000 + 1000000 - 500000
    assert by_agent[AGENT_B]['base_c'] == 1000000


async def test_a_refund_is_negative_in_its_own_month() -> None:
    rows = await commissions.fact(
        None, _slice(DimensionKey.DATE, filters={DimensionKey.SALESPERSON: (AGENT_A,)})
    )

    by_month = {row['k0']: row for row in rows}
    assert by_month[date(2026, 3, 1)]['commission_c'] == 15000
    assert by_month[date(2026, 4, 1)]['commission_c'] == -5000
    assert by_month[date(2026, 4, 1)]['base_c'] == -500000


async def test_status_labels_and_names_come_from_the_lines() -> None:
    rows = await commissions.fact(
        None,
        _slice(
            DimensionKey.COMMISSION_STATUS, DimensionKey.LABEL,
            DimensionKey.COMMISSION_PARTICIPATION,
        ),
    )  # fmt: skip

    found = {(r['k0'], r['k2']): (r['l1'], r['l2'], r['lines']) for r in rows}
    assert found == {
        ('closed', VITALICIO): ('CONCRETOS', 'CLIENTE VITALICIO', 1),
        ('closed', CANALIZACION): ('CONCRETOS', 'CANALIZACIÓN', 1),
        ('closed', CAMPO): ('CONCRETOS', 'CAMPO', 1),
        ('provisional', VITALICIO): ('CONCRETOS', 'CLIENTE VITALICIO', 1),
    }


async def test_filters_and_facility_scope_select_lines() -> None:
    closed = await commissions.fact(
        None, _slice(filters={DimensionKey.COMMISSION_STATUS: ('closed',)})
    )
    scoped = await commissions.fact(None, _slice(DimensionKey.SALESPERSON, facility=2))

    assert [row['commission_c'] for row in closed] == [20000]
    assert [(row['k0'], row['commission_c']) for row in scoped] == [(AGENT_B, 5000)]


async def test_each_month_of_the_period_is_read_once(read_period: AsyncMock) -> None:
    await commissions.fact(None, _slice(start=date(2026, 3, 15), end=date(2026, 5, 2)))

    assert [call.args[1:] for call in read_period.await_args_list] == [
        (2026, 3), (2026, 4), (2026, 5),
    ]  # fmt: skip


def test_measures_derive_from_the_sums() -> None:
    values = engine.derive(
        {'commission.commission_c': 15000, 'commission.base_c': 1500000, 'commission.lines': 4},
        VatTreatment.EXCLUDED,
    )

    assert values[MeasureKey.COMMISSION] == D('150.00')
    assert values[MeasureKey.COMMISSION_BASE] == D('15000.00')
    assert values[MeasureKey.COMMISSION_LINES] == D(4)
