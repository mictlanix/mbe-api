"""Expense vouchers through their routes, against the cash cut (spec 032, US1–US3).

US1 (T010): a confirmed voucher lowers the cut, drafts and cancelled drafts don't, and a draft from
a closed session lands in the caller's next one. US2 (T017): a void gives the amount back, and a
closed session's figures never change. US3: the ticket renders.
"""

from decimal import Decimal

from sqlalchemy import select

from app.enums import SourceType
from app.models.core import Expense
from app.models.incidence import Incidence
from app.services import cash_session_service

URL = '/api/v1/expense-vouchers'


async def _open(client, amount: str = '1000') -> int:
    response = await client.post(
        '/api/v1/cash-sessions', json={'cash_drawer': 1, 'opening_amount': amount}
    )
    assert response.status_code == 201, response.text
    return response.json()['cash_session_id']


async def _close(client, session: int) -> None:
    response = await client.post(f'/api/v1/cash-sessions/{session}/close', json={'counts': []})
    assert response.status_code == 200, response.text


async def _category(db, name: str = 'VARIOS') -> int:
    expense = Expense(expense=name, comment=f'Nota de {name}')
    db.add(expense)
    await db.commit()
    return expense.expense_id


async def _voucher(client, db, amounts: list[str]) -> int:
    response = await client.post(URL, json={})
    assert response.status_code == 201, response.text
    voucher = response.json()['expense_voucher_id']
    category = await _category(db)
    for amount in amounts:
        response = await client.post(
            f'{URL}/{voucher}/lines', json={'expense': category, 'amount': amount}
        )
        assert response.status_code == 200, response.text
    return voucher


async def _cut(db, session: int):  # noqa: ANN202
    return await cash_session_service.cut_figures(db, session)


# ── US1 ───────────────────────────────────────────────────────────────────────


async def test_a_confirmed_voucher_lowers_the_cut(client, db, seeded) -> None:
    session = await _open(client)
    voucher = await _voucher(client, db, ['100.50', '49.50'])

    response = await client.post(f'{URL}/{voucher}/confirm')

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body['status'], body['voided'], body['cash_session']) == ('completed', False, session)
    assert (body['total'], body['line_count'], body['creator_name']) == ('150.00', 2, 'Ana Ruiz')
    assert [line['comment'] for line in body['lines']] == ['Nota de VARIOS'] * 2
    figures = await _cut(db, session)
    assert figures.expenses_total == Decimal('150.00')
    assert figures.cash_in_drawer == Decimal('850.00')


async def test_drafts_and_cancelled_drafts_do_not_count(client, db, seeded) -> None:
    session = await _open(client)
    await _voucher(client, db, ['10'])
    cancelled = await _voucher(client, db, ['20'])

    response = await client.post(f'{URL}/{cancelled}/cancel')

    assert response.status_code == 200, response.text
    assert (response.json()['status'], response.json()['voided']) == ('cancelled', False)
    figures = await _cut(db, session)
    assert figures.expenses_total == Decimal(0)
    assert figures.cash_in_drawer == Decimal('1000.00')


async def test_a_draft_from_a_closed_session_lands_in_the_next(client, db, seeded) -> None:
    first = await _open(client)
    voucher = await _voucher(client, db, ['30'])
    await _close(client, first)
    second = await _open(client)

    response = await client.post(f'{URL}/{voucher}/confirm')

    assert response.status_code == 200, response.text
    assert response.json()['cash_session'] == second
    assert (await _cut(db, first)).expenses_total == Decimal(0)
    assert (await _cut(db, second)).expenses_total == Decimal('30.00')


# ── US2 ───────────────────────────────────────────────────────────────────────


async def test_a_void_gives_the_amount_back_to_the_cut(client, db, seeded) -> None:
    session = await _open(client)
    voucher = await _voucher(client, db, ['75'])
    await client.post(f'{URL}/{voucher}/confirm')
    assert (await _cut(db, session)).expenses_total == Decimal('75.00')

    response = await client.post(f'{URL}/{voucher}/void', json={'reason': 'Capturado dos veces'})

    assert response.status_code == 200, response.text
    assert (response.json()['status'], response.json()['voided']) == ('cancelled', True)
    assert (await _cut(db, session)).expenses_total == Decimal(0)
    entry = (
        await db.execute(
            select(Incidence).where(
                Incidence.source == int(SourceType.EXPENSE_VOUCHER),
                Incidence.instance_id == voucher,
            )
        )
    ).scalar_one()
    assert (entry.comment, entry.content) == ('Capturado dos veces', 'Expense voucher voided')


async def test_a_closed_session_refuses_void_and_confirm(client, db, seeded) -> None:
    session = await _open(client)
    confirmed = await _voucher(client, db, ['40'])
    await client.post(f'{URL}/{confirmed}/confirm')
    draft = await _voucher(client, db, ['60'])
    await _close(client, session)
    at_close = await _cut(db, session)

    voided = await client.post(f'{URL}/{confirmed}/void', json={'reason': 'error'})
    confirmed_late = await client.post(f'{URL}/{draft}/confirm')

    assert voided.status_code == 409
    assert voided.json()['detail'] == 'The cash session is closed'
    assert confirmed_late.status_code == 409
    assert confirmed_late.json()['detail'] == 'An open cash session is required'
    assert await _cut(db, session) == at_close


# ── US3 ───────────────────────────────────────────────────────────────────────


async def test_the_ticket_renders_for_a_confirmed_voucher_only(client, db, seeded) -> None:
    await _open(client)
    voucher = await _voucher(client, db, ['12.34'])

    draft = await client.get(f'{URL}/{voucher}/ticket')
    await client.post(f'{URL}/{voucher}/confirm')
    printed = await client.get(f'{URL}/{voucher}/ticket')

    assert draft.status_code == 409
    assert printed.status_code == 200, printed.text
    assert printed.headers['content-type'] == 'application/pdf'
    assert printed.content.startswith(b'%PDF')
