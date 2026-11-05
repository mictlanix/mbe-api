"""The documents behind an aggregate (spec 037, FR-040–FR-042, contracts/api.md "Detail").

Each kind reads the same row subqueries the facts sum (`facts.sale_lines` and the rest), with the
same filters and scope, so its amounts are the aggregate's amounts: a line carries its document's
rounding residual exactly as in the fact. Orders, refunds and credit notes group those lines by
document. `sums` cover every page; which one equals which measure is the contract's table.
"""

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, case, func, literal, null, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.expression import ColumnElement

from app.core.clock import local_now
from app.core.deps import CurrentUser, forbidden, has_any_privilege
from app.core.error_codes import ErrorCode
from app.core.errors import DomainError
from app.enums import AccessRight, SystemObject
from app.models.customer import Customer
from app.models.fiscal import FiscalDocument
from app.models.product import Product
from app.models.sales import CustomerPayment, CustomerRefund, SalesOrder
from app.schemas.analytics import (
    AnalyticsDetailItem,
    AnalyticsDetailRequest,
    AnalyticsDetailResponse,
    DetailFlag,
    DetailKind,
    Grain,
    MeasureKey,
    VatTreatment,
)
from app.services import sales_order_service
from app.services.analytics import engine, facts
from app.services.analytics.catalog import DIMENSIONS, Availability, Fact, Family

KIND_FACT = {
    DetailKind.ORDERS: Fact.SALE_LINE,
    DetailKind.LINES: Fact.SALE_LINE,
    DetailKind.REFUNDS: Fact.REFUND_LINE,
    DetailKind.CREDIT_NOTES: Fact.CREDIT_NOTE,
    DetailKind.PAYMENTS: Fact.PAYMENT,
}
#: The measure a kind's refusals name (`analytics.dimension_not_applicable`).
KIND_MEASURE = {
    DetailKind.ORDERS: MeasureKey.GROSS_SALES,
    DetailKind.LINES: MeasureKey.GROSS_SALES,
    DetailKind.REFUNDS: MeasureKey.REFUNDS,
    DetailKind.CREDIT_NOTES: MeasureKey.CREDIT_NOTES,
    DetailKind.PAYMENTS: MeasureKey.COLLECTED,
}
#: Read on the document kind, besides the family (FR-060).
KIND_PRIVILEGE = {
    DetailKind.ORDERS: SystemObject.SALES_ORDERS,
    DetailKind.LINES: SystemObject.SALES_ORDERS,
    DetailKind.REFUNDS: SystemObject.CUSTOMER_REFUNDS,
    DetailKind.CREDIT_NOTES: SystemObject.FISCAL_DOCUMENTS,
    DetailKind.PAYMENTS: SystemObject.CUSTOMER_PAYMENTS,
}
COST_FLAGS = {DetailFlag.BELOW_COST, DetailFlag.WITHOUT_COST}
SENSITIVE_KEYS = {'cost', 'margin'}
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
) -> tuple[int | None, bool]:
    """Privileges, scope and applicability. Returns the facility scope and cost visibility."""
    engine.validate_period(request.start, request.end)
    held = await engine.families(db, current)
    family = Family.PAYMENTS if request.kind is DetailKind.PAYMENTS else Family.SALES
    if family not in held:
        raise engine.family_forbidden(family)
    if not current.administrator:
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
    return facility, cost


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
    if request.flag is DetailFlag.OVERDUE:
        return await _overdue(db, request, facility, as_of, everything=everything)
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
    out of every item and of `sums`: absent, not null (FR-042, SC-005). Holders keep a null cost on
    an uncosted line, which is why this never uses `exclude_none`."""
    if cost_visible:
        return response.model_dump(mode='json')
    data = response.model_dump(mode='json', exclude={'items': {'__all__': SENSITIVE_KEYS}})
    data['sums'] = {k: v for k, v in data['sums'].items() if k not in SENSITIVE_KEYS}
    return data
