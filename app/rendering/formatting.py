"""Values as legacy's `es-MX` views printed them, independent of the host locale (#230)."""

import base64
import io
import math
import re
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

import segno
from barcode.charsets import code128

from app.enums import PaymentMethod, PaymentTerms, PaymentType

_DAYS = ('lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo')
_MONTHS = (
    'enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio',
    'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre',
)  # fmt: skip

# Legacy `[Display]` names (`Model/Constants/*.cs`). A code missing here prints as its number:
# legacy rows can carry SAT codes the enums do not name.
_METHOD_NAMES = {
    PaymentMethod.NA: 'N/A',
    PaymentMethod.CASH: 'Efectivo',
    PaymentMethod.CHECK: 'Cheque',
    PaymentMethod.EFT: 'Transferencia Electrónica',
    PaymentMethod.CREDIT_CARD: 'T. de Crédito',
    PaymentMethod.ELECTRONIC_PURSE: 'Monedero Electrónico',
    PaymentMethod.ELECTRONIC_MONEY: 'Dinero Electrónico',
    PaymentMethod.FOOD_VOUCHERS: 'Vales de Despensa',
    PaymentMethod.GIVING: 'Dación',
    PaymentMethod.TO_THE_SATISFACTION_OF_THE_CREDITOR: 'A Satisfacción Del Acreedor',
    PaymentMethod.DEBIT_CARD: 'T. de Débito',
    PaymentMethod.SERVICE_CARD: 'Tarjeta de Servicio',
    PaymentMethod.ADVANCE_PAYMENTS: 'Aplicación de Anticipos',
    PaymentMethod.TO_BE_DEFINED: 'Por Definir',
    PaymentMethod.GOVERNMENT_FUNDING: 'FONACOT',
}
_TERMS_NAMES = {PaymentTerms.IMMEDIATE: 'Contado', PaymentTerms.NET_D: 'Crédito'}
_PAYMENT_TYPE_NAMES = {
    PaymentType.IMMEDIATE: 'Contado',
    PaymentType.CREDIT_PAYMENT: 'Pago de Crédito',
    PaymentType.PAYMENT_IN_ADVANCE: 'Pago Anticipado',
    PaymentType.CREDIT_NOTE: 'Nota de Crédito',
}


def money(value: Decimal) -> str:
    amount = value.quantize(Decimal('0.01'), ROUND_HALF_UP)
    return f'{"-" if amount < 0 else ""}${abs(amount):,.2f}'


def date_short(value: date) -> str:
    return value.strftime('%Y-%m-%d')


def date_time(value: datetime) -> str:
    return value.strftime('%Y-%m-%d %H:%M:%S')


def date_long(value: date) -> str:
    return f'{_DAYS[value.weekday()]}, {_MONTHS[value.month - 1]} {value.day:02d}, {value.year}'


def qty(value: Decimal) -> str:
    text = f'{value.quantize(Decimal("0.0001"), ROUND_HALF_UP):f}'
    return text.rstrip('0').rstrip('.') if '.' in text else text


def percent(value: Decimal) -> str:
    return f'{(value * 100).quantize(Decimal("0.01"), ROUND_HALF_UP):f} %'


def pad8(value: int) -> str:
    return f'{value:08d}'


def pad6(value: int) -> str:
    return f'{value:06d}'


def price4(value: Decimal) -> str:
    """Legacy's `#,###.00##` (spec 020, research R8): two to four decimals, no leading zero."""
    amount = value.quantize(Decimal('0.0001'), ROUND_HALF_UP)
    whole, fraction = f'{abs(amount):,.4f}'.split('.')
    return f'{"-" if amount < 0 else ""}{"" if whole == "0" else whole}.' + (
        fraction.rstrip('0').ljust(2, '0')
    )


def method_name(code: int) -> str:
    return _METHOD_NAMES.get(code, str(code))


def terms_name(code: int) -> str:
    return _TERMS_NAMES.get(code, str(code))


def payment_type_name(code: int) -> str:
    return _PAYMENT_TYPE_NAMES.get(code, str(code))


def code128b_codes(value: str) -> list[int]:
    """Code128 symbol values for `value` in subset B, start code and checksum included.

    Legacy encodes subsets A/B only and picks B for digits (`Code128Content.cs`), so an 8-digit id
    is 8 symbols. python-barcode would switch to subset C (digit pairs), a different bar pattern.
    """
    codes = [code128.START_CODES['B'], *(code128.B[char] for char in value)]
    checksum = (codes[0] + sum(i * code for i, code in enumerate(codes[1:], 1))) % 103
    return [*codes, checksum]


#: The stop symbol with its final 2-module bar, 13 modules; `code128.STOP` omits the bar.
_STOP = '1100011101011'
#: Legacy's `MakeBarcodeImage(id, 2, false)`: 2 px per module, no quiet zone, 15% as tall as wide.
_MODULE_PX = 2


def barcode_data_uri(value: str) -> str:
    """Code128-B of `value` as an SVG `data:` URI at legacy's size; the template prints the text."""
    modules = ''.join(code128.CODES[code] for code in code128b_codes(value)) + _STOP
    width = len(modules) * _MODULE_PX
    height = math.ceil(width * 0.15)
    bars = ''.join(
        f'<rect x="{match.start() * _MODULE_PX}" width="{len(match.group()) * _MODULE_PX}"'
        f' height="{height}"/>'
        for match in re.finditer('1+', modules)
    )
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}px" height="{height}px"'
        f' viewBox="0 0 {width} {height}" shape-rendering="crispEdges">{bars}</svg>'
    )
    return 'data:image/svg+xml;base64,' + base64.b64encode(svg.encode()).decode()


# ── CFDI (spec 020, research R7 and R8) ───────────────────────────────────────

# Legacy `FiscalDocumentType` display names.
_FISCAL_TYPE_TITLES = {
    0: 'Factura',
    1: 'Recibo de Honorarios',
    2: 'Recibo de Arrendamiento',
    3: 'Nota de Cargo',
    100: 'Nota de Crédito',
    101: 'Aplicación de Anticipos',
    200: 'Recibo Electrónico de Pago',
}
# Legacy's `Resources.resx` texts for the SAT `c_TipoRelacion` codes it prints.
_RELATION_LABELS = {
    '01': '01 : Nota de Crédito de los Documentos Relacionados',
    '04': '04 : Sustitución de los CFDI Previos',
    '07': '07 : CFDI por Aplicación de Anticipo',
}
_PAYMENT_METHOD_LABELS = {
    'PUE': 'PUE : Pago en una sola exhibición',
    'PPD': 'PPD : Pago en parcialidades o diferido',
}


def fiscal_type_title(code: int) -> str:
    return _FISCAL_TYPE_TITLES.get(code, 'CFDI')


def relation_label(code: str) -> str:
    return _RELATION_LABELS.get(code, code)


def payment_method_label(code: str | None) -> str:
    return _PAYMENT_METHOD_LABELS.get(code, code or '')


def payment_form_label(code: str | None) -> str:
    """A SAT `c_FormaPago` code with legacy's name; the codes equal `PaymentMethod`'s values."""
    if not code:
        return ''
    return f'{code} : {method_name(int(code))}' if code.isdigit() else code


def qr_data_uri(payload: str) -> str:
    """The QR code as an SVG `data:` URI with no quiet zone, sized by CSS through its viewBox."""
    buffer = io.BytesIO()
    segno.make(payload, error='m').save(buffer, kind='svg', xmldecl=False, border=0, omitsize=True)
    return 'data:image/svg+xml;base64,' + base64.b64encode(buffer.getvalue()).decode()
