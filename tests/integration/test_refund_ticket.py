"""A confirmed refund prints its receipt (#287); an open or cancelled one doesn't."""

import io
import re

import pytest
from httpx import AsyncClient
from pypdf import PdfReader
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.test_refund_payout import URL, _confirm, _draft, session  # noqa: F401
from tests.problem import assert_problem


async def _text(client: AsyncClient, refund_id: int) -> str:
    response = await client.get(f'{URL}/{refund_id}/ticket')
    assert response.status_code == 200, response.text
    assert response.headers['content-type'] == 'application/pdf'
    assert f'devolucion-{refund_id:08d}.pdf' in response.headers['content-disposition']
    pages = PdfReader(io.BytesIO(response.content)).pages
    assert len(pages) == 1
    return ' '.join(pages[0].extract_text().split())


@pytest.mark.usefixtures('session')
async def test_a_store_credit_refund_prints_the_notes_ticket_number(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    refund = await _confirm(client, db, 'credit_note')

    text = await _text(client, refund['customer_refund_id'])

    assert 'Ticket de Devolución' in text
    assert f'{refund["serial"]:08d}' in text
    assert f'{refund["sales_order"]:08d}' in text
    line = refund['lines'][0]
    assert line['product_name'] in text and line['product_code'] in text
    assert re.search(r'Total \$[\d,]+\.\d\d', text), text
    assert 'Reembolso Nota de Crédito' in text
    assert f'Número de Ticket {refund["customer_payment"]:08d}' in text


@pytest.mark.usefixtures('session')
async def test_a_cash_refund_prints_its_own_id(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    refund = await _confirm(client, db, 'cash')

    text = await _text(client, refund['customer_refund_id'])

    assert 'Reembolso Efectivo' in text
    assert 'Número de Ticket' not in text
    assert f'{refund["customer_refund_id"]:08d}' in text


@pytest.mark.usefixtures('session')
@pytest.mark.parametrize('cancelled', [False, True], ids=['open', 'cancelled'])
async def test_an_unconfirmed_refund_has_no_receipt(
    client: AsyncClient,
    db: AsyncSession,
    cancelled: bool,
) -> None:
    refund_id = await _draft(client, db)
    if cancelled:
        done = await client.post(f'{URL}/{refund_id}/cancel')
        assert done.status_code == 200, done.text

    response = await client.get(f'{URL}/{refund_id}/ticket')

    body = assert_problem(response, 409, 'refund.not_confirmed')
    assert 'params' not in body or not body['params']


@pytest.mark.usefixtures('session')
async def test_an_unknown_refund_is_404(client: AsyncClient) -> None:
    response = await client.get(f'{URL}/999999/ticket')

    assert_problem(response, 404, 'resource.not_found')
