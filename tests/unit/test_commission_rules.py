"""The commission rules: participation, both schemes, refunds, the cap and the key freeze.

`month_lines` is pure: facts in, lines out. Every month's output is fed back as stored lines the
way a close would store it, which is how these tests walk an order across several months.
"""

import random
from dataclasses import replace
from datetime import datetime
from decimal import Decimal

import pytest

from app.enums import CommissionMovement, CommissionScheme
from app.services import commission_rules as rules

D = Decimal
VITALICIO, CANALIZACION, CAMPO = 1, 2, 3
LABEL = 1
AGENT_A, AGENT_B, NON_AGENT = 17, 33, 99


def _month(year: int, month: int) -> tuple[datetime, datetime]:
    start = datetime(year, month, 1)
    return start, datetime(year + (month == 12), month % 12 + 1, 1)


def _settings(**schemes: int) -> rules.Settings:
    agents = {AGENT_A: CommissionScheme.FULLY_PAID, AGENT_B: CommissionScheme.FULLY_PAID}
    agents.update({int(k[1:]): v for k, v in schemes.items()})
    rates = {
        (agent, LABEL, participation): share
        for agent in (AGENT_A, AGENT_B)
        for participation, share in ((VITALICIO, D(1)), (CANALIZACION, D('0.5')), (CAMPO, D('0.5')))
    }
    names = {
        VITALICIO: 'CLIENTE VITALICIO',
        CANALIZACION: 'CANALIZACIÓN',
        CAMPO: 'ATENCIÓN EN CAMPO',
    }
    return rules.Settings(agents=agents, rates=rates, participations=names)


def _line(**overrides: object) -> rules.CandidateLine:
    values = dict(
        sales_order=1,
        sales_order_detail=10,
        order_date=datetime(2026, 3, 2),
        customer=5,
        customer_name='Cliente',
        osp=AGENT_A,
        csp=AGENT_A,
        product=7,
        product_name='Concreto',
        price=D('10000.00'),
        quantity=D('1'),
        balance_zeroed_time=None,
        is_paid=False,
        label=LABEL,
        label_name='CONCRETOS',
        label_rate=D('0.01'),
    )
    values.update(overrides)
    return rules.CandidateLine(**values)


def _money(
    applied: str = '0', refunded: str = '0', total: str = '10000', last=None
) -> rules.OrderMoney:
    return rules.OrderMoney(
        applied=D(applied), refunded=D(refunded), order_total=D(total), last_activity=last
    )


def _run(
    month: tuple[int, int],
    lines: list[rules.CandidateLine],
    *,
    money: dict | None = None,
    refunds: list[rules.RefundLine] = (),
    stored: list[rules.StoredLine] = (),
    settings: rules.Settings | None = None,
    chain_start: datetime | None = None,
) -> list[rules.ComputedLine]:
    start, nxt = _month(*month)
    return rules.month_lines(
        month_start=start,
        next_month=nxt,
        chain_start=chain_start,
        candidates=list(lines),
        money=money or {1: _money()},
        refunds=list(refunds),
        stored=list(stored),
        settings=settings or _settings(),
    )


def _store(lines: list[rules.ComputedLine]) -> list[rules.StoredLine]:
    return [rules.StoredLine.from_computed(line) for line in lines]


def _sales(lines: list[rules.ComputedLine]) -> D:
    return sum((x.commission for x in lines if x.movement == CommissionMovement.SALE), D(0))


# ── Participation ─────────────────────────────────────────────────────────────


