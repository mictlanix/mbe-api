"""The documents behind an aggregate (spec 037, FR-040–FR-042, contracts/api.md "Detail").

Each kind reads the same row subqueries the facts sum (`facts.sale_lines` and the rest), with the
same filters and scope, so its amounts are the aggregate's amounts: a line carries its document's
rounding residual exactly as in the fact. Orders, refunds and credit notes group those lines by
document. `sums` cover every page; which one equals which measure is the contract's table.
"""

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from sqlalchemy import Select, case, func, literal, null, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.expression import ColumnElement

from app.core.clock import local_now
from app.core.deps import CurrentUser, forbidden, has_any_privilege
from app.core.error_codes import ErrorCode
from app.core.errors import DomainError
from app.enums import AccessRight, CommissionMovement, SystemObject
from app.models.customer import Customer
from app.models.fiscal import FiscalDocument
from app.models.product import Product
from app.models.sales import CustomerPayment, CustomerRefund, SalesOrder
from app.schemas.analytics import (
    AnalyticsDetailFigures,
    AnalyticsDetailItem,
    AnalyticsDetailRequest,
    AnalyticsDetailResponse,
    DetailFlag,
    DetailKind,
    Grain,
    MeasureKey,
    StockOptions,
    VatTreatment,
)
from app.services import accounts_service, kardex_service, sales_order_service, totals
from app.services.analytics import balances, commissions, engine, facts
from app.services.analytics.catalog import DIMENSIONS, Availability, Fact, Family

KIND_FACT = {
    DetailKind.ORDERS: Fact.SALE_LINE,
    DetailKind.LINES: Fact.SALE_LINE,
    DetailKind.REFUNDS: Fact.REFUND_LINE,
    DetailKind.CREDIT_NOTES: Fact.CREDIT_NOTE,
    DetailKind.PAYMENTS: Fact.PAYMENT,
    DetailKind.STOCK: Fact.STOCK,
    DetailKind.MOVEMENTS: Fact.MOVEMENT,
    DetailKind.RECEIVABLES: Fact.RECEIVABLE,
    DetailKind.COMMISSION_LINES: Fact.COMMISSION,
}
#: The measure a kind's refusals name (`analytics.dimension_not_applicable`).
KIND_MEASURE = {
    DetailKind.ORDERS: MeasureKey.GROSS_SALES,
    DetailKind.LINES: MeasureKey.GROSS_SALES,
    DetailKind.REFUNDS: MeasureKey.REFUNDS,
    DetailKind.CREDIT_NOTES: MeasureKey.CREDIT_NOTES,
    DetailKind.PAYMENTS: MeasureKey.COLLECTED,
    DetailKind.STOCK: MeasureKey.ON_HAND,
    DetailKind.MOVEMENTS: MeasureKey.UNITS_IN,
    DetailKind.RECEIVABLES: MeasureKey.RECEIVABLE,
    DetailKind.COMMISSION_LINES: MeasureKey.COMMISSION,
}
#: The family a kind belongs to; the rest are sales.
KIND_FAMILY = {
    DetailKind.PAYMENTS: Family.PAYMENTS,
    DetailKind.STOCK: Family.INVENTORY,
    DetailKind.MOVEMENTS: Family.INVENTORY,
    DetailKind.RECEIVABLES: Family.RECEIVABLES,
    DetailKind.COMMISSION_LINES: Family.COMMISSIONS,
}
#: Read on the document kind, besides the family (FR-060); stock pairs need none.
KIND_PRIVILEGE = {
    DetailKind.ORDERS: SystemObject.SALES_ORDERS,
    DetailKind.LINES: SystemObject.SALES_ORDERS,
    DetailKind.REFUNDS: SystemObject.CUSTOMER_REFUNDS,
    DetailKind.CREDIT_NOTES: SystemObject.FISCAL_DOCUMENTS,
    DetailKind.PAYMENTS: SystemObject.CUSTOMER_PAYMENTS,
    DetailKind.MOVEMENTS: SystemObject.KARDEX,
    DetailKind.RECEIVABLES: SystemObject.SALES_ORDERS,
}
COST_FLAGS = {DetailFlag.BELOW_COST, DetailFlag.WITHOUT_COST}
SENSITIVE_KEYS = {'cost', 'margin'}
#: A stock pair's value is cost-derived too: left out of its figures and of `sums` (spec 038).
SENSITIVE_VALUES = {'value'}
#: What each stock flag selects (research R5).
STOCK_FLAGS = {
    DetailFlag.STOCK_OUT: lambda pair: pair.stock_out,
    DetailFlag.BELOW_REORDER: lambda pair: pair.below_reorder,
    DetailFlag.DEAD: lambda pair: pair.dead,
    DetailFlag.SLOW: lambda pair: pair.slow,
    DetailFlag.UNCOSTED: lambda pair: pair.uncosted,
}
SORTS = {'date', 'amount', 'document'}


