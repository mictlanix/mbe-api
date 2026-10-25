"""Validates CFDI 4.0 XML against SAT's own schemas, vendored in `tests/fixtures/sat/` (SC-001).

`cfdv40.xsd` validates `cfdi:Complemento` strictly, so the schema set also holds `Pagos20.xsd`
(spec 028) through a local wrapper that imports both.
"""

from functools import cache
from pathlib import Path

from lxml import etree

_XSD = Path(__file__).parent / 'fixtures' / 'sat' / 'cfdi40_with_complements.xsd'


@cache
def _schema() -> etree.XMLSchema:
    return etree.XMLSchema(etree.parse(str(_XSD)))


def assert_valid_cfdi40(xml: str | bytes) -> None:
    data = xml.encode('utf-8') if isinstance(xml, str) else xml
    schema = _schema()
    if not schema.validate(etree.fromstring(data)):
        raise AssertionError('\n'.join(str(error) for error in schema.error_log))