class TestParticipation:
    def test_one_agent_on_both_sides_is_lifetime_customer(self) -> None:
        paid = _line(is_paid=True, balance_zeroed_time=datetime(2026, 3, 9))

        [line] = _run((2026, 3), [paid])

        assert (line.salesperson, line.participation) == (AGENT_A, VITALICIO)
        assert line.rate == D('0.01')
        assert line.commission == D('100.00')
        assert line.paid_date == datetime(2026, 3, 9)

    def test_two_agents_split_canalizacion_and_campo(self) -> None:
        paid = _line(
            osp=AGENT_B, csp=AGENT_A, is_paid=True, balance_zeroed_time=datetime(2026, 3, 9)
        )

        lines = _run((2026, 3), [paid])

        assert {(x.salesperson, x.participation, x.commission) for x in lines} == {
            (AGENT_B, CANALIZACION, D('50.00')),
            (AGENT_A, CAMPO, D('50.00')),
        }

    def test_a_customer_without_a_salesperson_earns_nothing(self) -> None:
        """The current system's branches compare csp with osp; NULL matches neither (parity)."""
        paid = _line(csp=None, is_paid=True, balance_zeroed_time=datetime(2026, 3, 9))

        assert _run((2026, 3), [paid]) == []

    def test_a_non_agent_side_takes_no_share(self) -> None:
        paid = _line(
            osp=NON_AGENT, csp=AGENT_A, is_paid=True, balance_zeroed_time=datetime(2026, 3, 9)
        )

        [line] = _run((2026, 3), [paid])

        assert (line.salesperson, line.participation) == (AGENT_A, CAMPO)

    def test_an_unlabelled_product_lists_at_zero(self) -> None:
        paid = _line(
            label=None,
            label_name=None,
            label_rate=D(0),
            is_paid=True,
            balance_zeroed_time=datetime(2026, 3, 9),
        )

        [line] = _run((2026, 3), [paid])

        assert line.rate == 0 and line.commission == 0

    def test_a_missing_rate_row_gives_a_zero_share(self) -> None:
        settings = _settings()
        settings = replace(settings, rates={})
        paid = _line(is_paid=True, balance_zeroed_time=datetime(2026, 3, 9))

        [line] = _run((2026, 3), [paid], settings=settings)

        assert line.participation_rate == 0 and line.commission == 0


# ── Scheme 1 ──────────────────────────────────────────────────────────────────


class TestSchemeOne:
    def test_an_order_paid_in_another_month_earns_nothing_here(self) -> None:
        paid = _line(is_paid=True, balance_zeroed_time=datetime(2026, 2, 27))

        assert _run((2026, 3), [paid]) == []

    def test_a_refund_before_payment_is_deducted_once_in_the_paid_month(self) -> None:
        """The September fix: dated before the order was paid, charged only where it is paid."""
        paid = _line(is_paid=True, balance_zeroed_time=datetime(2026, 4, 5))
        refund = rules.RefundLine(70, 10, datetime(2026, 3, 20), D('-10000.00'), D('0.3'))

        march = _run((2026, 3), [paid], refunds=[refund], chain_start=datetime(2026, 3, 1))
        april = _run((2026, 4), [paid], refunds=[refund], stored=_store(march))

        assert march == []
        assert sorted(x.commission for x in april) == [D('-30.00'), D('100.00')]

    def test_a_refund_of_an_earlier_months_order_lands_in_its_own_month(self) -> None:
        paid = _line(is_paid=True, balance_zeroed_time=datetime(2026, 3, 5))
        refund = rules.RefundLine(70, 10, datetime(2026, 4, 2), D('-10000.00'), D('0.3'))

        march = _run((2026, 3), [paid])
        april = _run((2026, 4), [paid], refunds=[refund], stored=_store(march))

        assert [x.commission for x in april] == [D('-30.00')]

    def test_a_refund_already_stored_is_not_deducted_again(self) -> None:
        paid = _line(is_paid=True, balance_zeroed_time=datetime(2026, 3, 5))
        refund = rules.RefundLine(70, 10, datetime(2026, 3, 20), D('-10000.00'), D('0.3'))

        march = _run((2026, 3), [paid], refunds=[refund])
        april = _run((2026, 4), [paid], refunds=[refund], stored=_store(march))

        assert april == []

    def test_late_pickup_after_a_reversal_before_the_month_closed(self) -> None:
        """Stamped in March, unpaid when March closed, paid again in May (analysis C2)."""
        reversed_ = _line(is_paid=False, balance_zeroed_time=datetime(2026, 3, 5))
        repaid = replace(reversed_, is_paid=True)
        chain = datetime(2026, 3, 1)

        march = _run((2026, 3), [reversed_], chain_start=chain)
        may = _run((2026, 5), [repaid], chain_start=chain, stored=_store(march))
        june = _run((2026, 6), [repaid], chain_start=chain, stored=_store(march + may))

        assert march == []
        assert [x.commission for x in may] == [D('100.00')]
        assert june == []