def parse_sort(sort: str | None) -> tuple[str, bool]:
    """`(column, descending)`; the default is the newest first."""
    if sort is None:
        return 'date', True
    name = sort.removeprefix('-')
    if name not in SORTS:
        raise DomainError(
            ErrorCode.ANALYTICS_SORT_INVALID, 'Sort by date, amount or document', {'sort': sort}
        )
    return name, sort.startswith('-')


async def check(
    db: AsyncSession, request: AnalyticsDetailRequest, current: CurrentUser
) -> tuple[AnalyticsDetailRequest, int | None, bool]:
    """Privileges, scope and applicability. Returns the request to run (a commission agent without
    112 gets their own lines only, spec 038 R11), the facility scope and cost visibility."""
    engine.validate_period(request.start, request.end)
    if request.kind is DetailKind.STOCK:
        engine.history_guard([MeasureKey.ON_HAND], stock_moment(request), request.end)
    elif request.kind is DetailKind.MOVEMENTS:
        engine.history_guard([MeasureKey.UNITS_IN], request.start, request.end)
    elif request.kind is DetailKind.RECEIVABLES:
        engine.history_guard([MeasureKey.RECEIVABLE], receivable_day(request), request.end)
    held = await engine.families(db, current)
    family = KIND_FAMILY.get(request.kind, Family.SALES)
    if family not in held:
        raise engine.family_forbidden(family)
    if not current.administrator and request.kind in KIND_PRIVILEGE:
        grant = (KIND_PRIVILEGE[request.kind], AccessRight.READ)
        if not await has_any_privilege(db, current.user_id, [grant]):
            raise forbidden([grant])
    cost = Family.COST in held
    if request.flag in COST_FLAGS and not cost:
        raise engine.family_forbidden(Family.COST)
    fact = KIND_FACT[request.kind]
    for item in request.filters:
        if DIMENSIONS[item.dimension].facts[fact] is Availability.REFUSED:
            raise DomainError(
                ErrorCode.ANALYTICS_DIMENSION_NOT_APPLICABLE,
                f'{request.kind.value} cannot be filtered by {item.dimension.value}',
                {'measure': KIND_MEASURE[request.kind].value, 'dimension': item.dimension.value},
            )
    parse_sort(request.sort)
    facility = await engine.resolve_scope(db, current, request.facility)
    await engine.check_facility_filters(db, current, engine.parse_filters(request.filters))
    if request.kind is DetailKind.COMMISSION_LINES:
        agent = await engine.own_commissions(db, current)
        if agent is not None:
            request = request.model_copy(
                update={'filters': engine.only_agent(request.filters, agent)}
            )
    return request, facility, cost


# ── The row selects ───────────────────────────────────────────────────────────


def _filtered(fact: Fact, request: AnalyticsDetailRequest, facility: int | None):
    """The fact's rows for the period and scope, the joins its filters need, and the conditions."""
    rows = facts.SOURCES[fact](request.start, request.end, facility)
    joins = facts._Joins(rows)
    conditions = [
        facts._matches(facts.dimension_expression(fact, dimension, joins, Grain.DAY), values)
        if DIMENSIONS[dimension].facts[fact] is Availability.COLUMN
        else literal(None in values)
        for dimension, values in engine.parse_filters(request.filters).items()
    ]
    c = rows.c
    if request.flag is DetailFlag.BELOW_COST:
        conditions.append(c.below == 1)
    elif request.flag is DetailFlag.WITHOUT_COST:
        conditions.append(c.costed == 0)
    elif request.flag in (DetailFlag.COLLECTED, DetailFlag.PAID_OUT, DetailFlag.STORE_CREDIT):
        conditions.append(c['class'] == request.flag.value)
    return rows, joins, conditions


