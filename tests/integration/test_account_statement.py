"""The account statement over real rows (spec 033, US5, FR-023).

One template for both sides, always covering every facility: it goes to the party, so it must be
complete whatever the caller's scope.
"""

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app import rendering
from app.models.purchases import PurchaseOrderDetail, SupplierBalanceCutoff
from app.models.sales import CustomerPayment
from app.models.supplier import Supplier
from app.rendering.formatting import money
from app.services import accounts_service, print_contexts
from tests.integration import inventory_seed as seed
from tests.integration.seed import seed_facility
from tests.integration.test_accounts_receivable_flow import NOW, PAST, _customer, _order, _payment


async def test_a_customer_statement_covers_every_facility(db: AsyncSession, seeded: None) -> None:
    await seed_facility(db)
    await _customer(db, 21, limit='50000')
    await _order(db, 21, '30000', due=PAST)
    elsewhere, _ = await _order(db, 21, '10000', facility=2)
    await _payment(db, 21, '1000')

    template, context = await print_contexts.account_statement_context(
        db, accounts_service.CUSTOMERS, 21, facility=1
    )

    detail = await accounts_service.detail(db, accounts_service.CUSTOMERS, 21, facility=None)
    summary = detail['summary']
    assert template == 'account_statement.html'
    assert (context['party_label'], context['linked_label']) == ('Cliente', 'Facturas')
    assert context['party'] == 'Cliente 21' and context['sales_agent'] == 'Ana Ruiz'
    assert len(context['documents']) == 2, 'facility 2 included'
    for key in ('owed', 'overdue', 'credit', 'net', 'available_credit'):
        assert context[key] == money(summary[key]), key
    assert [b['amount'] for b in context['buckets']] == [
        money(v) for v in summary['buckets'].values()
    ]
    assert context['credit_limit'] == money(Decimal('50000'))
    assert elsewhere in {d['id'] for d in detail['documents']}


async def test_payments_of_the_last_90_days(db: AsyncSession, seeded: None) -> None:
    await _customer(db, 21, limit='50000')
    order, _ = await _order(db, 21, '30000', due=PAST)
    recent = await _payment(db, 21, '1000', applied=((order, '600'),), change='100')
    old = await _payment(db, 21, '500')
    await db.execute(
        update(CustomerPayment)
        .where(CustomerPayment.customer_payment_id == old)
        .values(date=NOW - timedelta(days=91))
    )
    await db.commit()

    _, context = await print_contexts.account_statement_context(
        db, accounts_service.CUSTOMERS, 21, facility=1
    )

    (payment,) = context['payments']  # 100 of the 1,000 was handed back as change
    assert payment['id'] == recent
    assert (payment['amount'], payment['applied'], payment['unapplied']) == (
        money(Decimal('1000')),
        money(Decimal('600')),
        money(Decimal('300')),
    )


async def test_a_supplier_statement_uses_supplier_wording(db: AsyncSession, seeded: None) -> None:
    vendor = await seed.supplier(db)
    await db.execute(update(Supplier).where(Supplier.supplier_id == vendor).values(credit_days=30))
    product = await seed.product(db, code='ST1')
    db.add(SupplierBalanceCutoff(supplier_balance_cutoff_id=1, cutoff=seed.WHEN))
    await db.commit()
    order, _ = await seed.purchase_order(
        db, lines=[(product, '1', 1)], supplier=vendor, price='800'
    )
    await db.execute(
        update(PurchaseOrderDetail)
        .where(PurchaseOrderDetail.purchase_order == order)
        .values(tax_rate=Decimal(0))
    )
    await db.commit()

    _, context = await print_contexts.account_statement_context(
        db, accounts_service.SUPPLIERS, vendor, facility=1
    )

    assert (context['party_label'], context['linked_label']) == ('Proveedor', 'Recepciones')
    assert context['sales_agent'] is None
    assert context['owed'] == money(Decimal('800'))
    assert context['documents'][0]['number'] == str(order)


async def test_a_customer_with_only_credit_still_gets_a_statement(
    db: AsyncSession, seeded: None
) -> None:
    await _customer(db, 24, limit='0')
    await _payment(db, 24, '250')

    template, context = await print_contexts.account_statement_context(
        db, accounts_service.CUSTOMERS, 24, facility=1
    )

    assert context['documents'] == []
    assert context['credit'] == money(Decimal('250'))
    assert context['credit_limit'] is None
    pdf = await rendering.render_pdf(template, context)
    assert pdf.startswith(b'%PDF')