# ── Scheme 2 ──────────────────────────────────────────────────────────────────


S2 = {'a17': CommissionScheme.PARTIAL_PAYMENTS}


class TestSchemeTwo:
    def test_instalments_split_the_full_commission(self) -> None:
        line = _line()
        march = _run(
            (2026, 3),
            [line],
            settings=_settings(**S2),
            money={1: _money('4000', last=datetime(2026, 3, 15))},
        )
        april = _run(
            (2026, 4),
            [line],
            settings=_settings(**S2),
            stored=_store(march),
            money={1: _money('10000', last=datetime(2026, 4, 20))},
        )

        assert [x.commission for x in march] == [D('40.00')]
        assert [x.commission for x in april] == [D('60.00')]
        assert march[0].share == D('0.4') and april[0].share == D(1)
        assert april[0].paid_date == datetime(2026, 4, 20)

    def test_the_net_of_refunds_example(self) -> None:
        """Spec FR-016: $10,000 at 1%; $4,000 March, $3,000 returned April, $3,000 May → $70."""
        line = _line()
        refund = rules.RefundLine(70, 10, datetime(2026, 4, 10), D('-10000.00'), D('0.3'))
        settings = _settings(**S2)

        march = _run((2026, 3), [line], settings=settings, money={1: _money('4000')})
        april = _run(
            (2026, 4),
            [line],
            settings=settings,
            refunds=[refund],
            stored=_store(march),
            money={1: _money('4000', refunded='3000')},
        )
        may = _run(
            (2026, 5),
            [line],
            settings=settings,
            refunds=[refund],
            stored=_store(march + april),
            money={1: _money('7000', refunded='3000')},
        )

        assert [x.commission for x in march] == [D('40.00')]
        assert sorted(x.commission for x in april) == [D('-30.00'), D('17.14')]
        assert [x.commission for x in may] == [D('42.86')]
        assert sum(x.commission for x in march + april + may) == D('70.00')

    def test_a_reversal_is_clawed_back(self) -> None:
        line = _line()
        settings = _settings(**S2)

        march = _run((2026, 3), [line], settings=settings, money={1: _money('4000')})
        april = _run(
            (2026, 4), [line], settings=settings, stored=_store(march), money={1: _money('0')}
        )

        assert [x.commission for x in april] == [D('-40.00')]

    def test_a_reversal_after_full_payment_is_clawed_back(self) -> None:
        """Analysis C1: a fully stored line must still see the reversal."""
        line = _line()
        settings = _settings(**S2)

        march = _run((2026, 3), [line], settings=settings, money={1: _money('10000')})
        april = _run(
            (2026, 4), [line], settings=settings, stored=_store(march), money={1: _money('6000')}
        )

        assert [x.commission for x in march] == [D('100.00')]
        assert [x.commission for x in april] == [D('-40.00')]


# ── Switches and the key freeze ───────────────────────────────────────────────