def _cents(rows, vat: VatTreatment) -> ColumnElement:  # noqa: ANN001
    return rows.c.sub_c + rows.c.tax_c if vat is VatTreatment.INCLUDED else rows.c.sub_c


def _base(request: AnalyticsDetailRequest, facility: int | None) -> Select:
    """One row per item, with uniform columns: what the page, the count and the sums read."""
    kind = request.kind
    rows, joins, conditions = _filtered(KIND_FACT[kind], request, facility)
    c = rows.c
    none = null()
    if kind is DetailKind.PAYMENTS:
        columns = [
            c.id.label('id'), c.doc.label('document_id'), c.at.label('at'),
            c.facility.label('facility'), c.customer.label('customer'),
            c.salesperson.label('salesperson'), none.label('product'), none.label('quantity'),
            (c.collected_c + c.paid_out_c + c.store_c).label('amount_c'),
            literal(0).label('discount_c'), none.label('cost_c'), none.label('margin_c'),
            c.payment_method.label('payment_method'),
        ]  # fmt: skip
        query = select(*columns)
    elif kind is DetailKind.LINES:
        columns = [
            c.id.label('id'), c.doc.label('document_id'), c.at.label('at'),
            c.facility.label('facility'), c.customer.label('customer'),
            c.salesperson.label('salesperson'), c.product.label('product'),
            c.qty.label('quantity'), _cents(rows, request.vat).label('amount_c'),
            _discount(c, request.vat).label('discount_c'),
            case((c.costed == 1, c.cost_c), else_=None).label('cost_c'),
            case((c.costed == 1, c.sub_c - c.cost_c), else_=None).label('margin_c'),
            none.label('payment_method'),
        ]  # fmt: skip
        query = select(*columns)
    else:
        has_cost = kind is not DetailKind.CREDIT_NOTES
        has_lines = kind is not DetailKind.CREDIT_NOTES
        discounts = func.sum(_discount(c, request.vat)) if kind is DetailKind.ORDERS else None
        columns = [
            c.doc.label('id'), c.doc.label('document_id'), func.max(c.at).label('at'),
            func.max(c.facility).label('facility'), func.max(c.customer).label('customer'),
            (func.max(c.salesperson) if has_lines else none).label('salesperson'),
            none.label('product'),
            (func.sum(c.qty) if has_lines else none).label('quantity'),
            func.sum(_cents(rows, request.vat)).label('amount_c'),
            (discounts if kind is DetailKind.ORDERS else literal(0)).label('discount_c'),
            (func.sum(case((c.costed == 1, c.cost_c), else_=None)) if has_cost else none).label(
                'cost_c'
            ),
            (
                func.sum(case((c.costed == 1, c.sub_c - c.cost_c), else_=None))
                if has_cost
                else none
            ).label('margin_c'),
            none.label('payment_method'),
        ]  # fmt: skip
        query = select(*columns)
    query = joins.apply(query.select_from(rows)).where(true(), *conditions)
    if kind not in (DetailKind.LINES, DetailKind.PAYMENTS):
        query = query.group_by(c.doc)
    return query


def _discount(c, vat: VatTreatment) -> ColumnElement:  # noqa: ANN001
    return c.disc_sub_c + c.disc_tax_c if vat is VatTreatment.INCLUDED else c.disc_sub_c


# ── Running it ────────────────────────────────────────────────────────────────


