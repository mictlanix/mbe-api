"""Amounts in words, in legacy's format with correct Spanish (#230, research R10)."""

from decimal import Decimal

import pytest

from app.enums import CurrencyCode
from app.rendering.words import amount_in_words


@pytest.mark.parametrize(
    ('amount', 'expected'),
    [
        ('1234.50', 'MIL DOSCIENTOS TREINTA Y CUATRO PESOS 50/100 M. N.'),
        ('1.00', 'UN PESO 00/100 M. N.'),
        ('1.50', 'UN PESO 50/100 M. N.'),
        ('0.99', 'CERO PESOS 99/100 M. N.'),
        ('21', 'VEINTIÚN PESOS 00/100 M. N.'),
        ('31', 'TREINTA Y UN PESOS 00/100 M. N.'),
        ('11', 'ONCE PESOS 00/100 M. N.'),
        ('100', 'CIEN PESOS 00/100 M. N.'),
        ('101', 'CIENTO UN PESOS 00/100 M. N.'),
        ('115', 'CIENTO QUINCE PESOS 00/100 M. N.'),
        ('500', 'QUINIENTOS PESOS 00/100 M. N.'),
        ('999', 'NOVECIENTOS NOVENTA Y NUEVE PESOS 00/100 M. N.'),
        ('1000', 'MIL PESOS 00/100 M. N.'),
        ('2001', 'DOS MIL UN PESOS 00/100 M. N.'),
        ('21000', 'VEINTIÚN MIL PESOS 00/100 M. N.'),
        ('100000', 'CIEN MIL PESOS 00/100 M. N.'),
        ('101000', 'CIENTO UN MIL PESOS 00/100 M. N.'),
        ('1000000', 'UN MILLÓN DE PESOS 00/100 M. N.'),
        ('1000001', 'UN MILLÓN UN PESOS 00/100 M. N.'),
        ('1100000', 'UN MILLÓN CIEN MIL PESOS 00/100 M. N.'),
        ('2000000', 'DOS MILLONES DE PESOS 00/100 M. N.'),
        ('21000000', 'VEINTIÚN MILLONES DE PESOS 00/100 M. N.'),
        ('1000000000', 'MIL MILLONES DE PESOS 00/100 M. N.'),
        ('1001000000', 'MIL UN MILLONES DE PESOS 00/100 M. N.'),
    ],
)
def test_mxn(amount: str, expected: str) -> None:
    assert amount_in_words(Decimal(amount), CurrencyCode.MXN) == expected


def test_usd() -> None:
    assert amount_in_words(Decimal('1234.50'), CurrencyCode.USD) == (
        'MIL DOSCIENTOS TREINTA Y CUATRO DÓLARES AMERICANOS 50/100 USD'
    )


def test_usd_singular() -> None:
    assert amount_in_words(Decimal('1'), CurrencyCode.USD) == 'UN DÓLAR AMERICANO 00/100 USD'


def test_eur() -> None:
    assert amount_in_words(Decimal('1234.50'), CurrencyCode.EUR) == (
        'MIL DOSCIENTOS TREINTA Y CUATRO EUROS 50/100 EUR'
    )


def test_eur_singular() -> None:
    assert amount_in_words(Decimal('1.25'), CurrencyCode.EUR) == 'UN EURO 25/100 EUR'


def test_unknown_currency_falls_back_to_mxn() -> None:
    assert amount_in_words(Decimal('2'), 99) == 'DOS PESOS 00/100 M. N.'


def test_rounds_half_away_from_zero() -> None:
    assert amount_in_words(Decimal('0.005'), CurrencyCode.MXN) == 'CERO PESOS 01/100 M. N.'
    assert amount_in_words(Decimal('1.995'), CurrencyCode.MXN) == 'DOS PESOS 00/100 M. N.'


@pytest.mark.parametrize(
    'amount', ['0', '1', '21', '101', '1234.5', '21021', '1100000', '1000000000', '987654321.98']
)
@pytest.mark.parametrize('currency', list(CurrencyCode))
def test_no_double_spaces(amount: str, currency: CurrencyCode) -> None:
    words = amount_in_words(Decimal(amount), currency)

    assert '  ' not in words
    assert words == words.strip()
