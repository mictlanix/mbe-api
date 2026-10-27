"""Accounts payable over real rows (spec 033, US3).

The same view as receivables, built on spec 032's supplier balances, company-wide, and empty until
go-live sets the balance cutoff.
"""

from datetime import datetime, timedelta
from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import local_now
from app.models.inventory import InventoryReceipt
from app.models.purchases import PurchaseOrderDetail, SupplierBalanceCutoff
from app.models.supplier import Supplier
from tests.integration import inventory_seed as seed

URL = '/api/v1/accounts-payable'
RECEIVED = (local_now() - timedelta(days=45)).replace(microsecond=0)


async def _setup(db: AsyncSession, *, cutoff: datetime | None = seed.WHEN) -> tuple[int, int]:
    vendor = await seed.supplier(db)
    await db.execute(update(Supplier).where(Supplier.supplier_id == vendor).values(credit_days=30))
    product = await seed.product(db, code='AP1')
    db.add(SupplierBalanceCutoff(supplier_balance_cutoff_id=1, cutoff=cutoff))
    await db.commit()
    return vendor, product


async def _po(db: AsyncSession, vendor: int, product: int, total: str) -> int:
    order, _ = await seed.purchase_order(
        db, lines=[(product, '1', 1)], supplier=vendor, price=total
    )
    await db.execute(
        update(PurchaseOrderDetail)
        .where(PurchaseOrderDetail.purchase_order == order)
        .values(tax_rate=Decimal(0))
    )
    await db.commit()
    return order


async def _receipt(db: AsyncSession, order: int, at: datetime) -> int:
    receipt = InventoryReceipt(
        facility=1, warehouse=1, purchase_order=order, creator=1, updater=1, creation_time=at,
        modification_time=at, completed=True, cancelled=False, serial=12,
    )  # fmt: skip
    db.add(receipt)
    await db.commit()
    return receipt.inventory_receipt_id


async def test_nothing_shows_until_the_cutoff_is_set(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    """US3 scenario 3: go-live sets the cutoff; until then balances don't count."""
    vendor, product = await _setup(db, cutoff=None)
    await _po(db, vendor, product, '1000')

    response = await client.get(URL)

    assert response.status_code == 200, response.text
    assert response.json()['items'] == []
    assert response.json()['cutoff'] is None


async def test_each_supplier_agrees_with_its_spec_032_balance(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    """SC-003 in miniature, with a payment applied in part and credit left over."""
    vendor, product = await _setup(db)
    received = await _po(db, vendor, product, '8000')
    await _receipt(db, received, RECEIVED)
    unreceived = await _po(db, vendor, product, '2000')
    payment = await client.post(
        '/api/v1/supplier-payments', json={'supplier': vendor, 'amount': '4000', 'method': 3}
    )
    await client.post(
        f'/api/v1/supplier-payments/{payment.json()["supplier_payment_id"]}/applications',
        json={'applications': [{'purchase_order': received, 'amount': '3000'}]},
    )

    summary = await client.get(URL)
    balance = await client.get(f'/api/v1/supplier-payments/suppliers/{vendor}/balance')

    (row,) = summary.json()['items']
    for key in ('owed', 'credit', 'net'):
        assert Decimal(row[key]) == Decimal(balance.json()[key]), key
    assert row['owed'] == '7000.00'
    assert row['overdue'] == '5000.00', 'received 45 days ago with 30 days of credit'
    assert row['buckets']['1-30'] == '5000.00'
    assert row['buckets']['current'] == '2000.00', 'the PO not yet received (scenario 2)'
    assert sum(Decimal(v) for v in row['buckets'].values()) == Decimal(row['owed'])

    detail = await client.get(f'{URL}/{vendor}')

    assert detail.status_code == 200, detail.text
    first, second = detail.json()['documents']
    assert (first['id'], second['id']) == (received, unreceived)
    assert first['serial'] is None and first['facility'] is None
    assert first['salesperson'] is None
    assert first['linked'][0]['label'] == 'F1-12'
    assert (first['days_past_due'], first['bucket']) == (15, '1-30')
    assert (first['deductions'], first['applied'], first['balance']) == (
        '0.00',
        '3000.00',
        '5000.00',
    )
    assert second['due_date'] is None and second['bucket'] == 'current'
    assert [p['unapplied'] for p in detail.json()['payments']] == ['1000.00']


async def test_an_unknown_supplier_is_404(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    await _setup(db)

    assert (await client.get(f'{URL}/999')).status_code == 404