async def execute(
    db: AsyncSession, request: AnalyticsDetailRequest, facility: int | None, *, everything: bool
) -> AnalyticsDetailResponse:
    """A page of items (or every item, for an export) with the sums over all of them."""
    as_of = local_now()
    if request.kind is DetailKind.RECEIVABLES:
        return await _receivables(db, request, facility, as_of, everything=everything)
    if request.flag is DetailFlag.OVERDUE:
        return await _overdue(db, request, facility, as_of, everything=everything)
    if request.kind is DetailKind.STOCK:
        return await _stock(db, request, facility, as_of, everything=everything)
    if request.kind is DetailKind.MOVEMENTS:
        return await _movements(db, request, facility, as_of, everything=everything)
    if request.kind is DetailKind.COMMISSION_LINES:
        return await _commission_lines(db, request, facility, as_of, everything=everything)
    base = _base(request, facility).subquery('detail')
    total = (await db.execute(select(func.count()).select_from(base))).scalar_one()
    aggregate = (
        await db.execute(
            select(
                func.coalesce(func.sum(base.c.amount_c), 0),
                func.coalesce(func.sum(base.c.quantity), 0),
                func.coalesce(func.sum(base.c.discount_c), 0),
                func.coalesce(func.sum(base.c.cost_c), 0),
                func.coalesce(func.sum(base.c.margin_c), 0),
            )
        )
    ).one()
    sums = {
        'amount': engine.money(aggregate[0]),
        'quantity': engine.quantity(aggregate[1]),
        'discount': engine.money(aggregate[2]),
        'cost': engine.money(aggregate[3]),
        'margin': engine.money(aggregate[4]),
    }
    name, descending = parse_sort(request.sort)
    order = {'date': base.c.at, 'amount': base.c.amount_c, 'document': base.c.document_id}[name]
    page = (
        select(
            base,
            Customer.name.label('customer_name'),
            Product.code.label('product_code'),
            Product.name.label('product_name'),
        )
        .outerjoin(Customer, Customer.customer_id == base.c.customer)
        .outerjoin(Product, Product.product_id == base.c.product)
        .order_by(
            order.desc() if descending else order.asc(),
            base.c.id.desc() if descending else base.c.id,
        )
    )
    if not everything:
        page = page.offset(request.skip).limit(request.limit)
    found = (await db.execute(page)).all()
    folios = await _folios(db, request.kind, [row.document_id for row in found])
    items = [
        AnalyticsDetailItem(
            kind=request.kind,
            id=row.id,
            document_id=row.document_id,
            folio=folios.get(row.document_id),
            date=row.at,
            facility=row.facility,
            customer=row.customer,
            customer_name=row.customer_name,
            salesperson=row.salesperson,
            product=row.product,
            product_code=row.product_code,
            product_name=row.product_name,
            quantity=None if row.quantity is None else engine.quantity(row.quantity),
            amount=engine.money(row.amount_c),
            cost=None if row.cost_c is None else engine.money(row.cost_c),
            margin=None if row.margin_c is None else engine.money(row.margin_c),
            payment_method=row.payment_method,
        )
        for row in found
    ]
    return AnalyticsDetailResponse(as_of=as_of, items=items, total=total, sums=sums)


async def _folios(db: AsyncSession, kind: DetailKind, ids: Sequence[int]) -> dict[int, str]:
    if not ids:
        return {}
    match kind:
        case DetailKind.ORDERS | DetailKind.LINES:
            query = select(SalesOrder.sales_order_id, SalesOrder.serial).where(
                SalesOrder.sales_order_id.in_(ids)
            )
        case DetailKind.REFUNDS:
            query = select(CustomerRefund.customer_refund_id, CustomerRefund.serial).where(
                CustomerRefund.customer_refund_id.in_(ids)
            )
        case DetailKind.CREDIT_NOTES:
            query = select(FiscalDocument.fiscal_document_id, FiscalDocument.serial).where(
                FiscalDocument.fiscal_document_id.in_(ids)
            )
        case _:
            query = select(CustomerPayment.customer_payment_id, CustomerPayment.serial).where(
                CustomerPayment.customer_payment_id.in_(ids)
            )
    return {
        key: str(serial) for key, serial in (await db.execute(query)).all() if serial is not None
    }