class TestSwitchesAndFreeze:
    def test_switching_two_to_one_pays_only_the_remainder(self) -> None:
        line = _line()
        march = _run((2026, 3), [line], settings=_settings(**S2), money={1: _money('4000')})
        paid = replace(line, is_paid=True, balance_zeroed_time=datetime(2026, 4, 3))

        april = _run((2026, 4), [paid], stored=_store(march), money={1: _money('10000')})

        assert [x.commission for x in april] == [D('60.00')]
        assert april[0].scheme == CommissionScheme.FULLY_PAID

    def test_switching_one_to_two_after_full_payment_pays_nothing(self) -> None:
        paid = _line(is_paid=True, balance_zeroed_time=datetime(2026, 3, 3))
        march = _run((2026, 3), [paid])

        april = _run(
            (2026, 4),
            [paid],
            settings=_settings(**S2),
            stored=_store(march),
            money={1: _money('10000')},
        )

        assert april == []

    def test_stored_keys_survive_a_reassignment_and_a_rate_change(self) -> None:
        line = _line()
        march = _run((2026, 3), [line], settings=_settings(**S2), money={1: _money('4000')})
        moved = replace(line, csp=AGENT_B, osp=AGENT_B)
        settings = replace(
            _settings(a17=CommissionScheme.PARTIAL_PAYMENTS), rates={}, participations={}
        )

        april = _run(
            (2026, 4), [moved], settings=settings, stored=_store(march), money={1: _money('10000')}
        )

        assert [(x.salesperson, x.rate, x.commission) for x in april] == [
            (AGENT_A, D('0.01'), D('60.00'))
        ]

    def test_a_refund_is_charged_to_the_agent_who_was_paid(self) -> None:
        """FR-009a: the stored agent and rate, not the customer's new salesperson."""
        paid = _line(is_paid=True, balance_zeroed_time=datetime(2026, 3, 3))
        march = _run((2026, 3), [paid])
        moved = replace(paid, csp=AGENT_B, osp=AGENT_B, label_rate=D('0.05'))
        refund = rules.RefundLine(70, 10, datetime(2026, 4, 2), D('-10000.00'), D('0.3'))

        april = _run((2026, 4), [moved], refunds=[refund], stored=_store(march))

        assert [(x.salesperson, x.commission) for x in april] == [(AGENT_A, D('-30.00'))]


# ── The invariant (SC-006) ────────────────────────────────────────────────────


@pytest.mark.parametrize('seed', range(200))
def test_no_key_is_ever_paid_past_its_full_commission(seed: int) -> None:
    rnd = random.Random(25_000 + seed)
    line = _line(osp=rnd.choice([AGENT_A, AGENT_B]), csp=rnd.choice([AGENT_A, AGENT_B]))
    refunds: list[rules.RefundLine] = []
    stored: list[rules.StoredLine] = []
    applied, refunded = D(0), D(0)
    zeroed = None
    for month in range(3, 9):
        schemes = {f'a{a}': rnd.choice(list(CommissionScheme)) for a in (AGENT_A, AGENT_B)}
        event = rnd.choice(['pay', 'pay', 'reverse', 'refund', 'nothing'])
        if event == 'pay':
            applied = min(applied + D(rnd.choice([1000, 2500, 4000, 10000])), D(10000) - refunded)
        elif event == 'reverse':
            applied = max(applied - D(rnd.choice([1000, 4000])), D(0))
        elif event == 'refund' and refunded < D(5000):
            refunded += D(1000)
            refunds.append(
                rules.RefundLine(
                    100 + month, 10, datetime(2026, month, 10), D('-10000.00'), D('0.1')
                )
            )
        is_paid = applied >= D(10000) - refunded
        if is_paid and zeroed is None:
            zeroed = datetime(2026, month, 12)
        candidate = replace(line, is_paid=is_paid, balance_zeroed_time=zeroed)
        out = _run(
            (2026, month),
            [candidate],
            settings=_settings(**schemes),
            refunds=refunds,
            stored=stored,
            chain_start=datetime(2026, 3, 1),
            money={1: _money(str(applied), str(refunded))},
        )
        stored += _store(out)

        by_key: dict = {}
        for s in stored:
            by_key.setdefault((s.salesperson, s.participation), []).append(s)
        for rows in by_key.values():
            sale_rows = [r for r in rows if r.movement == CommissionMovement.SALE]
            if sale_rows:
                assert sum((r.commission for r in sale_rows), D(0)) <= sale_rows[0].full_commission
            refund_ids = [
                r.customer_refund_detail for r in rows if r.movement == CommissionMovement.REFUND
            ]
            assert len(refund_ids) == len(set(refund_ids))
