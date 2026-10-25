"""CFDI 4.0 payment receipts with the Pagos 2.0 complement (#53, spec 028).

The structure and the values are legacy's `PaymentReceiptToCFDv40`
(`mbe/Web/Helpers/CFDHelpers40.cs:123`):
- a zero-value Comprobante of type P in currency XXX;
- one "Pago" concept;
- one `Pago` with one `DoctoRelacionado` per paid invoice.

Values print at the scale their C# decimals carry, as in `cfdi_invoice`. Two corrections, both
research R3:
- **Tax breakdown.** Each paid invoice's taxes follow that invoice's own transfers, scaled to the
  amount paid. Legacy assumed every invoice was 16 %, so 0 % and exempt lines got a 16 % tax. For
  a 16 %-only invoice the result is legacy's exact `round6(amount / 1.16)` and
  `round6(base × 0.16)`.
- **`Totales`.** It is always written, with one field per rate present. Legacy dropped it when an
  invoice had no taxes, and summed every rate into the IVA16 fields.

The original string and the seal are `cfdi_invoice`'s; nothing here is stamped.
"""

import asyncio
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from lxml import etree

from app.services.cfdi_invoice import (
    _IVA,
    _XSI,
    CFDI,
    _element,
    _round,
    format_base,
    seal_root,
)

PAGOS = 'http://www.sat.gob.mx/Pagos20'
_SCHEMA_LOCATION = (
    'http://www.sat.gob.mx/cfd/4 http://www.sat.gob.mx/sitio_internet/cfd/4/cfdv40.xsd '
    'http://www.sat.gob.mx/Pagos20 http://www.sat.gob.mx/sitio_internet/cfd/Pagos/Pagos20.xsd'
)
_PAGOS_LOCATION = (
    'http://www.sat.gob.mx/Pagos20 http://www.sat.gob.mx/sitio_internet/cfd/Pagos/Pagos20.xsd'
)
_SIXTEEN = Decimal('0.16')
_ZERO = Decimal(0)


@dataclass(frozen=True)
class Transfer:
    """One of an invoice's summary transfers: `Tasa` with its rate, or `Exento` with none."""

    factor: str
    rate: Decimal | None
    base: Decimal


@dataclass(frozen=True)
class PaidInvoiceData:
    uuid: str
    batch: str | None
    serial: int | None
    installment: int
    previous_balance: Decimal
    amount: Decimal
    transfers: Sequence[Transfer]


@dataclass(frozen=True)
class PaymentData:
    batch: str | None
    serial: int
    issued: datetime
    issued_location: str
    issuer: str
    issuer_name: str | None
    issuer_regime: str
    recipient: str
    recipient_name: str | None
    recipient_postal_code: str
    recipient_regime: str
    payment_date: date
    payment_form: int
    amount: Decimal
    operation_number: str | None
    ordering_bank: str | None
    paid: Sequence[PaidInvoiceData]


def invoice_transfers(xml: str) -> list[Transfer]:
    """The summary `Impuestos/Traslados` of a stored invoice, CFDI 3.3 or 4.0."""
    root = ET.fromstring(xml.encode('utf-8'))
    taxes = next((e for e in root if e.tag.endswith('}Impuestos')), None)
    if taxes is None:
        return []
    transfers = []
    for traslado in taxes.iter():
        if not traslado.tag.endswith('}Traslado'):
            continue
        factor = traslado.get('TipoFactor', 'Tasa')
        rate = traslado.get('TasaOCuota')
        transfers.append(
            Transfer(
                factor, Decimal(rate) if rate is not None else None, Decimal(traslado.get('Base'))
            )
        )
    return transfers


@dataclass(frozen=True)
class _DocumentTax:
    factor: str
    rate: Decimal | None
    base: Decimal
    tax: Decimal | None