async def _overdue(
    db: AsyncSession,
    request: AnalyticsDetailRequest,
    facility: int | None,
    as_of: Any,
    *,
    everything: bool,
) -> AnalyticsDetailResponse:
    """Open credit orders past due as of today (spec 033's balance rule), whatever the period.

    The balance is computed in Python by `open_credit_orders`, so these are paged and summed here.
    """
    found = await sales_order_service.open_credit_orders(db, facility=facility, due_before=as_of)
    name, descending = parse_sort(request.sort)

    def sort_key(entry: tuple[Any, Any]) -> Any:
        order, balance = entry
        return {
            'date': order.confirmation_time or order.date,
            'amount': balance.balance,
            'document': order.sales_order_id,
        }[name]

    found.sort(key=lambda entry: entry[0].sales_order_id, reverse=descending)
    found.sort(key=sort_key, reverse=descending)
    names = await _customer_names(db, [order.customer for order, _ in found])
    shown = found if everything else found[request.skip : request.skip + request.limit]
    items = [
        AnalyticsDetailItem(
            kind=DetailKind.ORDERS, id=order.sales_order_id, document_id=order.sales_order_id,
            folio=None if order.serial is None else str(order.serial),
            date=order.confirmation_time or order.date, facility=order.facility,
            customer=order.customer, customer_name=names.get(order.customer),
            salesperson=order.salesperson, product=None, product_code=None, product_name=None,
            quantity=None, amount=balance.balance, cost=None, margin=None, payment_method=None,
        )
        for order, balance in shown
    ]  # fmt: skip
    total_amount = sum((balance.balance for _, balance in found), Decimal(0))
    sums = {
        'amount': total_amount,
        'quantity': Decimal('0.0000'),
        'discount': Decimal('0.00'),
        'cost': Decimal('0.00'),
        'margin': Decimal('0.00'),
    }
    return AnalyticsDetailResponse(as_of=as_of, items=items, total=len(found), sums=sums)


async def _customer_names(db: AsyncSession, ids: Sequence[int]) -> dict[int, str]:
    if not ids:
        return {}
    query = select(Customer.customer_id, Customer.name).where(Customer.customer_id.in_(set(ids)))
    return dict((await db.execute(query)).all())


def serialize_items(response: AnalyticsDetailResponse, cost_visible: bool) -> dict[str, Any]:
    """The response as JSON-ready data. Without the cost privilege, `cost` and `margin` are left
    out of every item and of `sums`, and so is a stock pair's value: absent, not null (FR-042,
    SC-005). Holders keep a null cost on an uncosted line, which is why this never uses
    `exclude_none`."""
    if cost_visible:
        return response.model_dump(mode='json')
    data = response.model_dump(mode='json', exclude={'items': {'__all__': SENSITIVE_KEYS}})
    for item in data['items']:
        if item.get('figures'):
            for key in SENSITIVE_VALUES:
                item['figures']['values'].pop(key, None)
    hidden = SENSITIVE_KEYS | SENSITIVE_VALUES
    data['sums'] = {k: v for k, v in data['sums'].items() if k not in hidden}
    return data


# ── Stock and movements (spec 038) ────────────────────────────────────────────


def stock_moment(request: AnalyticsDetailRequest) -> Any:
    """Stock pairs are listed as of the end of the period, or now when it reaches today."""
    return min(request.end, local_now().date())


def _text(value: Decimal | None, step: Decimal = engine.QUANTITY) -> str | None:
    return None if value is None else str(value.quantize(step, rounding='ROUND_HALF_UP'))


def _stock_figures(pair: balances.Pair) -> dict[str, str | None]:
    cover = pair.cover
    return {
        'on_hand': _text(pair.on_hand),
        'reserved': _text(pair.reserved),
        'available': _text(pair.available) if pair.reserved is not None else None,
        'in_transit': _text(pair.in_transit),
        'velocity': _text(pair.velocity),
        'cover': _text(cover, engine.DAYS),
        'reorder_point': _text(pair.reorder_point),
        'suggested': _text(pair.suggested),
        'last_movement': None if pair.last is None else pair.last.isoformat(),
        'value': str(engine.money(pair.value_c)) if pair.cost is not None else None,
    }


