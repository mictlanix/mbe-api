"""CFDI 4.0 invoices: the XML, its original string and the issuer's seal (#47, spec 027).

The values and their rounding are legacy's, so a sealed invoice reads exactly as the current system
would have issued it: `CFDHelpers40.InvoiceToCFDv40` (`mbe/Web/Helpers/CFDHelpers40.cs:368`) and
the line helpers in `mbe/Model/Helpers/ModelHelpers.cs` (research R5). Rounding is half away from
zero, per line, before anything is summed.

The original string is SAT's own stylesheet, cfdlib's XSLT 1.0 copy in `app/resources/cfdi40/`
(research R3). The seal is SHA-256 with RSA (PKCS#1 v1.5) under the issuer's CSD key (R4).

Everything here is pure and CPU-bound: no database, nothing stamped, no PAC.
"""

import asyncio
import base64
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from lxml import etree

from app.enums import PaymentTerms
from app.services.csd_service import _load_key

CFDI = 'http://www.sat.gob.mx/cfd/4'
_XSI = 'http://www.w3.org/2001/XMLSchema-instance'
_SCHEMA_LOCATION = (
    'http://www.sat.gob.mx/cfd/4 http://www.sat.gob.mx/sitio_internet/cfd/4/cfdv40.xsd'
)
_XSLT = Path(__file__).resolve().parents[1] / 'resources' / 'cfdi40' / 'cadena_original_4_0.xslt'

_MICROS = Decimal('0.000001')
_QUANTITY = Decimal('0.0001')
_IVA = '002'
#: `fiscal_document_detail.tax_rate` for an exempt line, as legacy stores it.
EXEMPT = Decimal('-1')


@dataclass(frozen=True)
class InvoiceLineData:
    product_service: str
    product_code: str | None
    quantity: Decimal
    unit: str
    unit_name: str | None
    description: str
    price: Decimal
    discount_rate: Decimal
    tax_rate: Decimal
    tax_included: bool


@dataclass(frozen=True)
class InvoiceData:
    """What the XML needs from a fiscal document, its lines and its relations."""

    batch: str | None
    serial: int
    issued: datetime
    payment_method: int
    payment_terms: PaymentTerms
    issued_location: str
    issuer: str
    issuer_name: str | None
    issuer_regime: str
    recipient: str
    recipient_name: str | None
    recipient_postal_code: str
    recipient_regime: str
    usage: str
    lines: Sequence[InvoiceLineData]
    relation_type: str | None = None
    relation_uuids: Sequence[str] = ()
    #: `I` for an invoice; `E` for a credit note, built the same way (spec 028, research R4).
    voucher_type: str = 'I'


@dataclass(frozen=True)
class LineAmounts:
    unit_value: Decimal
    subtotal: Decimal
    discount: Decimal
    total: Decimal
    taxes: Decimal
    base: Decimal


def _round(value: Decimal, places: int) -> Decimal:
    """C#'s `Math.Round(d, places, AwayFromZero)`: it removes decimal places, never adds them.

    Legacy writes every amount at the scale its C# decimal carries, so `1250` stays `1250` and
    `87.0000000 / 1.160000` is `75.0`. Python's Decimal follows the same scale rules for `+ - * /`,
    so only the rounding needs emulating (SC-002 found both on mbe_dev).
    """
    if value.as_tuple().exponent < -places:
        return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    return value


def line_amounts(line: InvoiceLineData) -> LineAmounts:
    """Legacy's `FiscalDocumentDetail` figures for one line (exchange rate 1: MXN only)."""
    q, p, d, t = line.quantity, line.price, line.discount_rate, line.tax_rate
    divided = line.tax_included and t > 0

    subtotal = _round(q * p / (1 + t) if divided else q * p, 2)
    discount = _round(q * p * d / (1 + t) if divided else q * p * d, 2)
    gross_discount = _round(q * p * d, 2) if d > 0 else Decimal(0)
    if line.tax_included or t <= 0:
        total = _round(q * p - gross_discount, 2)
    else:
        total = _round((q * p - gross_discount) * (1 + t), 2)
    taxes = total - subtotal + discount
    return LineAmounts(
        unit_value=_round(p / (1 + t) if divided else p, 6),
        subtotal=subtotal,
        discount=discount,
        total=total,
        taxes=taxes,
        base=taxes / t if t > 0 else subtotal - discount,
    )