def document_taxes(amount: Decimal, transfers: Sequence[Transfer]) -> list[_DocumentTax]:
    """The amount paid split in the invoice's own proportions (research R3).

    Each transfer weighs its base plus its tax. A single transfer divides the amount by `1 + rate`,
    exactly as legacy did for its one assumed 16 % rate.
    """
    if not transfers:
        return []
    if len(transfers) == 1:
        rate = transfers[0].rate or _ZERO
        bases = [amount / (1 + rate)]
    else:
        total = sum((t.base * (1 + (t.rate or _ZERO)) for t in transfers), _ZERO)
        bases = [amount * t.base / total for t in transfers]
    result = []
    for transfer, base in zip(transfers, bases, strict=True):
        base = _round(base, 6)
        tax = None if transfer.factor == 'Exento' else _round(base * transfer.rate, 6)
        result.append(_DocumentTax(transfer.factor, transfer.rate, base, tax))
    return result


def _rate(value: Decimal) -> str:
    return f'{value.quantize(Decimal("0.000001")):f}'


def _transfer_attributes(suffix: str, tax: _DocumentTax, base: Decimal, amount: Decimal | None):  # noqa: ANN202
    attributes = {
        f'Base{suffix}': format_base(base),
        f'Impuesto{suffix}': _IVA,
        f'TipoFactor{suffix}': tax.factor,
    }
    if tax.factor != 'Exento':
        attributes[f'TasaOCuota{suffix}'] = _rate(tax.rate)
        attributes[f'Importe{suffix}'] = format_base(amount)
    return attributes


def _pagos(parent: etree._Element, tag: str, attributes: dict[str, str | None]) -> etree._Element:
    return etree.SubElement(
        parent, f'{{{PAGOS}}}{tag}', {k: v for k, v in attributes.items() if v is not None}
    )


