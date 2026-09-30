"""Reading a stamped CFDI for its printed representation (#230, spec 020)."""

from pathlib import Path

import pytest

from app.services import cfdi

_FIXTURES = Path(__file__).parent.parent / 'fixtures' / 'cfdi'


def _xml(name: str) -> str:
    return (_FIXTURES / f'{name}.xml').read_text(encoding='utf-8')


# ── parse ─────────────────────────────────────────────────────────────────────


def test_parse_reads_an_invoice_verbatim() -> None:
    doc = cfdi.parse(_xml('invoice'))

    assert doc.comprobante['Serie'] == 'RH'
    assert doc.comprobante['Folio'] == '11405'
    assert doc.comprobante['Total'] == '3160.00'
    assert doc.comprobante['Descuento'] == '172.41'
    assert doc.comprobante['TipoDeComprobante'] == 'I'
    assert doc.emisor == {'Rfc': 'EKU9003173C9', 'Nombre': 'EMPRESA DEMO', 'RegimenFiscal': '601'}
    assert doc.receptor['Rfc'] == 'XAÑ010101AB1'
    assert doc.receptor['UsoCFDI'] == 'G03'
    assert len(doc.conceptos) == 3
    assert doc.conceptos[0]['ClaveUnidad'] == 'H87'
    assert doc.traslados_total == '435.87'
    assert doc.retenciones_total is None
    assert doc.relacionados == []
    assert doc.pagos == []
    assert doc.timbre['UUID'] == 'BDCAD3DC-5428-5E2D-89AE-588872E59495'
    assert doc.timbre['NoCertificadoSAT'] == '00001000000719545303'


def test_parse_keeps_only_the_document_level_taxes() -> None:
    """Every concept carries its own `Impuestos`; the totals are the root's."""
    doc = cfdi.parse(_xml('invoice_retention'))

    assert doc.traslados_total == '1114.29'
    assert doc.retenciones_total == '278.57'


def test_parse_reads_related_documents() -> None:
    assert cfdi.parse(_xml('credit_note')).relacionados == [
        ('01', ['5691CD2C-45B6-5A26-97EF-E6209C062EFB'])
    ]
    assert cfdi.parse(_xml('advance')).relacionados == [
        ('07', ['E68D8C69-5706-5E5B-B544-C789F78DF2D8'])
    ]


def test_parse_reads_the_payments_complement() -> None:
    doc = cfdi.parse(_xml('payment'))

    assert doc.receptor['Rfc'] == 'A&A010101AB1'
    assert len(doc.pagos) == 1
    payment = doc.pagos[0]
    assert payment['attrs']['Monto'] == '269206.00'
    assert payment['attrs']['NumOperacion'] == '000123'
    first = payment['documentos'][0]
    assert first['IdDocumento'] == '4492E5A7-3DBE-50A5-82FF-7F1880C7AECA'
    assert (first['ImpSaldoAnt'], first['ImpPagado'], first['ImpSaldoInsoluto']) == (
        '269206.00',
        '100000.00',
        '169206.00',
    )


@pytest.mark.parametrize(
    'data',
    [
        'not xml at all',
        '<?xml version="1.0"?><Otro/>',
        _xml('invoice').split('<cfdi:Complemento>')[0] + '</cfdi:Comprobante>',
    ],
    ids=['malformed', 'no-comprobante', 'no-timbre'],
)
def test_parse_refuses_what_is_not_a_stamped_cfdi(data: str) -> None:
    with pytest.raises(cfdi.CfdiError):
        cfdi.parse(data)


# ── Original string of the stamp (research R2) ────────────────────────────────


def test_tfd_original_string_follows_sat_order() -> None:
    timbre = cfdi.parse(_xml('invoice')).timbre

    assert cfdi.tfd_original_string(timbre) == (
        f'||1.1|{timbre["UUID"]}|2026-09-29T18:28:56|LSO1306189R5|{timbre["SelloCFD"]}'
        '|00001000000719545303||'
    )


def test_tfd_original_string_includes_a_leyenda_and_normalizes_space() -> None:
    timbre = {
        'Version': '1.1',
        'UUID': 'AAA',
        'FechaTimbrado': '2026-01-01T00:00:00',
        'RfcProvCertif': 'PAC',
        'Leyenda': '  una   leyenda ',
        'SelloCFD': 'SELLO',
        'NoCertificadoSAT': '0001',
    }

    assert cfdi.tfd_original_string(timbre) == (
        '||1.1|AAA|2026-01-01T00:00:00|PAC|una leyenda|SELLO|0001||'
    )


# ── SAT QR payload (research R3) ──────────────────────────────────────────────

_SAT = 'https://verificacfdi.facturaelectronica.sat.gob.mx/default.aspx'


def test_qr_payload_of_an_invoice_keeps_an_enye_raw() -> None:
    assert cfdi.sat_qr_payload(cfdi.parse(_xml('invoice'))) == (
        f'{_SAT}?id=BDCAD3DC-5428-5E2D-89AE-588872E59495&re=EKU9003173C9&rr=XAÑ010101AB1'
        '&tt=3160.00&fe=nolHiw=='
    )


def test_qr_payload_of_a_payment_escapes_an_ampersand_and_totals_zero() -> None:
    assert cfdi.sat_qr_payload(cfdi.parse(_xml('payment'))) == (
        f'{_SAT}?id=17E0A075-DB77-5CF7-9DEE-E6CC005B8168&re=EKU9003173C9&rr=A%26A010101AB1'
        '&tt=0&fe=ejv7iQ=='
    )


# ── Batch settings (research R5) ──────────────────────────────────────────────

_LEGACY_TEMPLATE = """{
    'Name': 'T02Blue',
    'Logo': '~/Content/images/casamaestra.png',
    'HeaderHeight': 0,
    'FooterHeight': 15,
    'ExtraInfo': []
}"""


def test_batch_settings_read_legacy_single_quoted_template() -> None:
    assert cfdi.batch_settings(_LEGACY_TEMPLATE) == cfdi.BatchSettings(
        logo_name='casamaestra.png', footer_height_mm=15, accounts=[]
    )


def test_batch_settings_read_bank_accounts() -> None:
    template = (
        "{'Logo': 'x.png', 'FooterHeight': 20, 'ExtraInfo': [{'Bank': 'BBVA', "
        "'Account': '0123', 'CLABE': '012180001234567890', 'Currency': 'MXN'}]}"
    )

    assert cfdi.batch_settings(template) == cfdi.BatchSettings(
        logo_name='x.png',
        footer_height_mm=20,
        accounts=[
            {'Bank': 'BBVA', 'Account': '0123', 'CLABE': '012180001234567890', 'Currency': 'MXN'}
        ],
    )


@pytest.mark.parametrize(
    'template',
    [
        None,
        '',
        "{'Logo': ",
        "__import__('os')",
        "{'FooterHeight': -3}",
        "{'FooterHeight': 'tall'}",
        "{'ExtraInfo': 'none'}",
        "['not', 'a', 'dict']",
    ],
)
def test_batch_settings_fall_back_to_defaults(template: str | None) -> None:
    assert cfdi.batch_settings(template) == cfdi.BatchSettings()