def format_base(value: Decimal) -> str:
    """A decimal as C# prints it: its own scale, never an exponent (research R5)."""
    return format(value, 'f')


def _money(value: Decimal) -> str:
    return format_base(value)


def _rate(value: Decimal) -> str:
    return f'{value.quantize(_MICROS):f}'


def _usage(code: str) -> str:
    """Legacy maps the CFDI 3.3 usage P01 to G03 for compatibility (`CfdiUsage2UsoCFDI`)."""
    return 'G03' if code == 'P01' else code


def _element(parent: etree._Element, tag: str, attributes: dict[str, str | None]):  # noqa: ANN202
    return etree.SubElement(
        parent, f'{{{CFDI}}}{tag}', {k: v for k, v in attributes.items() if v is not None}
    )


def build(data: InvoiceData, *, certificate_number: str, certificate: str) -> etree._Element:
    """The unsealed `cfdi:Comprobante` (`Sello` empty). `certificate` is the base64 DER."""
    concepts: list[tuple[InvoiceLineData, LineAmounts]] = [
        (line, line_amounts(line)) for line in data.lines
    ]
    discount = sum((a.discount for _, a in concepts), Decimal(0))

    root = etree.Element(f'{{{CFDI}}}Comprobante', nsmap={'cfdi': CFDI, 'xsi': _XSI})
    root.set(f'{{{_XSI}}}schemaLocation', _SCHEMA_LOCATION)
    header = {
        'Version': '4.0',
        'Serie': data.batch or None,
        'Folio': str(data.serial),
        'Fecha': data.issued.strftime('%Y-%m-%dT%H:%M:%S'),
        'Sello': '',
        'FormaPago': f'{data.payment_method:02d}',
        'NoCertificado': certificate_number,
        'Certificado': certificate,
        'SubTotal': _money(sum((a.subtotal for _, a in concepts), Decimal(0))),
        'Descuento': _money(discount) if discount > 0 else None,
        'Moneda': 'MXN',
        'Total': _money(sum((a.total for _, a in concepts), Decimal(0))),
        'TipoDeComprobante': data.voucher_type,
        'Exportacion': '01',
        'MetodoPago': 'PUE' if data.payment_terms == PaymentTerms.IMMEDIATE else 'PPD',
        'LugarExpedicion': data.issued_location,
    }
    for name, value in header.items():
        if value is not None:
            root.set(name, value)

    if data.relation_uuids:
        related = _element(root, 'CfdiRelacionados', {'TipoRelacion': data.relation_type})
        for uuid in data.relation_uuids:
            _element(related, 'CfdiRelacionado', {'UUID': uuid})

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
            'UsoCFDI': _usage(data.usage),
        },
    )

    # Transfers grouped by (factor, rate) in order of first appearance, as LINQ's GroupBy does.
    groups: dict[tuple[str, Decimal | None], list[Decimal]] = {}
    conceptos = _element(root, 'Conceptos', {})
    for line, amounts in concepts:
        not_taxed = amounts.subtotal == amounts.discount
        concept = _element(
            conceptos,
            'Concepto',
            {
                'ClaveProdServ': line.product_service,
                'NoIdentificacion': line.product_code or None,
                'Cantidad': f'{line.quantity.quantize(_QUANTITY):f}',
                'ClaveUnidad': line.unit,
                'Unidad': line.unit_name or None,
                'Descripcion': line.description,
                'ValorUnitario': format_base(amounts.unit_value),
                'Importe': _money(amounts.subtotal),
                'Descuento': _money(amounts.discount) if amounts.discount > 0 else None,
                'ObjetoImp': '01' if not_taxed else '02',
            },
        )
        if not_taxed:
            continue
        transfers = _element(_element(concept, 'Impuestos', {}), 'Traslados', {})
        if line.tax_rate == EXEMPT:
            key: tuple[str, Decimal | None] = ('Exento', None)
            _element(
                transfers,
                'Traslado',
                {'Base': format_base(amounts.base), 'Impuesto': _IVA, 'TipoFactor': 'Exento'},
            )
        else:
            key = ('Tasa', line.tax_rate.quantize(_MICROS))
            _element(
                transfers,
                'Traslado',
                {
                    'Base': format_base(amounts.base),
                    'Impuesto': _IVA,
                    'TipoFactor': 'Tasa',
                    'TasaOCuota': _rate(line.tax_rate),
                    'Importe': _money(amounts.taxes),
                },
            )
        groups.setdefault(key, [Decimal(0), Decimal(0)])
        groups[key][0] += amounts.base
        groups[key][1] += amounts.taxes

    if groups:
        rated = [_round(i, 2) for (factor, _), (_, i) in groups.items() if factor == 'Tasa']
        taxes = _element(
            root,
            'Impuestos',
            # SAT: with only exempt transfers there is no total transferred.
            {'TotalImpuestosTrasladados': _money(sum(rated)) if rated else None},
        )
        traslados = _element(taxes, 'Traslados', {})
        for (factor, rate), (base, importe) in groups.items():
            exempt = factor == 'Exento'
            _element(
                traslados,
                'Traslado',
                {
                    'Base': _money(_round(base, 2)),
                    'Impuesto': _IVA,
                    'TipoFactor': factor,
                    'TasaOCuota': None if exempt else _rate(rate),
                    'Importe': None if exempt else _money(_round(importe, 2)),
                },
            )
    return root