async def _stock(
    db: AsyncSession,
    request: AnalyticsDetailRequest,
    facility: int | None,
    as_of: datetime,
    *,
    everything: bool,
) -> AnalyticsDetailResponse:
    """The product × warehouse pairs at the moment, with R5's figures at the default options, and
    a flag's pairs only. The pairs are what the stock fact sums, so the sums equal the query."""
    moment = stock_moment(request)
    options = StockOptions()
    found = await balances.ledger(
        db,
        [moment],
        facility=facility,
        filters=engine.parse_filters(request.filters),
        options=options,
    )
    keep = STOCK_FLAGS.get(request.flag) if request.flag is not None else None
    pairs = [
        pair
        for pair in found.pairs[moment]
        if (keep(pair) if keep is not None else any(balances.columns(pair, options).values()))
    ]
    info = found.products

    def code(pair: balances.Pair) -> str:
        return info.get(pair.product, balances.ProductInfo()).code or ''

    # Sorted by code and warehouse, then stably by the requested column, null last: `date` is the
    # last movement, `amount` the on hand, `document` the product code.
    name, descending = parse_sort(request.sort)
    pairs.sort(key=lambda pair: (code(pair), pair.warehouse))
    value = {'date': lambda p: p.last, 'amount': lambda p: p.on_hand, 'document': code}[name]
    present = sorted((p for p in pairs if value(p) is not None), key=value, reverse=descending)
    pairs = present + [p for p in pairs if value(p) is None]
    shown = pairs if everything else pairs[request.skip : request.skip + request.limit]
    items = [
        AnalyticsDetailItem(
            kind=DetailKind.STOCK, id=pair.product, folio=None, facility=pair.facility,
            customer=None, customer_name=None, salesperson=None, product=pair.product,
            product_code=info.get(pair.product, balances.ProductInfo()).code,
            product_name=info.get(pair.product, balances.ProductInfo()).name,
            quantity=engine.quantity(pair.on_hand), cost=pair.cost, payment_method=None,
            warehouse=pair.warehouse, figures=AnalyticsDetailFigures(values=_stock_figures(pair)),
        )
        for pair in shown
    ]  # fmt: skip
    sums = {
        'quantity': engine.quantity(sum((p.on_hand for p in pairs), Decimal(0))),
        'suggested': engine.quantity(sum((p.suggested for p in pairs), Decimal(0))),
        'value': engine.money(sum(p.value_c for p in pairs)),
    }
    return AnalyticsDetailResponse(as_of=as_of, items=items, total=len(pairs), sums=sums)


async def _movements(
    db: AsyncSession,
    request: AnalyticsDetailRequest,
    facility: int | None,
    as_of: datetime,
    *,
    everything: bool,
) -> AnalyticsDetailResponse:
    """The ledger entries of the period, each resolved to its document as the kardex does."""
    rows, joins, conditions = _filtered(Fact.MOVEMENT, request, facility)
    c = rows.c
    base = (
        joins.apply(
            select(
                c.id, c.at, c.facility, c.warehouse, c.product, c.qty.label('quantity'),
                c.movement_type, c.reference, c.units_in, c.units_out,
            ).select_from(rows)
        )
        .where(true(), *conditions)
        .subquery('detail')
    )  # fmt: skip
    total = (await db.execute(select(func.count()).select_from(base))).scalar_one()
    aggregate = (
        await db.execute(
            select(
                func.coalesce(func.sum(base.c.quantity), 0),
                func.coalesce(func.sum(base.c.units_in), 0),
                func.coalesce(func.sum(base.c.units_out), 0),
            )
        )
    ).one()
    sums = {
        'quantity': engine.quantity(aggregate[0]),
        'units_in': engine.quantity(aggregate[1]),
        'units_out': engine.quantity(aggregate[2]),
    }
    name, descending = parse_sort(request.sort)
    order = {'date': base.c.at, 'amount': base.c.quantity, 'document': base.c.reference}[name]
    page = (
        select(base, Product.code.label('product_code'), Product.name.label('product_name'))
        .outerjoin(Product, Product.product_id == base.c.product)
        .order_by(
            order.desc() if descending else order.asc(),
            base.c.id.desc() if descending else base.c.id,
        )
    )
    if not everything:
        page = page.offset(request.skip).limit(request.limit)
    found = (await db.execute(page)).all()
    entries = [
        SimpleNamespace(
            lot_serial_tracking_id=row.id, source=row.movement_type, reference=row.reference,
            product=row.product,
        )
        for row in found
    ]  # fmt: skip
    documents = await kardex_service._documents(
        db, kardex_service.references_by_type(entries), {row.product for row in found}
    )
    resolved = kardex_service.resolve(entries, documents)
    items = []
    for row in found:
        document = resolved.get(row.id)
        items.append(
            AnalyticsDetailItem(
                kind=DetailKind.MOVEMENTS, id=row.id,
                document_id=None if document is None else document.id,
                folio=None if document is None or document.serial is None else str(document.serial),
                date=row.at, facility=row.facility, customer=None, customer_name=None,
                salesperson=None, product=row.product, product_code=row.product_code,
                product_name=row.product_name, quantity=engine.quantity(row.quantity),
                payment_method=None, warehouse=row.warehouse,
                figures=AnalyticsDetailFigures(
                    values={
                        'movement_type': str(row.movement_type),
                        'source': kardex_service.source_name(row.movement_type),
                        'reference': str(row.reference),
                    }
                ),
            )
        )  # fmt: skip
    return AnalyticsDetailResponse(as_of=as_of, items=items, total=total, sums=sums)


