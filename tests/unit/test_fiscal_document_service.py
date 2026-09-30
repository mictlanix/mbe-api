"""Fiscal document status, search and list totals (#230, spec 020, research R10)."""

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import fiscal_document_service as svc

_INVOICE = (Path(__file__).parent.parent / 'fixtures' / 'cfdi' / 'invoice.xml').read_text(
    encoding='utf-8'
)


@pytest.mark.parametrize(
    ('completed', 'cancelled', 'expected'),
    [
        (False, False, 'draft'),
        (True, False, 'issued'),
        (True, True, 'cancelled'),
        (False, True, 'cancelled'),
    ],
)
def test_document_status(completed: bool, cancelled: bool, expected: str) -> None:
    document = SimpleNamespace(completed=completed, cancelled=cancelled)
    assert svc.document_status(document) == expected


@pytest.mark.parametrize(
    ('search', 'expected'),
    [
        ('GR4776', ('batch_serial', ('GR', 4776))),
        ('gr 4776', ('batch_serial', ('GR', 4776))),
        ('4776', ('serial', 4776)),
        ('bdcad3dc-5428-5e2d-89ae-588872e59495', ('uuid', 'BDCAD3DC-5428-5E2D-89AE-588872E59495')),
        ('TOMAS', ('text', 'TOMAS')),
        ('  CLIENTE DEMO ', ('text', 'CLIENTE DEMO')),
    ],
)
def test_classify_search(search: str, expected: tuple) -> None:
    assert svc.classify_search(search) == expected


def test_xml_total_reads_cfdi_33_and_40() -> None:
    assert svc.xml_total(_INVOICE) == Decimal('3160.00')


def test_xml_total_reads_the_lowercase_total_of_older_versions() -> None:
    cfd = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/3" version="3.2"'
        ' subTotal="100.00" total="116.00"/>'
    )
    assert svc.xml_total(cfd) == Decimal('116.00')


@pytest.mark.parametrize('data', [None, '', 'not xml', '<Comprobante/>'])
def test_xml_total_is_none_without_a_readable_total(data: str | None) -> None:
    assert svc.xml_total(data) is None
