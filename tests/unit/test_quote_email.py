"""What a quote email says and who it may go to (spec 024, FR-005, FR-006, FR-008)."""

import pytest
from pydantic import ValidationError

from app.schemas.sales_quote import QuoteEmailRequest
from app.services import sales_quote_service


class TestTheRecipients:
    @pytest.mark.parametrize(
        'to',
        [
            [],
            [f'r{n}@cliente.mx' for n in range(11)],
            ['no-at-sign'],
            ['a@b'],
            ['a b@c.mx'],
            ['a@b.mx\r\nBcc: x@y.mx'],
            [f'{"a" * 245}@cliente.mx'],
        ],
        ids=['none', 'eleven', 'no-at', 'no-dot', 'space', 'header-injection', 'too-long'],
    )
    def test_a_bad_list_is_refused(self, to: list[str]) -> None:
        with pytest.raises(ValidationError):
            QuoteEmailRequest(to=to)

    def test_addresses_are_trimmed_and_deduplicated_keeping_the_first_spelling(self) -> None:
        request = QuoteEmailRequest(to=[' Ana@X.mx ', 'ana@x.mx', 'b@y.mx'])

        assert request.to == ['Ana@X.mx', 'b@y.mx']


class TestTheMessage:
    def test_subject_body_and_attachment_follow_the_current_system(self) -> None:
        message = sales_quote_service.quote_email_message(
            quote_id=7,
            facility_name='Matriz',
            salesperson='Ana Ruiz',
            to=['ana@cliente.mx'],
            pdf=b'%PDF',
        )

        assert message.to == ['ana@cliente.mx']
        assert message.subject == 'Matriz Cotización 7'
        assert message.body == (
            'Le enviamos la cotización 7.\n'
            'Agradecemos su atención y esperamos servirle muy pronto.\n'
            'Atte: Ana Ruiz.'
        )
        [attachment] = message.attachments
        assert attachment.filename == 'cotizacion-00000007.pdf'
        assert attachment.content_type == 'application/pdf'
        assert attachment.data == b'%PDF'


class TestTheAuditLine:
    def test_it_lists_every_recipient(self) -> None:
        assert sales_quote_service._recipients_context(['a@x.mx', 'b@y.mx']) == 'To: a@x.mx, b@y.mx'

    def test_a_long_list_is_cut_to_the_column_with_an_ellipsis(self) -> None:
        addresses = [f'{"a" * 240}{n}@cliente.mx' for n in range(10)]

        line = sales_quote_service._recipients_context(addresses)

        assert len(line) == 1000
        assert line.endswith('…')