# ── Receivables (spec 038) ────────────────────────────────────────────────────


def receivable_day(request: AnalyticsDetailRequest) -> Any:
    """The open orders are listed as of the period's end: now when it reaches today, else the
    stored month-end on or before it, as the receivable fact reads them (R8)."""
    return balances.read_day(stock_moment(request), local_now().date())


async def _receivables(
    db: AsyncSession,
    request: AnalyticsDetailRequest,
    facility: int | None,
    as_of: datetime,
    *,
    everything: bool,
) -> AnalyticsDetailResponse:
    """The open credit orders the receivable fact sums at the moment, with flag `overdue` those
    past due. `amount` is the balance in base currency; the sum rounds once, as the fact does."""
    day = receivable_day(request)
    found = (await balances.receivables_at(db, [day], facility))[day]
    documents = balances.selected(found, engine.parse_filters(request.filters), day)
    if request.flag is DetailFlag.OVERDUE:
        documents = [d for d in documents if accounts_service.days_past_due(d.due_date, day) > 0]
    orders = {}
    if documents:
        rows = await db.execute(
            select(SalesOrder.sales_order_id, SalesOrder.serial, SalesOrder.confirmation_time,
                   SalesOrder.date)
            .where(SalesOrder.sales_order_id.in_([d.id for d in documents]))
        )  # fmt: skip
        orders = {oid: (serial, confirmed or dated) for oid, serial, confirmed, dated in rows.all()}
    name, descending = parse_sort(request.sort)
    value = {
        'date': lambda d: orders.get(d.id, (None, None))[1],
        'amount': lambda d: d.base,
        'document': lambda d: d.id,
    }[name]
    documents.sort(key=lambda d: d.id, reverse=descending)
    present = sorted((d for d in documents if value(d) is not None), key=value, reverse=descending)
    documents = present + [d for d in documents if value(d) is None]
    shown = documents if everything else documents[request.skip : request.skip + request.limit]
    names = await _customer_names(db, [d.party for d in shown])
    items = []
    for document in shown:
        serial, dated = orders.get(document.id, (None, None))
        days = accounts_service.days_past_due(document.due_date, day)
        items.append(
            AnalyticsDetailItem(
                kind=DetailKind.RECEIVABLES, id=document.id, document_id=document.id,
                folio=None if serial is None else str(serial), date=dated,
                facility=document.facility, customer=document.party,
                customer_name=names.get(document.party), salesperson=document.salesperson,
                product=None, product_code=None, product_name=None, quantity=None,
                amount=document.base.quantize(totals.CENTS, rounding='ROUND_HALF_UP'),
                payment_method=None,
                figures=AnalyticsDetailFigures(
                    values={
                        'balance': str(document.balance),
                        'exchange_rate': str(document.exchange_rate),
                        'due_date': None if document.due_date is None
                        else document.due_date.isoformat(),
                        'days_past_due': str(days),
                        'bucket': accounts_service.bucket(days),
                        'due_bucket': balances.due_bucket(document.due_date, day),
                    }
                ),
            )
        )  # fmt: skip
    total = sum((d.base for d in documents), Decimal(0))
    sums = {'amount': engine.money(balances.cents(total))}
    return AnalyticsDetailResponse(as_of=as_of, items=items, total=len(documents), sums=sums)


