"""The Pagos 2.0 payment receipt builder (spec 028, research R3).

Legacy's `PaymentReceiptToCFDv40` (`CFDHelpers40.cs:123`) is the reference for the structure and the
16 % values; the tax breakdown follows each invoice's own transfers, where legacy assumed 16 %.
"""

import base64
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.x509 import load_der_x509_certificate
from lxml import etree

from app.services import cfdi_invoice
from app.services import cfdi_payment as pay
from tests.cfdi_schema import assert_valid_cfdi40
from tests.unit.test_csd_service import _CERTIFICATE_NUMBER, _PASSWORD, _make_csd

_FIXTURE = Path(__file__).parents[1] / 'fixtures' / 'cfdi' / 'payment.xml'
_NS = {'cfdi': 'http://www.sat.gob.mx/cfd/4', 'pago20': 'http://www.sat.gob.mx/Pagos20'}
_IVA16 = (pay.Transfer('Tasa', Decimal('0.160000'), Decimal('232074.14')),)


def _paid(**overrides: object) -> pay.PaidInvoiceData:
    values: dict[str, object] = {
        'uuid': '4492E5A7-3DBE-50A5-82FF-7F1880C7AECA',
        'batch': 'GR',
        'serial': 4702,
        'installment': 1,
        'previous_balance': Decimal('269206.00'),
        'amount': Decimal('100000.00'),
        'transfers': _IVA16,
    }
    values.update(overrides)
    return pay.PaidInvoiceData(**values)


def _fixture_data(**overrides: object) -> pay.PaymentData:
    """Receipt CGR 11 as stored: legacy let its payment amount exceed what it applied."""
    values: dict[str, object] = {
        'batch': 'CGR',
        'serial': 11,
        'issued': datetime(2026, 8, 19, 9, 25, 23),
        'issued_location': '06000',
        'issuer': 'EKU9003173C9',
        'issuer_name': 'EMPRESA DEMO',
        'issuer_regime': '601',
        'recipient': 'A&A010101AB1',
        'recipient_name': 'CLIENTE DEMO',
        'recipient_postal_code': '06000',
        'recipient_regime': '626',
        'payment_date': date(2026, 8, 19),
        'payment_form': 3,
        'amount': Decimal('269206.00'),
        'operation_number': '000123',
        'ordering_bank': None,
        'paid': (_paid(),),
    }
    values.update(overrides)
    return pay.PaymentData(**values)


def _build(data: pay.PaymentData) -> etree._Element:
    return pay.build(data, certificate_number=_CERTIFICATE_NUMBER, certificate='AA==')


def _attrs(root: etree._Element, path: str) -> list[dict[str, str]]:
    return [dict(e.attrib) for e in root.findall(path, _NS)]


def _stored_original_string() -> str:
    root = etree.parse(str(_FIXTURE)).getroot()
    for timbre in root.iter('{http://www.sat.gob.mx/TimbreFiscalDigital}TimbreFiscalDigital'):
        timbre.getparent().remove(timbre)
    return cfdi_invoice.original_string(root)


# ── Parity with legacy ────────────────────────────────────────────────────────


def test_rebuilt_fixture_has_the_stored_original_string() -> None:
    """SC-002 in the repository: a 16 %-only invoice gives legacy's exact values."""
    root = pay.build(_fixture_data(), certificate_number='00001000000701674368', certificate='AA==')

    assert cfdi_invoice.original_string(root) == _stored_original_string()


def test_header_and_concept_are_legacys_zero_value_receipt() -> None:
    root = _build(_fixture_data())

    assert {k: root.get(k) for k in ('SubTotal', 'Total', 'Moneda', 'TipoDeComprobante')} == {
        'SubTotal': '0',
        'Total': '0',
        'Moneda': 'XXX',
        'TipoDeComprobante': 'P',
    }
    assert root.get('FormaPago') is None and root.get('MetodoPago') is None
    assert root.find('cfdi:Receptor', _NS).get('UsoCFDI') == 'CP01'
    assert _attrs(root, 'cfdi:Conceptos/cfdi:Concepto') == [
        {
            'ClaveProdServ': '84111506',
            'Cantidad': '1',
            'ClaveUnidad': 'ACT',
            'Descripcion': 'Pago',
            'ValorUnitario': '0',
            'Importe': '0',
            'ObjetoImp': '01',
        }
    ]


