"""What a CFDI email says, and the QR its PDF carries (spec 028, FR-013, FR-014)."""

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.rendering.formatting import qr_data_uri
from app.services import cfdi
from app.services.fiscal_email import fiscal_email_message
from app.services.print_contexts import fiscal_document_context

_CFDI_FIXTURES = Path(__file__).parent.parent / 'fixtures' / 'cfdi'
_UUID = 'BDCAD3DC-5428-5E2D-89AE-588872E59495'
_QR_URL = 'https://verificacfdi.facturaelectronica.sat.gob.mx/default.aspx?id=X'


def _message(**overrides):  # noqa: ANN202
    values = dict(
        issuer='EKU9003173C9',
        recipient='XAÑ010101AB1',
        batch='RH',
        serial=11405,
        total=Decimal('3160.00'),
        uuid=_UUID,
        qr_url=_QR_URL,
        to=['ana@cliente.mx', 'b@cliente.mx'],
        pdf=b'%PDF',
        xml='<cfdi:Comprobante Nombre="Ñ"/>',
    )
    values.update(overrides)
    return fiscal_email_message(**values)


class TestTheMessage:
    def test_it_goes_to_every_recipient(self) -> None:
        assert _message().to == ['ana@cliente.mx', 'b@cliente.mx']

    def test_the_subject_names_the_issuer_and_the_folio(self) -> None:
        assert _message().subject == 'Comprobante Fiscal - EKU9003173C9 - RH011405'

    def test_the_body_is_the_current_systems(self) -> None:
        assert _message().body == (
            'Has recibido el siguiente comprobante fiscal digital por internet (CFDI).\n'
            '\n'
            f'Folio Fiscal: {_UUID}\n'
            'RFC Emisor: EKU9003173C9\n'
            'RFC Receptor: XAÑ010101AB1\n'
            'Total: $3,160.00\n'
            '\n'
            'Puedes hacer la validación del mismo en el sitio oficial del SAT.\n'
            f'{_QR_URL}\n'
            '\n'
            'Adjunto, podrás encontrar tu comprobante en los formatos PDF y XML.\n'
            '\n'
            'Gracias por utilizar nuestros servicios.'
        )

    def test_a_payment_receipts_zero_total_is_written_as_money(self) -> None:
        assert 'Total: $0.00\n' in _message(total=Decimal('0')).body

    def test_the_pdf_and_the_xml_are_attached_under_the_legacy_name(self) -> None:
        pdf, xml = _message().attachments

        assert (pdf.filename, pdf.content_type, pdf.data) == (
            'EKU9003173C9-RH011405.pdf',
            'application/pdf',
            b'%PDF',
        )
        assert (xml.filename, xml.content_type, xml.data) == (
            'EKU9003173C9-RH011405.xml',
            'application/xml',
            '<cfdi:Comprobante Nombre="Ñ"/>'.encode(),
        )

    def test_a_document_without_a_batch_keeps_the_padded_serial(self) -> None:
        message = _message(batch=None, serial=6)

        assert message.subject == 'Comprobante Fiscal - EKU9003173C9 - 000006'
        assert message.attachments[0].filename == 'EKU9003173C9-000006.pdf'


# ── FR-014: the emailed PDF keeps SAT's verification QR ─────────────────────


class _Result:
    def scalars(self) -> '_Result':
        return self

    def first(self) -> None:
        return None


class _Db:
    """No catalog rows: the QR is built from the XML alone."""

    async def get(self, model: type, key: object) -> None:
        return None

    async def execute(self, statement: object) -> _Result:
        return _Result()


@pytest.mark.parametrize(
    ('fixture', 'type_'), [('invoice', 0), ('credit_note', 100), ('payment', 200)]
)
async def test_the_pdf_carries_the_sat_qr(fixture: str, type_: int) -> None:
    xml = (_CFDI_FIXTURES / f'{fixture}.xml').read_text(encoding='utf-8')
    document = SimpleNamespace(
        fiscal_document_id=1,
        type=type_,
        issuer='EKU9003173C9',
        issuer_regime_name=None,
        batch=None,
        serial=1,
        reference=None,
        cancelled=False,
        cancellation_date=None,
        lines=[],
    )

    _, context = await fiscal_document_context(_Db(), document, xml)

    payload = cfdi.sat_qr_payload(cfdi.parse(xml))
    assert payload.startswith(cfdi.SAT_VERIFICATION_URL)
    assert context['qr'] == qr_data_uri(payload)