# ── Commissions (spec 038) ────────────────────────────────────────────────────


def _rate(value: Any, step: str) -> str:
    return str(Decimal(str(value)).quantize(Decimal(step), rounding='ROUND_HALF_UP'))


def _commission_figures(entry: commissions.Entry) -> dict[str, str | None]:
    """Everything an agent needs to recompute the line: base × label rate × participation share
    (× the paid share, partial-payment scheme), less what earlier months already paid."""
    x = entry.line

    def text(value: Any) -> str | None:
        return None if value is None else str(value)

    return {
        'month': entry.month.isoformat(),
        'status': entry.status,
        'movement': CommissionMovement(x.movement).name.lower(),
        'scheme': str(x.scheme),
        'sales_order': str(x.sales_order),
        'sales_order_detail': str(x.sales_order_detail),
        'customer_refund_detail': text(x.customer_refund_detail),
        'order_date': x.order_date.isoformat(),
        'paid_date': x.paid_date.isoformat(),
        'product': str(x.product),
        'label': text(x.label),
        'label_name': x.label_name,
        'label_rate': _rate(x.label_rate, '0.000001'),
        'participation': str(x.participation),
        'participation_name': x.participation_name,
        'participation_rate': _rate(x.participation_rate, '0.000001'),
        'rate': _rate(x.rate, '0.00000001'),
        'price': _rate(x.price, '0.01'),
        'base': _rate(x.total, '0.01'),
        'share': None if x.share is None else _rate(x.share, '0.000001'),
        'full_commission': _rate(x.full_commission, '0.01'),
        'amount': _rate(x.commission, '0.01'),
    }


async def _commission_lines(
    db: AsyncSession,
    request: AnalyticsDetailRequest,
    facility: int | None,
    as_of: datetime,
    *,
    everything: bool,
) -> AnalyticsDetailResponse:
    """Spec 025's lines of the months the period touches: the lines the commission fact sums, so
    `sums.amount` is the commission, `sums.base` its base and `total` the line count (R11)."""
    found = commissions.selected(
        await commissions.entries(db, request.start, request.end, facility),
        engine.parse_filters(request.filters),
        Grain.MONTH,
    )
    name, descending = parse_sort(request.sort)
    value = {
        'date': lambda e: e.line.paid_date,
        'amount': lambda e: e.line.commission,
        'document': lambda e: e.line.sales_order,
    }[name]
    found.sort(
        key=lambda e: (
            e.month, e.line.sales_order, e.line.sales_order_detail, e.line.salesperson,
            e.line.participation, e.line.movement, e.line.customer_refund_detail or 0,
        ),
        reverse=descending,
    )  # fmt: skip
    found.sort(key=value, reverse=descending)
    shown = found if everything else found[request.skip : request.skip + request.limit]
    folios = await _folios(db, DetailKind.ORDERS, [e.line.sales_order for e in shown])
    codes: dict[int, str] = {}
    if shown:
        rows = await db.execute(
            select(Product.product_id, Product.code).where(
                Product.product_id.in_({e.line.product for e in shown})
            )
        )
        codes = dict(rows.all())
    items = [
        AnalyticsDetailItem(
            kind=DetailKind.COMMISSION_LINES, id=e.line.sales_order_detail,
            document_id=e.line.sales_order, folio=folios.get(e.line.sales_order),
            date=e.line.paid_date, facility=e.facility, customer=e.line.customer,
            customer_name=e.line.customer_name, salesperson=e.line.salesperson,
            product=e.line.product, product_code=codes.get(e.line.product),
            product_name=e.line.product_name, quantity=engine.quantity(e.line.quantity),
            amount=engine.money(commissions.cents(e.line.commission)), payment_method=None,
            figures=AnalyticsDetailFigures(values=_commission_figures(e)),
        )
        for e in shown
    ]  # fmt: skip
    sums = {
        'amount': engine.money(sum(commissions.cents(e.line.commission) for e in found)),
        'base': engine.money(sum(commissions.cents(e.line.total) for e in found)),
    }
    return AnalyticsDetailResponse(as_of=as_of, items=items, total=len(found), sums=sums)
