"""The mail capability: one message, over SMTP, for any document (spec 024, FR-012–FR-014).

`smtplib`'s client classes are replaced with mocks, so these pin the SMTP conversation each security
mode holds and the message it hands over, without a server. Nothing here knows about quotes: the
invoice email reuses this module unchanged.
"""

import smtplib
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

from app.services import mail

PDF = b'%PDF-1.7 fake'


def _settings(**overrides: object) -> SimpleNamespace:
    values = {
        'smtp_host': 'smtp.example.com',
        'smtp_port': 587,
        'smtp_security': 'starttls',
        'smtp_user': 'sender@example.com',
        'smtp_password': 'app-password',
        'smtp_timeout': 30,
        'mail_from': 'noreply@example.com',
        'mail_reply_to': '',
    }
    return SimpleNamespace(**{**values, **overrides})


def _message(**overrides: object) -> mail.Message:
    values = {
        'to': ['ana@cliente.mx', 'compras@cliente.mx'],
        'subject': 'Matriz Cotización 7',
        'body': 'Le enviamos la cotización 7.',
        'attachments': [mail.Attachment('cotizacion-00000007.pdf', 'application/pdf', PDF)],
    }
    return mail.Message(**{**values, **overrides})


async def _send(settings: SimpleNamespace, message: mail.Message) -> tuple[MagicMock, MagicMock]:
    """Sends through mocked `SMTP` and `SMTP_SSL`; returns both classes for inspection."""
    with (
        patch.object(mail.smtplib, 'SMTP') as plain,
        patch.object(mail.smtplib, 'SMTP_SSL') as ssl,
    ):
        await mail.SmtpMailer(settings).send(message)
    return plain, ssl


def _sent(client_class: MagicMock):  # noqa: ANN202 — the EmailMessage handed to send_message
    return client_class.return_value.send_message.call_args.args[0]


class TestTheSmtpConversation:
    async def test_starttls_upgrades_logs_in_sends_and_quits_in_that_order(self) -> None:
        plain, ssl = await _send(_settings(), _message())

        plain.assert_called_once_with('smtp.example.com', 587, timeout=30)
        session = plain.return_value
        assert [c[0] for c in session.method_calls] == [
            'starttls',
            'login',
            'send_message',
            'quit',
        ]
        assert session.login.call_args == call('sender@example.com', 'app-password')
        ssl.assert_not_called()

    async def test_ssl_uses_an_implicit_tls_connection(self) -> None:
        plain, ssl = await _send(_settings(smtp_security='ssl', smtp_port=465), _message())

        ssl.assert_called_once_with('smtp.example.com', 465, timeout=30)
        ssl.return_value.starttls.assert_not_called()
        ssl.return_value.send_message.assert_called_once()
        plain.assert_not_called()

    async def test_a_local_relay_without_a_user_neither_upgrades_nor_logs_in(self) -> None:
        plain, _ = await _send(_settings(smtp_security='none', smtp_user=''), _message())

        session = plain.return_value
        session.starttls.assert_not_called()
        session.login.assert_not_called()
        session.send_message.assert_called_once()


class TestTheMessage:
    async def test_headers_name_the_sender_every_recipient_and_the_subject(self) -> None:
        plain, _ = await _send(_settings(), _message())

        sent = _sent(plain)
        assert sent['From'] == 'noreply@example.com'
        assert sent['To'] == 'ana@cliente.mx, compras@cliente.mx'
        assert sent['Subject'] == 'Matriz Cotización 7'
        assert sent['Reply-To'] is None

    async def test_a_configured_reply_to_is_set(self) -> None:
        plain, _ = await _send(_settings(mail_reply_to='ventas@example.com'), _message())

        assert _sent(plain)['Reply-To'] == 'ventas@example.com'

    async def test_the_body_is_utf8_plain_text(self) -> None:
        plain, _ = await _send(_settings(), _message())

        body = _sent(plain).get_body(preferencelist=('plain',))
        assert body.get_content_type() == 'text/plain'
        assert body.get_content().strip() == 'Le enviamos la cotización 7.'

    async def test_each_attachment_carries_its_name_type_and_bytes(self) -> None:
        plain, _ = await _send(_settings(), _message())

        attachments = list(_sent(plain).iter_attachments())
        assert len(attachments) == 1
        assert attachments[0].get_filename() == 'cotizacion-00000007.pdf'
        assert attachments[0].get_content_type() == 'application/pdf'
        assert attachments[0].get_content() == PDF


class TestFailures:
    @pytest.mark.parametrize(
        'error',
        [
            smtplib.SMTPRecipientsRefused({'ana@cliente.mx': (550, b'no such user')}),
            smtplib.SMTPAuthenticationError(535, b'bad credentials'),
            OSError('timed out'),
        ],
    )
    async def test_server_and_network_failures_become_a_mail_error(
        self, error: Exception
    ) -> None:
        with (
            patch.object(mail.smtplib, 'SMTP') as plain,
            patch.object(mail.smtplib, 'SMTP_SSL'),
        ):
            plain.return_value.send_message.side_effect = error
            with pytest.raises(mail.MailError) as raised:
                await mail.SmtpMailer(_settings()).send(_message())

        assert str(raised.value)


    async def test_a_failed_goodbye_after_a_sent_message_is_not_a_failure(self) -> None:
        with (
            patch.object(mail.smtplib, 'SMTP') as plain,
            patch.object(mail.smtplib, 'SMTP_SSL'),
        ):
            plain.return_value.quit.side_effect = smtplib.SMTPServerDisconnected('gone')
            await mail.SmtpMailer(_settings()).send(_message())

        plain.return_value.send_message.assert_called_once()


class TestConfiguration:
    @pytest.mark.parametrize(
        'overrides', [{'smtp_host': ''}, {'mail_from': ''}, {'smtp_host': '', 'mail_from': ''}]
    )
    def test_mail_is_off_without_a_server_or_a_sender(self, overrides: dict) -> None:
        with patch.object(mail, 'settings', _settings(**overrides)):
            assert mail.get_mailer() is None

    def test_mail_is_on_with_both(self) -> None:
        with patch.object(mail, 'settings', _settings()):
            assert isinstance(mail.get_mailer(), mail.SmtpMailer)