def build(data: PaymentData, *, certificate_number: str, certificate: str) -> etree._Element:
    """The unsealed payment receipt (`Sello` empty). `certificate` is the base64 DER."""
    root = etree.Element(
        f'{{{CFDI}}}Comprobante', nsmap={'cfdi': CFDI, 'xsi': _XSI, 'pago20': PAGOS}
    )
    root.set(f'{{{_XSI}}}schemaLocation', _SCHEMA_LOCATION)
    header = {
        'Version': '4.0',
        'Serie': data.batch or None,
        'Folio': str(data.serial),
        'Fecha': data.issued.strftime('%Y-%m-%dT%H:%M:%S'),
        'Sello': '',
        'NoCertificado': certificate_number,
        'Certificado': certificate,
        'SubTotal': '0',
        'Moneda': 'XXX',
        'Total': '0',
        'TipoDeComprobante': 'P',
        'Exportacion': '01',
        'LugarExpedicion': data.issued_location,
    }
    for name, value in header.items():
        if value is not None:
            root.set(name, value)

    _element(
        root,
        'Emisor',
        {'Rfc': data.issuer, 'Nombre': data.issuer_name, 'RegimenFiscal': data.issuer_regime},
    )
    _element(
        root,
        'Receptor',
        {
            'Rfc': data.recipient,
            'Nombre': data.recipient_name,
            'DomicilioFiscalReceptor': data.recipient_postal_code,
            'RegimenFiscalReceptor': data.recipient_regime,
            'UsoCFDI': 'CP01',
        },
    )
    _element(
        _element(root, 'Conceptos', {}),
        'Concepto',
        {
            'ClaveProdServ': '84111506',
            'Cantidad': '1',
            'ClaveUnidad': 'ACT',
            'Descripcion': 'Pago',
            'ValorUnitario': '0',
            'Importe': '0',
            'ObjetoImp': '01',
        },
    )

    pagos = _pagos(_element(root, 'Complemento', {}), 'Pagos', {'Version': '2.0'})
    pagos.set(f'{{{_XSI}}}schemaLocation', _PAGOS_LOCATION)
    totales = _pagos(pagos, 'Totales', {})
    pago = _pagos(
        pagos,
        'Pago',
        {
            'FechaPago': f'{data.payment_date.isoformat()}T00:00:00',
            'FormaDePagoP': f'{data.payment_form:02d}',
            'MonedaP': 'MXN',
            'TipoCambioP': '1',
            'Monto': format_base(data.amount),
            'NumOperacion': data.operation_number or None,
            'NomBancoOrdExt': data.ordering_bank or None,
        },
    )

    # Summed by (factor, rate) in order of first appearance, as legacy's LINQ grouping does.
    groups: dict[tuple[str, Decimal | None], list[Decimal]] = {}
    for paid in data.paid:
        taxes = document_taxes(paid.amount, paid.transfers)
        document = _pagos(
            pago,
            'DoctoRelacionado',
            {
                'IdDocumento': paid.uuid,
                'Serie': paid.batch or None,
                'Folio': str(paid.serial) if paid.serial is not None else None,
                'MonedaDR': 'MXN',
                'EquivalenciaDR': '1',
                'NumParcialidad': str(paid.installment),
                'ImpSaldoAnt': format_base(paid.previous_balance),
                'ImpPagado': format_base(paid.amount),
                'ImpSaldoInsoluto': format_base(paid.previous_balance - paid.amount),
                'ObjetoImpDR': '02' if taxes else '01',
            },
        )
        if not taxes:
            continue
        traslados = _pagos(_pagos(document, 'ImpuestosDR', {}), 'TrasladosDR', {})
        for tax in taxes:
            _pagos(traslados, 'TrasladoDR', _transfer_attributes('DR', tax, tax.base, tax.tax))
            sums = groups.setdefault((tax.factor, tax.rate), [_ZERO, _ZERO])
            sums[0] += tax.base
            # A zero tax is falsy but keeps its scale (`0.000000`); only exempt has none.
            sums[1] += tax.tax if tax.tax is not None else _ZERO

    totals: dict[str, str] = {}
    if groups:
        traslados_p = _pagos(_pagos(pago, 'ImpuestosP', {}), 'TrasladosP', {})
        for (factor, rate), (base, amount) in groups.items():
            group = _DocumentTax(factor, rate, base, amount)
            base, amount = _round(base, 6), _round(amount, 6)
            _pagos(traslados_p, 'TrasladoP', _transfer_attributes('P', group, base, amount))
            if factor == 'Exento':
                totals['TotalTrasladosBaseIVAExento'] = format_base(_round(base, 2))
            elif rate == _SIXTEEN:
                totals['TotalTrasladosBaseIVA16'] = format_base(_round(base, 2))
                totals['TotalTrasladosImpuestoIVA16'] = format_base(_round(amount, 2))
            elif rate == _ZERO:
                totals['TotalTrasladosBaseIVA0'] = format_base(_round(base, 2))
                totals['TotalTrasladosImpuestoIVA0'] = format_base(_round(amount, 2))
    # Pagos20.xsd fixes the attribute order of `Totales`; the stylesheet reads them in that order.
    order = (
        'TotalTrasladosBaseIVA16',
        'TotalTrasladosImpuestoIVA16',
        'TotalTrasladosBaseIVA0',
        'TotalTrasladosImpuestoIVA0',
        'TotalTrasladosBaseIVAExento',
    )
    for name in order:
        if name in totals:
            totales.set(name, totals[name])
    totales.set('MontoTotalPagos', format_base(_round(data.amount, 2)))
    return root


def seal(
    data: PaymentData,
    *,
    certificate_number: str,
    certificate_data: bytes,
    key_data: bytes,
    key_password: bytes,
) -> str:
    """The sealed payment receipt as text. Raises ValueError when the key does not open."""
    return seal_root(
        build(data, certificate_number=certificate_number, certificate=''),
        certificate_number=certificate_number,
        certificate_data=certificate_data,
        key_data=key_data,
        key_password=key_password,
    )


async def seal_async(
    data: PaymentData,
    *,
    certificate_number: str,
    certificate_data: bytes,
    key_data: bytes,
    key_password: bytes,
) -> str:
    return await asyncio.to_thread(
        seal,
        data,
        certificate_number=certificate_number,
        certificate_data=certificate_data,
        key_data=key_data,
        key_password=key_password,
    )
