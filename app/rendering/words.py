"""Amounts in Spanish words, as legacy `Web/Utils/CurrencyConverter.cs` printed them (#230).

Legacy's format is kept (currency names, `cc/100`, `M. N.`, rounding half away from zero); its
grammar is corrected, per research R10: "MIL", not "UN MIL"; "uno" shortened before a noun or
"mil"; a singular noun for one; "DE" after an exact million.
"""

from decimal import ROUND_HALF_UP, Decimal

from app.enums import CurrencyCode

_UNITS = (
    '', 'UNO', 'DOS', 'TRES', 'CUATRO', 'CINCO', 'SEIS', 'SIETE', 'OCHO', 'NUEVE', 'DIEZ', 'ONCE',
    'DOCE', 'TRECE', 'CATORCE', 'QUINCE', 'DIECISÉIS', 'DIECISIETE', 'DIECIOCHO', 'DIECINUEVE',
    'VEINTE', 'VEINTIUNO', 'VEINTIDÓS', 'VEINTITRÉS', 'VEINTICUATRO', 'VEINTICINCO', 'VEINTISÉIS',
    'VEINTISIETE', 'VEINTIOCHO', 'VEINTINUEVE',
)  # fmt: skip
_TENS = ('', '', '', 'TREINTA', 'CUARENTA', 'CINCUENTA', 'SESENTA', 'SETENTA', 'OCHENTA', 'NOVENTA')
_HUNDREDS = (
    '', 'CIENTO', 'DOSCIENTOS', 'TRESCIENTOS', 'CUATROCIENTOS', 'QUINIENTOS', 'SEISCIENTOS',
    'SETECIENTOS', 'OCHOCIENTOS', 'NOVECIENTOS',
)  # fmt: skip

# (singular noun, plural noun, suffix); any other currency prints as MXN, as legacy's did.
_CURRENCIES = {
    CurrencyCode.MXN: ('PESO', 'PESOS', 'M. N.'),
    CurrencyCode.USD: ('DÓLAR AMERICANO', 'DÓLARES AMERICANOS', 'USD'),
    CurrencyCode.EUR: ('EURO', 'EUROS', 'EUR'),
}


def _below_thousand(n: int) -> list[str]:
    """1–999, with a final "uno" shortened: every caller puts a noun, "mil" or "millón" after it."""
    words = []
    hundreds, rest = divmod(n, 100)
    if hundreds:
        words.append('CIEN' if hundreds == 1 and rest == 0 else _HUNDREDS[hundreds])
    if rest >= 30:
        tens, unit = divmod(rest, 10)
        words.append(_TENS[tens])
        if unit:
            words += ['Y', _UNITS[unit]]
    elif rest:
        words.append(_UNITS[rest])
    if words[-1] == 'UNO':
        words[-1] = 'UN'
    elif words[-1] == 'VEINTIUNO':
        words[-1] = 'VEINTIÚN'
    return words


def _below_million(n: int) -> list[str]:
    thousands, rest = divmod(n, 1000)
    words = []
    if thousands:
        words += ([] if thousands == 1 else _below_thousand(thousands)) + ['MIL']
    if rest:
        words += _below_thousand(rest)
    return words


def _integer(n: int) -> list[str]:
    if n == 0:
        return ['CERO']
    millions, rest = divmod(n, 1_000_000)
    words = []
    if millions == 1:
        words += ['UN', 'MILLÓN']
    elif millions:
        words += _below_million(millions) + ['MILLONES']
    if rest:
        words += _below_million(rest)
    elif millions:
        words.append('DE')
    return words


def amount_in_words(amount: Decimal, currency: int) -> str:
    singular, plural, suffix = _CURRENCIES.get(currency, _CURRENCIES[CurrencyCode.MXN])
    amount = amount.quantize(Decimal('0.01'), ROUND_HALF_UP)
    units = int(amount)
    cents = int((amount - units) * 100)
    noun = singular if units == 1 else plural
    return ' '.join([*_integer(units), noun, f'{cents:02d}/100', suffix])
