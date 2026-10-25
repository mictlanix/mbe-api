"""The CFDI 4.0 invoice builder, original string and seal (spec 027, research R3–R5).

The formulas are legacy's (`CFDHelpers40.InvoiceToCFDv40`, `ModelHelpers`); the anonymised stamped
invoice in `tests/fixtures/cfdi/invoice.xml` is the reference for both values and formatting.
"""

import base64
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.x509 import load_der_x509_certificate
from lxml import etree

from app.enums import PaymentTerms
from app.services import cfdi_invoice as cfdi
from tests.cfdi_schema import assert_valid_cfdi40
from tests.unit.test_csd_service import _CERTIFICATE_NUMBER, _PASSWORD, _make_csd

_FIXTURE = Path(__file__).parents[1] / 'fixtures' / 'cfdi' / 'invoice.xml'
_NS = {'cfdi': 'http://www.sat.gob.mx/cfd/4'}
_DISCOUNT = Decimal('0.06666667')


def _line(**overrides: object) -> cfdi.InvoiceLineData:
    values: dict[str, object] = {
        'product_service': '31162800',
        'product_code': 'CN05',
        'quantity': Decimal('4.0000'),
        'unit': 'H87',
        'unit_name': 'Pieza',
        'description': 'TUBO CONDUIT PESADO PVC 3 MTS (R-1) VERDE 1 1/2" CN05',
        'price': Decimal('90.0000000'),
        'discount_rate': Decimal('0'),
        'tax_rate': Decimal('0.160000'),
        'tax_included': True,
    }
    values.update(overrides)
    return cfdi.InvoiceLineData(**values)


def _fixture_data() -> cfdi.InvoiceData:
    """The stored rows behind `invoice.xml`, as the database would hold them."""
    alambron = {
        'product_service': '10141605',
        'product_code': 'ESTALBRON1010',
        'unit': 'KGM',
        'unit_name': 'Kilogramo',
        'description': 'ESTRIBO ALAMBRON 10 X 10',
        'price': Decimal('30.0000000'),
        'discount_rate': _DISCOUNT,
    }
    return cfdi.InvoiceData(
        batch='RH',
        serial=11405,
        issued=datetime(2026, 9, 29, 18, 28, 56),
        payment_method=3,
        payment_terms=PaymentTerms.IMMEDIATE,
        issued_location='06000',
        issuer='EKU9003173C9',
        issuer_name='EMPRESA DEMO',
        issuer_regime='601',
        recipient='XAÑ010101AB1',
        recipient_name='CLIENTE DEMO',
        recipient_postal_code='06000',
        recipient_regime='601',
        usage='G03',
        lines=(
            _line(),
            _line(quantity=Decimal('35.0000'), **alambron),
            _line(quantity=Decimal('65.0000'), **alambron),
        ),
    )


def _stored_original_string() -> str:
    root = etree.parse(str(_FIXTURE)).getroot()
    for complement in root.findall('cfdi:Complemento', _NS):
        root.remove(complement)
    return cfdi.original_string(root)


def _attrs(root: etree._Element, path: str) -> list[dict[str, str]]:
    return [dict(e.attrib) for e in root.findall(path, _NS)]


# ── Line formulas ─────────────────────────────────────────────────────────────


def test_tax_included_line_matches_the_fixture() -> None:
    amounts = cfdi.line_amounts(_line())

    assert amounts.unit_value == Decimal('77.586207')
    assert amounts.subtotal == Decimal('310.34')
    assert amounts.discount == Decimal('0')
    assert amounts.total == Decimal('360.00')
    assert amounts.taxes == Decimal('49.66')
    assert cfdi.format_base(amounts.base) == '310.375'


def test_discounted_line_matches_the_fixture() -> None:
    amounts = cfdi.line_amounts(
        _line(quantity=Decimal('65'), price=Decimal('30'), discount_rate=_DISCOUNT)
    )

    assert (amounts.subtotal, amounts.discount, amounts.taxes) == (
        Decimal('1681.03'),
        Decimal('112.07'),
        Decimal('251.04'),
    )
    assert cfdi.format_base(amounts.base) == '1569'


def test_tax_excluded_line_adds_the_tax() -> None:
    amounts = cfdi.line_amounts(
        _line(quantity=Decimal('3'), price=Decimal('10'), tax_included=False)
    )

    assert (amounts.unit_value, amounts.subtotal, amounts.total, amounts.taxes) == (
        Decimal('10'),
        Decimal('30.00'),
        Decimal('34.80'),
        Decimal('4.80'),
    )


def test_zero_rate_base_is_the_net_amount() -> None:
    amounts = cfdi.line_amounts(_line(tax_rate=Decimal('0'), discount_rate=Decimal('0.1')))

    assert amounts.subtotal == Decimal('360.00')
    assert amounts.discount == Decimal('36.00')
    assert amounts.taxes == Decimal('0')
    assert amounts.base == Decimal('324.00')