_local = threading.local()


def _transform() -> etree.XSLT:
    """One compiled stylesheet per thread: lxml stylesheets must not cross threads."""
    if not hasattr(_local, 'xslt'):
        _local.xslt = etree.XSLT(etree.parse(str(_XSLT)))
    return _local.xslt


def original_string(xml: etree._Element) -> str:
    return str(_transform()(etree.ElementTree(xml)))


def serialize(root: etree._Element) -> str:
    return '<?xml version="1.0" encoding="utf-8"?>' + etree.tostring(root, encoding='unicode')


def seal_root(
    root: etree._Element,
    *,
    certificate_number: str,
    certificate_data: bytes,
    key_data: bytes,
    key_password: bytes,
) -> str:
    """Seal any built CFDI 4.0, invoice or payment receipt, and return it as text.

    Sets `NoCertificado` and `Certificado`, signs the original string and sets `Sello`. Raises
    ValueError when the key does not open.
    """
    key = _load_key(key_data, key_password)
    root.set('NoCertificado', certificate_number)
    root.set('Certificado', base64.b64encode(certificate_data).decode('ascii'))
    signature = key.sign(original_string(root).encode('utf-8'), padding.PKCS1v15(), hashes.SHA256())
    root.set('Sello', base64.b64encode(signature).decode('ascii'))
    return serialize(root)


def seal(
    data: InvoiceData,
    *,
    certificate_number: str,
    certificate_data: bytes,
    key_data: bytes,
    key_password: bytes,
) -> str:
    """The sealed CFDI 4.0 XML as text. Raises ValueError when the key does not open."""
    root = build(data, certificate_number=certificate_number, certificate='')
    return seal_root(
        root,
        certificate_number=certificate_number,
        certificate_data=certificate_data,
        key_data=key_data,
        key_password=key_password,
    )


async def seal_async(
    data: InvoiceData,
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


async def seal_root_async(
    root: etree._Element,
    *,
    certificate_number: str,
    certificate_data: bytes,
    key_data: bytes,
    key_password: bytes,
) -> str:
    return await asyncio.to_thread(
        seal_root,
        root,
        certificate_number=certificate_number,
        certificate_data=certificate_data,
        key_data=key_data,
        key_password=key_password,
    )
