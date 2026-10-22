"""The quote document, rendered for real (spec 024, FR-002).

Text extraction is the check: every element the legacy `Quotations/Print.cshtml` printed, plus the
payment terms, has to be on the page, and the totals have to be the quote's own.
"""

import io
from decimal import Decimal

import pytest
from httpx import AsyncClient
from pypdf import PdfReader

from app.enums import CurrencyCode
from app.rendering import formatting, words


async def _quote(client: AsyncClient, *, lines: int, confirm: bool, **body: object) -> dict:
    created = await client.post('/api/v1/sales-quotes', json={'customer': 1, **body})
    assert created.status_code == 201, created.text
    quote_id = created.json()['sales_quote_id']
    for n in range(lines):
        line = {'product': 1, 'quantity': '1', 'comment': f'renglon{n}'}
        if n == 0:
            line['discount_rate'] = '0.1'
        added = await client.post(f'/api/v1/sales-quotes/{quote_id}/lines', json=line)
        assert added.status_code == 200, added.text
    if confirm:
        confirmed = await client.post(f'/api/v1/sales-quotes/{quote_id}/confirm')
        assert confirmed.status_code == 200, confirmed.text
    quote = await client.get(f'/api/v1/sales-quotes/{quote_id}')
    return quote.json()


async def _pages(client: AsyncClient, quote_id: int) -> list:
    response = await client.get(f'/api/v1/sales-quotes/{quote_id}/document')
    assert response.status_code == 200, response.text
    assert response.headers['content-type'] == 'application/pdf'
    pages = PdfReader(io.BytesIO(response.content)).pages
    for page in pages:
        assert float(page.mediabox.width) == pytest.approx(612, abs=1)  # Letter
    return pages


def _flat(text: str) -> str:
    return ' '.join(text.split())


@pytest.mark.usefixtures('seeded')
async def test_a_confirmed_quote_carries_every_element(client: AsyncClient) -> None:
    quote = await _quote(
        client, lines=3, confirm=True, payment_terms=1, contact=1, comment='Entrega en obra'
    )

    pages = await _pages(client, quote['sales_quote_id'])
    text = _flat(' '.join(page.extract_text() for page in pages))

    # The layout sets the store name and the title in upper case (`.uppercase`).
    assert 'MATRIZ' in text.upper()
    assert 'Cliente Uno' in text
    assert 'Juan Pérez' in text and '5555555555' in text
    assert f'COTIZACIÓN {quote["sales_quote_id"]}' in text.upper()
    assert quote['date'][:10] in text
    assert quote['due_date'][:10] in text
    assert 'Crédito – 30 días' in text
    assert 'Entrega en obra' in text
    for n in range(3):
        assert f'renglon{n}' in text
    for field in ('subtotal', 'tax_total', 'total'):
        assert formatting.money(Decimal(quote[field])) in text
    in_words = words.amount_in_words(Decimal(quote['total']), CurrencyCode(quote['currency']))
    assert _flat(in_words) in text
    assert 'Elaborada por' in text and 'Ana Ruiz' in text


@pytest.mark.usefixtures('seeded')
async def test_a_cash_quote_says_contado(client: AsyncClient) -> None:
    quote = await _quote(client, lines=1, confirm=True, payment_terms=0)

    text = _flat((await _pages(client, quote['sales_quote_id']))[0].extract_text())

    assert 'Contado' in text
    assert 'días' not in text


@pytest.mark.usefixtures('seeded')
async def test_a_long_draft_paginates(client: AsyncClient) -> None:
    quote = await _quote(client, lines=50, confirm=False, payment_terms=0)

    pages = await _pages(client, quote['sales_quote_id'])

    assert len(pages) > 1
    # The last line lands past the first page; the footer may take a page of its own.
    assert 'renglon49' in ' '.join(page.extract_text() for page in pages[1:])


@pytest.mark.usefixtures('seeded')
async def test_a_cancelled_quote_still_renders(client: AsyncClient) -> None:
    quote = await _quote(client, lines=1, confirm=False, payment_terms=0)
    cancelled = await client.post(f'/api/v1/sales-quotes/{quote["sales_quote_id"]}/cancel')
    assert cancelled.status_code == 200, cancelled.text

    await _pages(client, quote['sales_quote_id'])
