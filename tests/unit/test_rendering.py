"""Document rendering: config, formatters, fetcher and barcode (#230)."""

import base64
import io
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
import segno

from app.core.config import settings
from app.rendering.fetcher import ASSETS_DIR, LocalOnlyFetcher
from app.rendering.formatting import (
    barcode_data_uri,
    code128b_codes,
    date_long,
    date_short,
    date_time,
    fiscal_type_title,
    method_name,
    money,
    pad6,
    pad8,
    payment_form_label,
    payment_method_label,
    payment_type_name,
    percent,
    price4,
    qr_data_uri,
    qty,
    relation_label,
    terms_name,
)


def test_promissory_note_default() -> None:
    text = settings.promissory_note_template.format(
        customer='Ana', balance='$10.00', due_date='2026-10-01', issuer='Mictlanix'
    )
    assert 'Ana' in text
    assert '$10.00' in text
    assert '2026-10-01' in text
    assert 'Mictlanix' in text
    assert '{' not in text
    assert '}' not in text


class TestFormatters:
    def test_money(self) -> None:
        assert money(Decimal('1234.5')) == '$1,234.50'
        assert money(Decimal('-1234.5')) == '-$1,234.50'

    def test_dates(self) -> None:
        moment = datetime(2026, 9, 24, 15, 3)

        assert date_short(moment) == '2026-09-24'
        assert date_time(moment) == '2026-09-24 15:03:00'
        assert date_long(date(2026, 9, 24)) == 'jueves, septiembre 24, 2026'

    def test_qty_trims_trailing_zeros(self) -> None:
        assert qty(Decimal('2.5000')) == '2.5'
        assert qty(Decimal('3')) == '3'

    def test_percent(self) -> None:
        assert percent(Decimal('0.1')) == '10.00 %'

    def test_padded_ids(self) -> None:
        assert pad8(1234) == '00001234'
        assert pad6(12) == '000012'

    def test_enum_names(self) -> None:
        assert method_name(1) == 'Efectivo'
        assert method_name(28) == 'T. de Débito'
        assert method_name(77) == '77'
        assert terms_name(0) == 'Contado'
        assert payment_type_name(2) == 'Pago de Crédito'


class TestFetcher:
    def test_serves_a_bundled_asset_with_its_content_type(self) -> None:
        url = (ASSETS_DIR / 'fonts' / 'OpenSans-Regular.ttf').as_uri()

        response = LocalOnlyFetcher().fetch(url)

        assert response.content_type == 'font/ttf'
        assert response.read()[:4] == b'\x00\x01\x00\x00'

    def test_serves_an_image_from_images_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, 'images_dir', str(tmp_path))
        (tmp_path / 'logo.png').write_bytes(b'png')

        response = LocalOnlyFetcher().fetch((tmp_path / 'logo.png').as_uri())

        assert response.content_type == 'image/png'
        assert response.read() == b'png'

    def test_serves_a_data_uri(self) -> None:
        response = LocalOnlyFetcher().fetch('data:text/plain;base64,aGk=')

        assert response.read() == b'hi'

    @pytest.mark.parametrize(
        'url',
        [
            'http://example.com/x.png',
            'https://example.com/x.png',
            'file:///etc/hosts',
            ASSETS_DIR.as_uri() + '/../fetcher.py',
        ],
    )
    def test_refuses_everything_else(self, url: str) -> None:
        with pytest.raises(ValueError):
            LocalOnlyFetcher().fetch(url)


def test_barcode_is_a_deterministic_svg_data_uri() -> None:
    uri = barcode_data_uri('00001234')

    assert uri.startswith('data:image/svg+xml;base64,')
    assert barcode_data_uri('00001234') == uri


def test_barcode_is_code128_subset_b_like_legacy() -> None:
    """Legacy encodes Code128 in subset A/B only, B for digits (`Code128Content.cs`), so an 8-digit
    id is 8 symbols, not 4 subset-C pairs. The bar pattern must match legacy's."""
    # START B, then P J J 1 2 3 C. Checksum as legacy computes it (`Code128Content.cs:70-75`):
    # 104 + 1*48 + 2*42 + 3*42 + 4*17 + 5*18 + 6*19 + 7*35 = 879, and 879 % 103 = 55.
    assert code128b_codes('PJJ123C') == [104, 48, 42, 42, 17, 18, 19, 35, 55]
    assert code128b_codes('00337416')[:9] == [104, 16, 16, 19, 19, 23, 20, 17, 22]


