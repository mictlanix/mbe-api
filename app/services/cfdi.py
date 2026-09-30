"""The stamped CFDI as its printed representation reads it (#230, spec 020).

Every value is returned exactly as stamped, as the attribute string, so what the PDF prints is what
SAT holds (research R1). The XML is our own stamping output, parsed with the standard library:
expat resolves no external entities and caps entity expansion (research R6).
"""

import ast
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import PurePosixPath

SAT_VERIFICATION_URL = 'https://verificacfdi.facturaelectronica.sat.gob.mx/default.aspx'


class CfdiError(ValueError):
    """The text is not a stamped CFDI."""


@dataclass(frozen=True)
class Cfdi:
    comprobante: dict[str, str]
    emisor: dict[str, str]
    receptor: dict[str, str]
    conceptos: list[dict[str, str]]
    traslados_total: str | None
    retenciones_total: str | None
    relacionados: list[tuple[str, list[str]]]
    pagos: list[dict]
    timbre: dict[str, str]


def _attrs(element: ET.Element) -> dict[str, str]:
    """Attributes by local name. `xsi:schemaLocation` is dropped, as nothing prints it."""
    return {
        key.split('}')[-1]: value
        for key, value in element.attrib.items()
        if not key.endswith('schemaLocation')
    }


def parse(data: str) -> Cfdi:
    try:
        root = ET.fromstring(data.encode('utf-8'))
    except ET.ParseError as error:
        raise CfdiError(str(error)) from error
    timbre = root.find('.//{*}TimbreFiscalDigital')
    if root.tag.split('}')[-1] != 'Comprobante' or timbre is None:
        raise CfdiError('not a stamped CFDI')

    # Only the root's own `Impuestos` holds the document totals; each concept has its own.
    impuestos = root.find('{*}Impuestos')
    taxes = _attrs(impuestos) if impuestos is not None else {}
    return Cfdi(
        comprobante=_attrs(root),
        emisor=_attrs(root.find('{*}Emisor')),
        receptor=_attrs(root.find('{*}Receptor')),
        conceptos=[_attrs(c) for c in root.findall('{*}Conceptos/{*}Concepto')],
        traslados_total=taxes.get('TotalImpuestosTrasladados'),
        retenciones_total=taxes.get('TotalImpuestosRetenidos'),
        relacionados=[
            (group.get('TipoRelacion', ''), [r.get('UUID', '') for r in group])
            for group in root.findall('{*}CfdiRelacionados')
        ],
        pagos=[
            {
                'attrs': _attrs(pago),
                'documentos': [_attrs(d) for d in pago.findall('{*}DoctoRelacionado')],
            }
            for pago in root.findall('{*}Complemento/{*}Pagos/{*}Pago')
        ],
        timbre=_attrs(timbre),
    )


_TFD_FIELDS = (
    'Version',
    'UUID',
    'FechaTimbrado',
    'RfcProvCertif',
    'Leyenda',
    'SelloCFD',
    'NoCertificadoSAT',
)


def tfd_original_string(timbre: dict[str, str]) -> str:
    """SAT's `cadenaoriginal_TFD_1_1.xslt`, which legacy ran through cfdlib (research R2)."""
    values = [' '.join(timbre.get(name, '').split()) for name in _TFD_FIELDS]
    # Leyenda is optional, and an absent one leaves no empty field behind.
    if not values[4]:
        del values[4]
    return '||' + '|'.join(values) + '||'


def sat_qr_payload(cfdi: Cfdi) -> str:
    """Legacy's `ValidataionUrl` with the stamped values (research R3).

    An `&` in an RFC would end the query parameter, so it is written as `%26`. Nothing else is
    escaped: an `Ñ` stays as legacy has always printed it.
    """

    def rfc(value: str) -> str:
        return value.replace('&', '%26')

    return (
        f'{SAT_VERIFICATION_URL}?id={cfdi.timbre["UUID"]}&re={rfc(cfdi.emisor["Rfc"])}'
        f'&rr={rfc(cfdi.receptor["Rfc"])}&tt={cfdi.comprobante["Total"]}'
        f'&fe={cfdi.comprobante["Sello"][-8:]}'
    )


@dataclass(frozen=True)
class BatchSettings:
    logo_name: str | None = None
    footer_height_mm: Decimal | int = 15
    accounts: list[dict[str, str]] = field(default_factory=list)


_ACCOUNT_KEYS = ('Bank', 'Account', 'CLABE', 'Currency')


def batch_settings(template: str | None) -> BatchSettings:
    """`taxpayer_batch.template`, a single-quoted literal legacy parsed with Newtonsoft.

    `ast.literal_eval` reads it and cannot run code. Anything unreadable falls back to the
    defaults, field by field (research R5).
    """
    try:
        values = ast.literal_eval(template or '')
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        return BatchSettings()
    if not isinstance(values, dict):
        return BatchSettings()

    logo = values.get('Logo')
    logo_name = PurePosixPath(logo.replace('\\', '/')).name if isinstance(logo, str) else ''
    height = values.get('FooterHeight')
    valid_height = isinstance(height, int | float) and not isinstance(height, bool) and height >= 0
    extra = values.get('ExtraInfo')
    accounts = [
        {key: str(item.get(key) or '') for key in _ACCOUNT_KEYS}
        for item in (extra if isinstance(extra, list) else [])
        if isinstance(item, dict)
    ]
    return BatchSettings(
        logo_name=logo_name or None,
        footer_height_mm=Decimal(str(height)) if valid_height else 15,
        accounts=accounts,
    )