def test_exempt_line_has_no_tax() -> None:
    amounts = cfdi.line_amounts(_line(tax_rate=Decimal('-1')))

    assert (amounts.subtotal, amounts.taxes, amounts.base) == (
        Decimal('360.00'),
        Decimal('0'),
        Decimal('360.00'),
    )


def test_rounding_is_half_away_from_zero() -> None:
    # 1 × 0.125 tax-excluded: the subtotal 0.125 rounds up to 0.13.
    amounts = cfdi.line_amounts(
        _line(quantity=Decimal('1'), price=Decimal('0.125'), tax_included=False)
    )

    assert amounts.subtotal == Decimal('0.13')


@pytest.mark.parametrize(
    ('value', 'text'),
    [
        (Decimal('251.04') / Decimal('0.160000'), '1569'),
        (Decimal('251.04') / Decimal('0.16'), '1569'),
        (Decimal('49.66') / Decimal('0.160000'), '310.375'),
        (Decimal('360.00') - Decimal('36.00'), '324.00'),
    ],
)
def test_base_keeps_its_natural_scale_without_exponent(value: Decimal, text: str) -> None:
    """C# decimals print at their own scale: a quotient has no trailing zeros, a difference of
    cents keeps two places. Python can return the quotient as `1.569E+3`."""
    assert cfdi.format_base(value) == text


# ── The document ──────────────────────────────────────────────────────────────


def test_rebuilt_fixture_has_the_stored_original_string() -> None:
    """SC-002 in the repository: same data, same original string."""
    root = cfdi.build(
        _fixture_data(), certificate_number='00001000000721949306', certificate='ANONIMIZADO'
    )

    assert cfdi.original_string(root) == _stored_original_string()


def test_rebuilt_fixture_writes_legacy_formatting() -> None:
    root = cfdi.build(_fixture_data(), certificate_number=_CERTIFICATE_NUMBER, certificate='AA==')

    assert root.get('SubTotal') == '2896.54'
    assert root.get('Descuento') == '172.41'
    assert root.get('Total') == '3160.00'
    assert root.get('MetodoPago') == 'PUE'
    assert root.get('FormaPago') == '03'
    assert root.get('TipoCambio') is None
    concepts = _attrs(root, 'cfdi:Conceptos/cfdi:Concepto')
    assert [c['Cantidad'] for c in concepts] == ['4.0000', '35.0000', '65.0000']
    assert 'Descuento' not in concepts[0]
    transfers = _attrs(
        root, 'cfdi:Conceptos/cfdi:Concepto/cfdi:Impuestos/cfdi:Traslados/cfdi:Traslado'
    )
    assert [t['Base'] for t in transfers] == ['310.375', '844.8125', '1569']
    assert {t['TasaOCuota'] for t in transfers} == {'0.160000'}
    assert _attrs(root, 'cfdi:Impuestos') == [{'TotalImpuestosTrasladados': '435.87'}]
    assert _attrs(root, 'cfdi:Impuestos/cfdi:Traslados/cfdi:Traslado') == [
        {
            'Base': '2724.19',
            'Impuesto': '002',
            'TipoFactor': 'Tasa',
            'TasaOCuota': '0.160000',
            'Importe': '435.87',
        }
    ]


def test_ppd_usage_p01_and_blank_code() -> None:
    data = replace(
        _fixture_data(),
        payment_terms=PaymentTerms.NET_D,
        payment_method=99,
        usage='P01',
        lines=(_line(product_code=''),),
    )

    root = cfdi.build(data, certificate_number=_CERTIFICATE_NUMBER, certificate='AA==')

    assert (root.get('MetodoPago'), root.get('FormaPago')) == ('PPD', '99')
    assert root.find('cfdi:Receptor', _NS).get('UsoCFDI') == 'G03'
    assert 'NoIdentificacion' not in root.find('cfdi:Conceptos/cfdi:Concepto', _NS).attrib


def test_rates_are_grouped_and_exempt_has_no_rate() -> None:
    data = replace(
        _fixture_data(),
        lines=(_line(), _line(tax_rate=Decimal('0')), _line(tax_rate=Decimal('-1')), _line()),
    )

    root = cfdi.build(data, certificate_number=_CERTIFICATE_NUMBER, certificate='AA==')

    groups = _attrs(root, 'cfdi:Impuestos/cfdi:Traslados/cfdi:Traslado')
    assert [(g['TipoFactor'], g.get('TasaOCuota'), g.get('Importe')) for g in groups] == [
        ('Tasa', '0.160000', '99.32'),
        ('Tasa', '0.000000', '0.00'),
        ('Exento', None, None),
    ]
    assert groups[2]['Base'] == '360.00'
    assert root.find('cfdi:Impuestos', _NS).get('TotalImpuestosTrasladados') == '99.32'