def test_the_payment_and_its_document() -> None:
    root = _build(_fixture_data(ordering_bank='BBVA MEXICO'))

    [pago] = _attrs(root, 'cfdi:Complemento/pago20:Pagos/pago20:Pago')
    assert pago == {
        'FechaPago': '2026-08-19T00:00:00',
        'FormaDePagoP': '03',
        'MonedaP': 'MXN',
        'TipoCambioP': '1',
        'Monto': '269206.00',
        'NumOperacion': '000123',
        'NomBancoOrdExt': 'BBVA MEXICO',
    }
    [document] = _attrs(root, 'cfdi:Complemento/pago20:Pagos/pago20:Pago/pago20:DoctoRelacionado')
    assert document == {
        'IdDocumento': '4492E5A7-3DBE-50A5-82FF-7F1880C7AECA',
        'Serie': 'GR',
        'Folio': '4702',
        'MonedaDR': 'MXN',
        'EquivalenciaDR': '1',
        'NumParcialidad': '1',
        'ImpSaldoAnt': '269206.00',
        'ImpPagado': '100000.00',
        'ImpSaldoInsoluto': '169206.00',
        'ObjetoImpDR': '02',
    }


def test_an_operation_number_and_bank_are_omitted_when_blank() -> None:
    root = _build(_fixture_data(operation_number=None))

    [pago] = _attrs(root, 'cfdi:Complemento/pago20:Pagos/pago20:Pago')
    assert 'NumOperacion' not in pago and 'NomBancoOrdExt' not in pago


# ── Taxes per document (the correction to legacy) ─────────────────────────────


def _dr_transfers(root: etree._Element) -> list[dict[str, str]]:
    return _attrs(root, './/pago20:DoctoRelacionado/pago20:ImpuestosDR/pago20:TrasladosDR/*')


def test_a_16_percent_invoice_gives_legacys_values() -> None:
    assert _dr_transfers(_build(_fixture_data())) == [
        {
            'BaseDR': '86206.896552',
            'ImpuestoDR': '002',
            'TipoFactorDR': 'Tasa',
            'TasaOCuotaDR': '0.160000',
            'ImporteDR': '13793.103448',
        }
    ]


def test_mixed_rates_split_the_amount_in_the_invoices_proportions() -> None:
    """Values keep the scale C#'s decimals give them, as everywhere in the CFDI (spec 027, R5)."""
    # 1,000 at 16 % (1,160 of the total) and 500 at 0 % (500): 1,660 in all, of which 830 is paid.
    transfers = (
        pay.Transfer('Tasa', Decimal('0.160000'), Decimal('1000.00')),
        pay.Transfer('Tasa', Decimal('0.000000'), Decimal('500.00')),
    )
    paid = _paid(previous_balance=Decimal('1660.00'), amount=Decimal('830.00'), transfers=transfers)
    root = _build(_fixture_data(amount=Decimal('830.00'), paid=(paid,)))

    assert [(t['BaseDR'], t['TasaOCuotaDR'], t['ImporteDR']) for t in _dr_transfers(root)] == [
        ('500', '0.160000', '80.000000'),
        ('250', '0.000000', '0.000000'),
    ]
    totals = _attrs(root, 'cfdi:Complemento/pago20:Pagos/pago20:Totales')[0]
    assert totals == {
        'TotalTrasladosBaseIVA16': '500',
        'TotalTrasladosImpuestoIVA16': '80.00',
        'TotalTrasladosBaseIVA0': '250',
        'TotalTrasladosImpuestoIVA0': '0.00',
        'MontoTotalPagos': '830.00',
    }


def test_an_exempt_group_has_a_base_and_no_tax() -> None:
    transfers = (pay.Transfer('Exento', None, Decimal('300.00')),)
    paid = _paid(previous_balance=Decimal('300.00'), amount=Decimal('300.00'), transfers=transfers)
    root = _build(_fixture_data(amount=Decimal('300.00'), paid=(paid,)))

    assert _dr_transfers(root) == [
        {'BaseDR': '300.00', 'ImpuestoDR': '002', 'TipoFactorDR': 'Exento'}
    ]
    assert _attrs(root, './/pago20:ImpuestosP/pago20:TrasladosP/pago20:TrasladoP') == [
        {'BaseP': '300.00', 'ImpuestoP': '002', 'TipoFactorP': 'Exento'}
    ]
    totals = _attrs(root, 'cfdi:Complemento/pago20:Pagos/pago20:Totales')[0]
    assert totals == {'TotalTrasladosBaseIVAExento': '300.00', 'MontoTotalPagos': '300.00'}


