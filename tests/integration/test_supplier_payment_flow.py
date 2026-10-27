"""Supplier payments end to end (spec 032, US4–US6): record, apply, reverse, cancel, the balances,
and cash payments in the cut.

Concurrent applications (SC-003) and serials (SC-004) are checked on the dev deployment
(quickstart.md); SQLite has no row locks.
"""

import io
from decimal import Decimal

from pypdf import PdfReader
from sqlalchemy import update

from app.models.core import Expense
from app.models.purchases import (
    ExpenseVoucher,
    ExpenseVoucherDetail,
    PurchaseOrderDetail,
    SupplierBalanceCutoff,
)
from app.models.supplier import SupplierPayment
from tests.integration import inventory_seed as seed

URL = '/api/v1/supplier-payments'
EFT, CASH = 3, 1


async def _setup(db, *, cutoff=seed.WHEN):  # noqa: ANN001, ANN202
    """A supplier, the cutoff row, and an untaxed product to put on its POs."""
    vendor = await seed.supplier(db)
    product = await seed.product(db, code='SP1')
    db.add(SupplierBalanceCutoff(supplier_balance_cutoff_id=1, cutoff=cutoff))
    await db.commit()
    return vendor, product


async def _po(db, vendor: int, product: int, total: str) -> int:  # noqa: ANN001
    """A PO confirmed at the cutoff, with one untaxed line of `total`."""
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