def test_barcode_has_legacy_geometry() -> None:
    """`MakeBarcodeImage(id, 2, false)`: 2 px per module, no quiet zone, height = ceil(15% width).

    8 characters: ((11 symbols - 3) * 11 + 35) * 2 = 246 px wide, 37 px tall."""
    svg = base64.b64decode(barcode_data_uri('00337416').split(',', 1)[1]).decode()
    assert 'width="246px"' in svg
    assert 'height="37px"' in svg
    # The cash cut's 6-digit id: ((9 - 3) * 11 + 35) * 2 = 202 px, ceil(30.3) = 31 px.
    svg = base64.b64decode(barcode_data_uri('010006').split(',', 1)[1]).decode()
    assert 'width="202px"' in svg
    assert 'height="31px"' in svg


def test_control_characters_are_stripped_from_rendered_values() -> None:
    """Legacy text can carry mojibake with C1 controls (U+009D in a real mbe_dev comment).

    No font has a glyph for them, so WeasyPrint would pull in a host fallback font and the
    output would depend on the machine. Line breaks and tabs are kept.
    """
    from app.rendering import environment

    template = environment.from_string('{{ value }}')
    assert template.render(value='IMPERCON â€œPâ€\x9d\x81') == (
        'IMPERCON â€œPâ€'
    )
    assert template.render(value='a\x00b\x1fc\x7fd') == 'abcd'
    assert template.render(value='line one\r\nline two\tend') == 'line one\r\nline two\tend'
    assert template.render(value=12) == '12'


def test_stripping_keeps_markup_unescaped() -> None:
    from markupsafe import Markup

    from app.rendering import environment

    rendered = environment.from_string('{{ value }}').render(value=Markup('<b>a\x9db</b>'))
    assert rendered == '<b>ab</b>'


# ── CFDI formats (spec 020, research R8) ──────────────────────────────────────


def test_qr_data_uri_is_segno_svg_without_quiet_zone() -> None:
    payload = 'https://verificacfdi.facturaelectronica.sat.gob.mx/default.aspx?id=X&tt=0'
    buffer = io.BytesIO()
    segno.make(payload, error='m').save(
        buffer, kind='svg', xmldecl=False, border=0, omitsize=True
    )
    svg = buffer.getvalue()

    assert b'xmlns="http://www.w3.org/2000/svg"' in svg
    assert b'viewBox=' in svg
    assert qr_data_uri(payload) == 'data:image/svg+xml;base64,' + base64.b64encode(svg).decode()


def test_quantity_follows_legacy_four_decimal_format() -> None:
    assert qty(Decimal('3.0000')) == '3'
    assert qty(Decimal('24.0530')) == '24.053'


@pytest.mark.parametrize(
    ('value', 'expected'),
    [
        ('474.137931', '474.1379'),
        ('1422.41', '1,422.41'),
        ('1698.116207', '1,698.1162'),
        ('0', '.00'),
        ('844827.586207', '844,827.5862'),
    ],
)
def test_price4_follows_legacy_hash_format(value: str, expected: str) -> None:
    """`#,###.00##`: at least two decimals, at most four, and no leading zero."""
    assert price4(Decimal(value)) == expected


@pytest.mark.parametrize(
    ('code', 'title'),
    [
        (0, 'Factura'),
        (100, 'Nota de Crédito'),
        (101, 'Aplicación de Anticipos'),
        (200, 'Recibo Electrónico de Pago'),
        (999, 'CFDI'),
    ],
)
def test_fiscal_type_title(code: int, title: str) -> None:
    assert fiscal_type_title(code) == title


@pytest.mark.parametrize(
    ('code', 'label'),
    [
        ('01', '01 : Nota de Crédito de los Documentos Relacionados'),
        ('04', '04 : Sustitución de los CFDI Previos'),
        ('07', '07 : CFDI por Aplicación de Anticipo'),
        ('03', '03'),
    ],
)
def test_relation_label(code: str, label: str) -> None:
    assert relation_label(code) == label


def test_payment_method_label() -> None:
    assert payment_method_label('PUE') == 'PUE : Pago en una sola exhibición'
    assert payment_method_label('PPD') == 'PPD : Pago en parcialidades o diferido'
    assert payment_method_label(None) == ''


def test_payment_form_label() -> None:
    assert payment_form_label('03') == '03 : Transferencia Electrónica'
    assert payment_form_label('99') == '99 : Por Definir'
    assert payment_form_label(None) == ''