def test_an_invoice_without_taxes_is_not_subject_and_totales_is_still_written() -> None:
    paid = _paid(previous_balance=Decimal('50.00'), amount=Decimal('50.00'), transfers=())
    root = _build(_fixture_data(amount=Decimal('50.00'), paid=(paid,)))

    [document] = root.findall('.//pago20:DoctoRelacionado', _NS)
    assert document.get('ObjetoImpDR') == '01'
    assert document.find('pago20:ImpuestosDR', _NS) is None
    assert root.find('.//pago20:Pago/pago20:ImpuestosP', _NS) is None
    assert _attrs(root, 'cfdi:Complemento/pago20:Pagos/pago20:Totales') == [
        {'MontoTotalPagos': '50.00'}
    ]


def test_two_invoices_add_up_in_impuestos_p_and_totales() -> None:
    first = _paid(previous_balance=Decimal('116.00'), amount=Decimal('116.00'))
    second = _paid(
        uuid='2A4B6C8D-0E1F-4A3B-8C5D-6E7F8A9B0C1D',
        serial=4703,
        previous_balance=Decimal('232.00'),
        amount=Decimal('58.00'),
        installment=2,
    )
    root = _build(_fixture_data(amount=Decimal('174.00'), paid=(first, second)))

    assert _attrs(root, './/pago20:ImpuestosP/pago20:TrasladosP/pago20:TrasladoP') == [
        {
            'BaseP': '150',
            'ImpuestoP': '002',
            'TipoFactorP': 'Tasa',
            'TasaOCuotaP': '0.160000',
            'ImporteP': '24.000000',
        }
    ]
    [document_2] = [d for d in _attrs(root, './/pago20:DoctoRelacionado') if d['Folio'] == '4703']
    assert (document_2['NumParcialidad'], document_2['ImpSaldoInsoluto']) == ('2', '174.00')


# ── Reading an invoice's transfers ────────────────────────────────────────────


def test_invoice_transfers_reads_the_summary_of_a_stored_invoice() -> None:
    xml = (Path(__file__).parents[1] / 'fixtures' / 'cfdi' / 'invoice.xml').read_text()

    assert pay.invoice_transfers(xml) == [
        pay.Transfer('Tasa', Decimal('0.160000'), Decimal('2724.19'))
    ]


def test_invoice_transfers_of_an_untaxed_invoice_is_empty() -> None:
    xml = '<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4" Total="5"/>'

    assert pay.invoice_transfers(xml) == []


# ── Sealing ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    'transfers',
    [
        _IVA16,
        (),
        (
            pay.Transfer('Tasa', Decimal('0.160000'), Decimal('100.00')),
            pay.Transfer('Exento', None, Decimal('40.00')),
        ),
    ],
)
def test_sealed_receipt_is_valid_and_its_seal_verifies(transfers: tuple) -> None:
    """SC-001, against cfdv40.xsd and Pagos20.xsd."""
    certificate_data, key_data = _make_csd()
    paid = _paid(previous_balance=Decimal('500.00'), amount=Decimal('500.00'), transfers=transfers)

    xml = pay.seal(
        _fixture_data(amount=Decimal('500.00'), paid=(paid,)),
        certificate_number=_CERTIFICATE_NUMBER,
        certificate_data=certificate_data,
        key_data=key_data,
        key_password=_PASSWORD,
    )

    assert_valid_cfdi40(xml)
    root = etree.fromstring(xml.encode('utf-8'))
    load_der_x509_certificate(certificate_data).public_key().verify(
        base64.b64decode(root.get('Sello')),
        cfdi_invoice.original_string(root).encode('utf-8'),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )


async def test_seal_async_runs_in_a_thread() -> None:
    certificate_data, key_data = _make_csd()
    data = replace(_fixture_data(), amount=Decimal('100000.00'))

    xml = await pay.seal_async(
        data,
        certificate_number=_CERTIFICATE_NUMBER,
        certificate_data=certificate_data,
        key_data=key_data,
        key_password=_PASSWORD,
    )

    assert_valid_cfdi40(xml)