def test_only_exempt_lines_omit_the_total_transferred() -> None:
    data = replace(_fixture_data(), lines=(_line(tax_rate=Decimal('-1')),))

    root = cfdi.build(data, certificate_number=_CERTIFICATE_NUMBER, certificate='AA==')

    assert root.find('cfdi:Impuestos', _NS).get('TotalImpuestosTrasladados') is None


def test_full_discount_is_not_subject_to_tax() -> None:
    data = replace(_fixture_data(), lines=(_line(discount_rate=Decimal('1')),))

    root = cfdi.build(data, certificate_number=_CERTIFICATE_NUMBER, certificate='AA==')

    concept = root.find('cfdi:Conceptos/cfdi:Concepto', _NS)
    assert concept.get('ObjetoImp') == '01'
    assert concept.find('cfdi:Impuestos', _NS) is None
    assert root.find('cfdi:Impuestos', _NS) is None
    assert root.get('Total') == '0.00'


def test_relations_are_listed_under_their_type() -> None:
    data = replace(
        _fixture_data(),
        relation_type='07',
        relation_uuids=(
            '1F0E7E4A-9F5B-4E59-9A4B-2B7C3A1D2E01',
            '2A4B6C8D-0E1F-4A3B-8C5D-6E7F8A9B0C1D',
        ),
    )

    root = cfdi.build(data, certificate_number=_CERTIFICATE_NUMBER, certificate='AA==')

    related = root.find('cfdi:CfdiRelacionados', _NS)
    assert related.get('TipoRelacion') == '07'
    assert [r.get('UUID') for r in related] == list(data.relation_uuids)
    assert root.index(related) == 0


# ── Sealing ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    'lines',
    [
        None,
        (_line(tax_rate=Decimal('0')), _line(tax_rate=Decimal('-1'))),
        (_line(discount_rate=Decimal('1')), _line()),
        (_line(quantity=Decimal('2.5'), price=Decimal('19.99'), tax_included=False),),
    ],
)
def test_sealed_xml_is_valid_and_its_seal_verifies(lines: tuple | None) -> None:
    """SC-001."""
    certificate_data, key_data = _make_csd()
    data = _fixture_data() if lines is None else replace(_fixture_data(), lines=lines)

    xml = cfdi.seal(
        data,
        certificate_number=_CERTIFICATE_NUMBER,
        certificate_data=certificate_data,
        key_data=key_data,
        key_password=_PASSWORD,
    )

    assert xml.startswith('<?xml version="1.0" encoding="utf-8"?>')
    assert_valid_cfdi40(xml)
    root = etree.fromstring(xml.encode('utf-8'))
    assert root.get('NoCertificado') == _CERTIFICATE_NUMBER
    assert base64.b64decode(root.get('Certificado')) == certificate_data
    load_der_x509_certificate(certificate_data).public_key().verify(
        base64.b64decode(root.get('Sello')),
        cfdi.original_string(root).encode('utf-8'),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )


async def test_seal_async_runs_in_a_thread() -> None:
    certificate_data, key_data = _make_csd()

    xml = await cfdi.seal_async(
        _fixture_data(),
        certificate_number=_CERTIFICATE_NUMBER,
        certificate_data=certificate_data,
        key_data=key_data,
        key_password=_PASSWORD,
    )

    assert_valid_cfdi40(xml)


def test_a_wrong_password_is_refused() -> None:
    certificate_data, key_data = _make_csd()

    with pytest.raises(ValueError, match='password'):
        cfdi.seal(
            _fixture_data(),
            certificate_number=_CERTIFICATE_NUMBER,
            certificate_data=certificate_data,
            key_data=key_data,
            key_password=b'wrong',
        )


# ── C# decimal scale (found by SC-002 on mbe_dev) ─────────────────────────────


def test_an_exact_division_keeps_the_scale_c_sharp_gives_it() -> None:
    """Invoice ACH6216: 145.0000000 / 1.160000 is written `125.0`, not `125.000000`."""
    amounts = cfdi.line_amounts(_line(price=Decimal('145.0000000')))

    assert cfdi.format_base(amounts.unit_value) == '125.0'


def test_rounding_never_adds_decimal_places() -> None:
    """Invoice GR4776: the summary transfer's `Base` is `1250`, rounded but not padded."""
    data = replace(
        _fixture_data(),
        lines=(
            _line(
                quantity=Decimal('3.0000'),
                price=Decimal('550.0000000'),
                discount_rate=Decimal('0.12121212'),
            ),
        ),
    )

    root = cfdi.build(data, certificate_number=_CERTIFICATE_NUMBER, certificate='AA==')

    [summary] = _attrs(root, 'cfdi:Impuestos/cfdi:Traslados/cfdi:Traslado')
    assert (summary['Base'], summary['Importe']) == ('1250', '200.00')
    assert root.find('cfdi:Impuestos', _NS).get('TotalImpuestosTrasladados') == '200.00'
