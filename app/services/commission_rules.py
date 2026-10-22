"""Commission rules for one month: facts in, lines out (spec 025, research R1).

Pure: no database, no clock. `commission_service` gathers the facts and stores what a close
returns; everything that decides money is here, so it can be tested without a database.

**Who earns.** The current system's branches (`ReportsController.GetCommissionDetailsSQLQuery`, as
fixed by mbe PR #58): when the order's salesperson (osp) and the customer's (csp) coincide, the
agent earns as CLIENTE VITALICIO; otherwise osp earns CANALIZACIÓN and csp ATENCIÓN EN CAMPO. Only
agents earn, and a customer without a salesperson matches no branch, so its orders earn nothing.

**Key freeze.** Once a line has stored rows, its (agent, participation) keys, names and rates are
those stored (FR-009a): a reassignment or a rate change never moves a line already paid on.

**Per key, every month:**

- *Scheme 1, fully paid:* the line's full commission, less what earlier months stored, in the month
  its order is paid (`balance_zeroed_time`). An order whose stamp fell in an earlier closed month
  without being stored, because its payment was reversed before that month closed, is picked up now
  (analysis C2).
- *Scheme 2, partial payments:* `ROUND(full × min(applied ÷ (total − refunded), 1), 2)`, less what
  earlier months stored. A reversal therefore comes back as a negative amount (FR-016).
- *Refunds, both schemes:* a negative line for each refund line dated before the month's end that is
  not yet stored for the key. Under scheme 1 its sale must count this month or already be stored,
  which reproduces both of the current system's refund branches and charges a refund once (FR-017).

The cap holds by construction: a sale amount is always "what is due by now, less what was stored",
and "what is due" never exceeds the full commission.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from app.enums import CommissionMovement, CommissionScheme

CENTS = Decimal('0.01')
LIFETIME_CUSTOMER, CHANNELLING, FIELD_SERVICE = 1, 2, 3


def _round(value: Decimal) -> Decimal:
    """MariaDB's `ROUND(x, 2)`, which the current system's query uses: half away from zero."""
    return value.quantize(CENTS, ROUND_HALF_UP)


@dataclass(frozen=True)
class CandidateLine:
    sales_order: int
    sales_order_detail: int
    order_date: datetime
    customer: int
    customer_name: str
    osp: int
    csp: int | None
    product: int
    product_name: str
    #: Unit price after discount, tax included, rounded to cents, as the current system rounds it.
    price: Decimal
    quantity: Decimal
    #: None when unset or the `0001-01-01` sentinel.
    balance_zeroed_time: datetime | None
    is_paid: bool
    label: int | None
    label_name: str | None
    label_rate: Decimal


@dataclass(frozen=True)
class OrderMoney:
    """An order's money as of the month's end (scheme 2)."""

    applied: Decimal
    refunded: Decimal
    order_total: Decimal
    #: The order's latest application or refund in the month, for the line's paid date.
    last_activity: datetime | None = None


@dataclass(frozen=True)
class RefundLine:
    customer_refund_detail: int
    sales_order_detail: int
    date: datetime
    #: Negative unit price after discount, tax included, rounded to cents.
    price: Decimal
    quantity: Decimal


@dataclass(frozen=True)
class Settings:
    #: agent → `CommissionScheme`
    agents: dict[int, int]
    #: (agent, label, participation) → share
    rates: dict[tuple[int, int, int], Decimal]
    #: participation → name
    participations: dict[int, str]


@dataclass(frozen=True)
class ComputedLine:
    scheme: int
    movement: int
    sales_order: int
    sales_order_detail: int
    customer_refund_detail: int | None
    order_date: datetime
    paid_date: datetime
    customer: int
    customer_name: str
    product: int
    product_name: str
    label: int | None
    label_name: str | None
    label_rate: Decimal
    salesperson: int
    order_salesperson: int
    customer_salesperson: int
    participation: int
    participation_name: str
    participation_rate: Decimal
    rate: Decimal
    price: Decimal
    quantity: Decimal
    total: Decimal
    full_commission: Decimal
    share: Decimal | None
    commission: Decimal


@dataclass(frozen=True)
class StoredLine:
    """A row of a closed month, as far as later months need it."""

    sales_order_detail: int
    salesperson: int
    participation: int
    participation_name: str
    participation_rate: Decimal
    label: int | None
    label_name: str | None
    label_rate: Decimal
    rate: Decimal
    customer_salesperson: int
    movement: int
    customer_refund_detail: int | None
    scheme: int
    commission: Decimal
    full_commission: Decimal

    @classmethod
    def from_computed(cls, line: ComputedLine) -> 'StoredLine':
        return cls(
            sales_order_detail=line.sales_order_detail,
            salesperson=line.salesperson,
            participation=line.participation,
            participation_name=line.participation_name,
            participation_rate=line.participation_rate,
            label=line.label,
            label_name=line.label_name,
            label_rate=line.label_rate,
            rate=line.rate,
            customer_salesperson=line.customer_salesperson,
            movement=line.movement,
            customer_refund_detail=line.customer_refund_detail,
            scheme=line.scheme,
            commission=line.commission,
            full_commission=line.full_commission,
        )


@dataclass(frozen=True)
class _Key:
    agent: int
    participation: int
    participation_name: str
    participation_rate: Decimal
    label: int | None
    label_name: str | None
    label_rate: Decimal
    customer_salesperson: int

    @property
    def rate(self) -> Decimal:
        return self.label_rate * self.participation_rate


def _derived_keys(line: CandidateLine, settings: Settings) -> list[_Key]:
    """The current system's three branches, with today's settings."""
    if line.csp is None:
        return []
    if line.csp == line.osp:
        branches = [(line.csp, LIFETIME_CUSTOMER)]
    else:
        branches = [(line.osp, CHANNELLING), (line.csp, FIELD_SERVICE)]
    keys = []
    for agent, participation in branches:
        if agent not in settings.agents:
            continue
        share = (
            settings.rates.get((agent, line.label, participation), Decimal(0))
            if line.label is not None
            else Decimal(0)
        )
        keys.append(
            _Key(
                agent=agent,
                participation=participation,
                participation_name=settings.participations.get(participation, ''),
                participation_rate=share,
                label=line.label,
                label_name=line.label_name,
                label_rate=line.label_rate if line.label is not None else Decimal(0),
                customer_salesperson=line.csp,
            )
        )
    return keys


def _frozen_keys(rows: Iterable[StoredLine]) -> list[_Key]:
    keys: dict[tuple[int, int], _Key] = {}
    for row in rows:
        keys.setdefault(
            (row.salesperson, row.participation),
            _Key(
                agent=row.salesperson,
                participation=row.participation,
                participation_name=row.participation_name,
                participation_rate=row.participation_rate,
                label=row.label,
                label_name=row.label_name,
                label_rate=row.label_rate,
                customer_salesperson=row.customer_salesperson,
            ),
        )
    return list(keys.values())


def month_lines(
    *,
    month_start: datetime,
    next_month: datetime,
    chain_start: datetime | None,
    candidates: list[CandidateLine],
    money: dict[int, OrderMoney],
    refunds: list[RefundLine],
    stored: list[StoredLine],
    settings: Settings,
) -> list[ComputedLine]:
    """Every non-zero commission line the month owes, per research R1."""
    stored_by_line: dict[int, list[StoredLine]] = {}
    for row in stored:
        stored_by_line.setdefault(row.sales_order_detail, []).append(row)
    refunds_by_line: dict[int, list[RefundLine]] = {}
    for refund in refunds:
        refunds_by_line.setdefault(refund.sales_order_detail, []).append(refund)

    out: list[ComputedLine] = []
    for line in candidates:
        rows = stored_by_line.get(line.sales_order_detail, [])
        keys = _frozen_keys(rows) if rows else _derived_keys(line, settings)
        total = _round(line.price * line.quantity)
        for key in keys:
            key_rows = [
                r
                for r in rows
                if (r.salesperson, r.participation) == (key.agent, key.participation)
            ]
            stored_sales = sum(
                (r.commission for r in key_rows if r.movement == CommissionMovement.SALE),
                Decimal(0),
            )
            has_stored_sale = any(r.movement == CommissionMovement.SALE for r in key_rows)
            stored_refunds = {
                r.customer_refund_detail
                for r in key_rows
                if r.movement == CommissionMovement.REFUND
            }
            scheme = settings.agents.get(
                key.agent, key_rows[-1].scheme if key_rows else CommissionScheme.FULLY_PAID
            )
            full = _round(line.price * line.quantity * key.rate)

            def emit(
                movement: int,
                *,
                commission: Decimal,
                paid_date: datetime,
                price: Decimal = line.price,
                quantity: Decimal = line.quantity,
                line_total: Decimal = total,
                full_commission: Decimal = full,
                share: Decimal | None = None,
                refund: RefundLine | None = None,
                key: _Key = key,
                scheme: int = scheme,
            ) -> None:
                out.append(
                    ComputedLine(
                        scheme=int(scheme),
                        movement=int(movement),
                        sales_order=line.sales_order,
                        sales_order_detail=line.sales_order_detail,
                        customer_refund_detail=refund.customer_refund_detail if refund else None,
                        order_date=refund.date if refund else line.order_date,
                        paid_date=paid_date,
                        customer=line.customer,
                        customer_name=line.customer_name,
                        product=line.product,
                        product_name=line.product_name,
                        label=key.label,
                        label_name=key.label_name,
                        label_rate=key.label_rate,
                        salesperson=key.agent,
                        order_salesperson=line.osp,
                        customer_salesperson=key.customer_salesperson,
                        participation=key.participation,
                        participation_name=key.participation_name,
                        participation_rate=key.participation_rate,
                        rate=key.rate,
                        price=price,
                        quantity=quantity,
                        total=line_total,
                        full_commission=full_commission,
                        share=share,
                        commission=commission,
                    )
                )

            # An unlabelled product or a missing rate earns 0, and still lists once, as in the
            # current system, so that someone sees it and labels it.
            listed_at_zero = full == 0 and not has_stored_sale
            sale_counts_now = False
            if scheme == CommissionScheme.FULLY_PAID:
                zeroed = line.balance_zeroed_time
                in_month = zeroed is not None and month_start <= zeroed < next_month
                picked_up_late = (
                    zeroed is not None
                    and chain_start is not None
                    and chain_start <= zeroed < month_start
                    and not has_stored_sale
                )
                if line.is_paid and (in_month or picked_up_late):
                    sale_counts_now = True
                    amount = full - stored_sales
                    if amount or listed_at_zero:
                        emit(CommissionMovement.SALE, commission=amount, paid_date=zeroed)
            else:
                facts = money.get(line.sales_order)
                if facts is not None:
                    # Net of refunds, so a returned item does not hold the share below 100%
                    # (FR-016); a fully refunded order is wholly owed and its refunds cancel it.
                    owed = facts.order_total - facts.refunded
                    ratio = min(facts.applied / owed, Decimal(1)) if owed > 0 else Decimal(1)
                    share = ratio.quantize(Decimal('0.000001'), ROUND_HALF_UP)
                    amount = _round(full * ratio) - stored_sales
                    if amount or listed_at_zero:
                        emit(
                            CommissionMovement.SALE,
                            commission=amount,
                            paid_date=facts.last_activity or month_start,
                            share=share,
                        )

            for refund in refunds_by_line.get(line.sales_order_detail, []):
                if refund.date >= next_month or refund.customer_refund_detail in stored_refunds:
                    continue
                if scheme == CommissionScheme.FULLY_PAID and not (
                    sale_counts_now or has_stored_sale
                ):
                    continue
                refund_total = _round(refund.price * refund.quantity)
                amount = _round(refund.price * refund.quantity * key.rate)
                if amount:
                    emit(
                        CommissionMovement.REFUND,
                        commission=amount,
                        paid_date=refund.date,
                        price=refund.price,
                        quantity=refund.quantity,
                        line_total=refund_total,
                        full_commission=amount,
                        refund=refund,
                    )
    return out
