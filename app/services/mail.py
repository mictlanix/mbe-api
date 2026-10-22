"""Outgoing mail: one message, over SMTP, for any document (spec 024).

Nothing here knows what is being sent. The quote email builds a `Message` and hands it over, and
the invoice email (mictlanix/mbe-roadmap#50) will do the same with the CFDI's PDF and XML attached.

The SMTP conversation is the standard library's, run in a worker thread: `smtplib` blocks, and the
event loop must not (constitution VI). A send either succeeds or raises `MailError`. Unlike the
current system, which sent in a background task and reported success whatever happened, the caller
learns the outcome and can tell the user.
"""

import asyncio
import smtplib
from collections.abc import Sequence
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Protocol

from app.core.config import settings


@dataclass(frozen=True)
class Attachment:
    filename: str
    content_type: str
    data: bytes


@dataclass(frozen=True)
class Message:
    to: Sequence[str]
    subject: str
    body: str
    attachments: Sequence[Attachment] = field(default_factory=tuple)


class MailError(Exception):
    """The server refused the message, or could not be reached."""


class Mailer(Protocol):
    async def send(self, message: Message) -> None: ...


class SmtpMailer:
    def __init__(self, config: object) -> None:
        self._config = config

    def _build(self, message: Message) -> EmailMessage:
        msg = EmailMessage()
        msg['From'] = self._config.mail_from
        msg['To'] = ', '.join(message.to)
        msg['Subject'] = message.subject
        if self._config.mail_reply_to:
            msg['Reply-To'] = self._config.mail_reply_to
        msg.set_content(message.body)
        for attachment in message.attachments:
            maintype, subtype = attachment.content_type.split('/', 1)
            msg.add_attachment(
                attachment.data, maintype=maintype, subtype=subtype, filename=attachment.filename
            )
        return msg

    def _send_sync(self, msg: EmailMessage) -> None:
        config = self._config
        client = smtplib.SMTP_SSL if config.smtp_security == 'ssl' else smtplib.SMTP
        try:
            session = client(config.smtp_host, config.smtp_port, timeout=config.smtp_timeout)
            try:
                if config.smtp_security == 'starttls':
                    session.starttls()
                if config.smtp_user:
                    session.login(config.smtp_user, config.smtp_password)
                session.send_message(msg)
            finally:
                # The server has the message once `send_message` returns; a failed goodbye must not
                # report a delivered email as failed, so the caller records it.
                try:
                    session.quit()
                except (smtplib.SMTPException, OSError):
                    pass
        except (smtplib.SMTPException, OSError) as exc:
            raise MailError(str(exc) or type(exc).__name__) from exc

    async def send(self, message: Message) -> None:
        await asyncio.to_thread(self._send_sync, self._build(message))


def get_mailer() -> Mailer | None:
    """The configured mailer, or None while mail is off. A FastAPI dependency, so tests swap it."""
    if not settings.smtp_host or not settings.mail_from:
        return None
    return SmtpMailer(settings)
