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
