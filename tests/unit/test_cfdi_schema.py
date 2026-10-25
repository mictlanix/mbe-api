"""The schema helper itself: a real stamped invoice passes, a broken one does not."""

from pathlib import Path

import pytest
from lxml import etree

from tests.cfdi_schema import assert_valid_cfdi40

_FIXTURE = Path(__file__).parents[1] / 'fixtures' / 'cfdi' / 'invoice.xml'
_CFDI = '{http://www.sat.gob.mx/cfd/4}'


def _without_stamp() -> etree._Element:
    root = etree.parse(str(_FIXTURE)).getroot()
    for complement in root.findall(f'{_CFDI}Complemento'):
        root.remove(complement)
    return root


def test_a_stamped_invoice_without_its_stamp_is_valid() -> None:
    assert_valid_cfdi40(etree.tostring(_without_stamp()))


def test_a_missing_required_attribute_is_reported() -> None:
    root = _without_stamp()
    del root.attrib['Moneda']

    with pytest.raises(AssertionError, match='Moneda'):
        assert_valid_cfdi40(etree.tostring(root))


_PAYMENT = Path(__file__).parents[1] / 'fixtures' / 'cfdi' / 'payment.xml'
_PAGOS = '{http://www.sat.gob.mx/Pagos20}'


def _payment_without_stamp() -> etree._Element:
    root = etree.parse(str(_PAYMENT)).getroot()
    for timbre in root.iter('{http://www.sat.gob.mx/TimbreFiscalDigital}TimbreFiscalDigital'):
        timbre.getparent().remove(timbre)
    return root


def test_a_payment_receipt_is_checked_against_pagos_20() -> None:
    assert_valid_cfdi40(etree.tostring(_payment_without_stamp()))


def test_a_broken_pagos_complement_is_reported() -> None:
    root = _payment_without_stamp()
    del next(root.iter(f'{_PAGOS}Totales')).attrib['MontoTotalPagos']

    with pytest.raises(AssertionError, match='MontoTotalPagos'):
        assert_valid_cfdi40(etree.tostring(root))
