"""HTML templates to PDF with WeasyPrint, in-process (#230).

Three rules hold for every render:

- **Zero network.** Every URL goes through `LocalOnlyFetcher`, which serves only `data:` URIs,
  the bundled `static/` assets and files under `settings.images_dir`, and refuses everything else.
- **Off the event loop.** Layout runs in a worker thread behind a single-slot limiter: rendering
  holds the GIL, so more slots add latency, not throughput, and one slot also means the shared
  `FontConfiguration` is never used by two renders at once (research R3).
- **Never persisted.** The PDF is built in memory and returned; nothing is written to disk.

The `weasyprint` logger keeps its default level on purpose: a font, stylesheet or image that fails
to load must surface as a warning (FR-003). A clean render logs nothing.
"""

import re
from pathlib import Path

import anyio
from fastapi import Response
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from weasyprint import HTML
from weasyprint.text.fonts import FontConfiguration

from app.rendering import formatting, words
from app.rendering.fetcher import ASSETS_DIR, LocalOnlyFetcher

#: C0/C1 control characters other than tab, LF and CR. Legacy text carries some as mojibake
#: (U+009D in a real mbe_dev comment); no font has a glyph for them, so leaving them in would pull
#: a host fallback font into the PDF and make the output depend on the machine.
_CONTROL_CHARS = re.compile('[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]')


def _strip_control_chars(value: object) -> object:
    if not isinstance(value, str):
        return value
    # Keep `Markup` as `Markup`: `re.sub` returns a plain str, which autoescape would escape again.
    return type(value)(_CONTROL_CHARS.sub('', value))


environment = Environment(
    loader=FileSystemLoader(Path(__file__).parent / 'templates'),
    autoescape=True,
    undefined=StrictUndefined,
    finalize=_strip_control_chars,
)
environment.filters.update(
    {
        name: getattr(formatting, name)
        for name in (
            'money', 'date_short', 'date_time', 'date_long', 'qty', 'percent', 'pad8', 'pad6',
            'method_name', 'terms_name', 'payment_type_name', 'barcode_data_uri',
        )
    }
)  # fmt: skip
environment.filters['amount_in_words'] = words.amount_in_words

_FONT_CONFIG = FontConfiguration()
_FETCHER = LocalOnlyFetcher()
_RENDER_LIMITER = anyio.CapacityLimiter(1)

PDF_RESPONSE = {
    200: {'content': {'application/pdf': {'schema': {'type': 'string', 'format': 'binary'}}}}
}


def _write(html: str) -> bytes:
    document = HTML(string=html, base_url=f'{ASSETS_DIR}/', url_fetcher=_FETCHER).render(
        font_config=_FONT_CONFIG
    )
    page = document.pages[0]
    # A ticket is laid out on a tall probe page, then cut to where its content ends (research R8).
    if 'ticket-end' in page.anchors:
        page.height = page.anchors['ticket-end'][1]
    return document.write_pdf()


async def render_pdf(template: str, context: dict) -> bytes:
    html = environment.get_template(template).render(context)
    return await anyio.to_thread.run_sync(_write, html, limiter=_RENDER_LIMITER)


def pdf_response(content: bytes, filename: str) -> Response:
    return Response(
        content,
        media_type='application/pdf',
        headers={'Content-Disposition': f'inline; filename="{filename}"'},
    )