async def _pay(client, vendor: int, amount: str, **body):  # noqa: ANN001, ANN202
    response = await client.post(
        URL, json={'supplier': vendor, 'amount': amount, 'method': EFT} | body
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _po_balance(client, order: int) -> Decimal:  # noqa: ANN001
    response = await client.get(f'/api/v1/purchase-orders/{order}/payments')
    assert response.status_code == 200, response.text
    return Decimal(response.json()['balance'])


async def _expected_cash(client, session: int) -> Decimal:  # noqa: ANN001
    response = await client.get(f'/api/v1/cash-sessions/{session}')
    assert response.status_code == 200, response.text
    return Decimal(response.json()['expected_cash'])


async def test_pay_apply_and_read_the_balances(client, db, seeded):  # noqa: ANN001
    """The spec's Independent Test for US4, then US6's views of it."""
    vendor, product = await _setup(db)
    first, second = await _po(db, vendor, product, '6000'), await _po(db, vendor, product, '8000')

    payment = await _pay(client, vendor, '10000', reference='SPEI 1')
    assert (payment['facility'], payment['serial'], payment['status']) == (1, 1, 'completed')
    assert (Decimal(payment['applied']), Decimal(payment['credit'])) == (0, Decimal('10000'))

    response = await client.post(
        f'{URL}/{payment["supplier_payment_id"]}/applications',
        json={
            'applications': [
                {'purchase_order': first, 'amount': '6000'},
                {'purchase_order': second, 'amount': '3000'},
            ]
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert (Decimal(body['applied']), Decimal(body['credit'])) == (Decimal(9000), Decimal(1000))
    assert [(a['purchase_order'], Decimal(a['amount'])) for a in body['applications']] == [
        (first, Decimal(6000)),
        (second, Decimal(3000)),
    ]
    assert (await _po_balance(client, first), await _po_balance(client, second)) == (0, 5000)

    response = await client.get(f'{URL}/suppliers/{vendor}/balance')
    assert response.status_code == 200, response.text
    summary = response.json()
    assert [o['purchase_order_id'] for o in summary['orders']] == [second]
    assert [p['supplier_payment_id'] for p in summary['payments']] == [
        payment['supplier_payment_id']
    ]
    assert [Decimal(summary[k]) for k in ('owed', 'credit', 'net')] == [5000, 1000, 4000]

    # Over the PO's balance, and over the payment's credit.
    over = {'applications': [{'purchase_order': second, 'amount': '1000.01'}]}
    response = await client.post(f'{URL}/{payment["supplier_payment_id"]}/applications', json=over)
    assert response.status_code == 409


async def test_create_with_applications_in_one_call(client, db, seeded):  # noqa: ANN001
    vendor, product = await _setup(db)
    order = await _po(db, vendor, product, '6000')

    payment = await _pay(
        client, vendor, '7000', applications=[{'purchase_order': order, 'amount': '6000'}]
    )

    assert Decimal(payment['credit']) == 1000
    assert await _po_balance(client, order) == 0
    fetched = (await client.get(f'{URL}/{payment["supplier_payment_id"]}')).json()
    assert fetched == payment


async def test_a_cash_payment_and_a_voucher_leave_the_cut_until_cancelled(client, db, seeded):  # noqa: ANN001
    vendor, _ = await _setup(db)
    response = await client.post(
        '/api/v1/cash-sessions', json={'cash_drawer': 1, 'opening_amount': '500'}
    )
    session = response.json()['cash_session_id']
    category = Expense(expense='Papelería')
    db.add(category)
    await db.flush()
    voucher = ExpenseVoucher(
        creator=1,
        updater=1,
        facility=1,
        cash_session=session,
        date=seed.WHEN,
        creation_time=seed.WHEN,
        modification_time=seed.WHEN,
        completed=True,
        cancelled=False,
    )
    db.add(voucher)
    await db.flush()
    db.add(
        ExpenseVoucherDetail(
            expense_voucher=voucher.expense_voucher_id, expense=category.expense_id, amount=50
        )
    )
    await db.commit()
    assert await _expected_cash(client, session) == 450

    payment = await _pay(client, vendor, '200', method=CASH)
    assert (payment['cash_session'], payment['facility']) == (session, 1)
    assert await _expected_cash(client, session) == 250

    response = await client.post(
        f'{URL}/{payment["supplier_payment_id"]}/cancel', json={'reason': 'wrong supplier'}
    )
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'cancelled'
    assert await _expected_cash(client, session) == 450


async def test_the_cut_ticket_prints_cash_paid_to_suppliers(client, db, seeded):  # noqa: ANN001
    vendor, _ = await _setup(db)
    response = await client.post(
        '/api/v1/cash-sessions', json={'cash_drawer': 1, 'opening_amount': '500'}
    )
    session = response.json()['cash_session_id']
    await _pay(client, vendor, '200', method=CASH)
    response = await client.post(f'/api/v1/cash-sessions/{session}/close', json={'counts': []})
    assert response.status_code == 200, response.text

    response = await client.get(f'/api/v1/cash-sessions/{session}/ticket')

    assert response.status_code == 200, response.text
    text = ' '.join(PdfReader(io.BytesIO(response.content)).pages[0].extract_text().split())
    assert 'Pagos a proveedores : $200.00' in text, text


async def test_serials_per_facility(client, db, seeded, current_user):  # noqa: ANN001
    vendor, _ = await _setup(db)
    other = (await seed.setup_inventory(db)).b.facility

    serials = [(p['facility'], p['serial']) for p in [await _pay(client, vendor, '1')] * 1]
    serials += [((p := await _pay(client, vendor, '1'))['facility'], p['serial']) for _ in '12']
    current_user.facility_id = None
    serials.append(
        ((p := await _pay(client, vendor, '1', facility=other))['facility'], p['serial'])
    )

    assert serials == [(1, 1), (1, 2), (1, 3), (other, 1)]


async def test_reverse_then_cancel_restores_every_balance(client, db, seeded):  # noqa: ANN001
    vendor, product = await _setup(db)
    first, second = await _po(db, vendor, product, '6000'), await _po(db, vendor, product, '8000')
    payment = await _pay(
        client,
        vendor,
        '10000',
        applications=[
            {'purchase_order': first, 'amount': '6000'},
            {'purchase_order': second, 'amount': '3000'},
        ],
    )
    pid = payment['supplier_payment_id']
    application = payment['applications'][1]['purchase_order_payment_id']

    response = await client.post(
        f'{URL}/{pid}/applications/{application}/reverse', json={'reason': 'wrong PO'}
    )
    assert response.status_code == 200, response.text
    assert Decimal(response.json()['credit']) == 4000
    assert await _po_balance(client, second) == 8000
    response = await client.post(
        f'{URL}/{pid}/applications/{application}/reverse', json={'reason': 'again'}
    )
    assert response.status_code == 409

    response = await client.post(f'{URL}/{pid}/cancel', json={'reason': 'duplicated'})
    assert response.status_code == 200, response.text
    assert (await _po_balance(client, first), await _po_balance(client, second)) == (6000, 8000)
    assert Decimal(response.json()['credit']) == 0
    assert (await client.post(f'{URL}/{pid}/cancel', json={'reason': 'x'})).status_code == 409


async def test_list_filters(client, db, seeded):  # noqa: ANN001
    vendor, _ = await _setup(db)
    kept = await _pay(client, vendor, '10')
    gone = await _pay(client, vendor, '20', method=2)
    await client.post(f'{URL}/{gone["supplier_payment_id"]}/cancel', json={'reason': 'x'})
    db.add(SupplierPayment(supplier=vendor, amount=5, method=EFT, date=seed.WHEN, creator=1))
    await db.commit()

    async def ids(**params) -> list[int]:  # noqa: ANN003
        response = await client.get(URL, params=params)
        assert response.status_code == 200, response.text
        return [item['supplier_payment_id'] for item in response.json()['items']]

    everything = await ids(supplier=vendor)
    assert everything[1:] == [gone['supplier_payment_id'], kept['supplier_payment_id']]
    assert len(everything) == 3
    assert await ids(status='cancelled') == [gone['supplier_payment_id']]
    assert await ids(method=EFT, facility=1) == [kept['supplier_payment_id']]
    assert await ids(**{'from': '2026-10-02'}) == [
        gone['supplier_payment_id'],
        kept['supplier_payment_id'],
    ]
    assert await ids(to='2026-10-01') == [everything[0]]
    assert await ids(skip=1, limit=1) == [gone['supplier_payment_id']]
    assert (await client.get(URL, params={'status': 'draft'})).status_code == 422
