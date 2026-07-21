"""Parsing and validation of SAT CSD (Certificado de Sello Digital) pairs.

A CSD arrives as two DER files: an X.509 certificate (`.cer`) and an encrypted PKCS#8
private key (`.key`), unlocked by a password. Everything the API stores as metadata is read
back out of the certificate itself rather than trusted from the upload form.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime

from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey
from cryptography.hazmat.primitives.serialization import load_der_private_key, load_pem_private_key
from cryptography.x509 import (
    Certificate,
    NameOID,
    load_der_x509_certificate,
    load_pem_x509_certificate,
)


@dataclass(frozen=True)
class ParsedCsd:
    certificate_id: str
    taxpayer: str | None
    valid_from: datetime
    valid_to: datetime


def _load_certificate(data: bytes) -> Certificate:
    try:
        return load_der_x509_certificate(data)
    except ValueError:
        pass
    try:
        return load_pem_x509_certificate(data)
    except ValueError as e:
        raise ValueError('Unreadable certificate — expected a DER or PEM encoded .cer') from e


def _load_key(data: bytes, password: bytes) -> RSAPrivateKey:
    """Load the private key, distinguishing a wrong password from an unreadable file.

    `cryptography` reports both as ValueError, so the file is probed with the password and,
    when that fails, without one — a key that opens unencrypted is a malformed CSD, while a
    key that refuses both is most likely the wrong password.
    """
    for loader in (load_der_private_key, load_pem_private_key):
        try:
            key = loader(data, password=password)
        except (ValueError, TypeError):
            continue
        if not isinstance(key, RSAPrivateKey):
            raise ValueError('Private key is not RSA — not a valid CSD key')
        return key
    for loader in (load_der_private_key, load_pem_private_key):
        try:
            loader(data, password=None)
        except (ValueError, TypeError):
            continue
        else:
            raise ValueError('Private key is not password protected — not a valid CSD key')
    raise ValueError('Wrong password, or unreadable private key — expected a DER or PEM .key')


def _certificate_number(certificate: Certificate) -> str:
    """SAT carries the 20-digit certificate number as the ASCII bytes of the X.509 serial."""
    raw = certificate.serial_number
    as_bytes = raw.to_bytes((raw.bit_length() + 7) // 8, 'big')
    try:
        decoded = as_bytes.decode('ascii')
    except UnicodeDecodeError:
        return str(raw)
    return decoded if decoded.isdigit() else str(raw)


def _taxpayer(certificate: Certificate) -> str | None:
    """Read the holder's RFC out of the subject.

    SAT puts it in x500UniqueIdentifier, on its own or as `RFC / CURP`, and falls back to
    serialNumber on older certificates. Returns None when neither is present, leaving the
    caller to decide whether an unverifiable holder is acceptable.
    """
    for oid in (NameOID.X500_UNIQUE_IDENTIFIER, NameOID.SERIAL_NUMBER):
        for attribute in certificate.subject.get_attributes_for_oid(oid):
            value = attribute.value
            if isinstance(value, bytes):
                continue
            candidate = value.split('/')[0].strip()
            if 12 <= len(candidate) <= 13:
                return candidate.upper()
    return None


def _parse_sync(certificate_data: bytes, key_data: bytes, key_password: bytes) -> ParsedCsd:
    certificate = _load_certificate(certificate_data)
    key = _load_key(key_data, key_password)

    certificate_key = certificate.public_key()
    if not isinstance(certificate_key, RSAPublicKey):
        raise ValueError('Certificate does not carry an RSA public key — not a valid CSD')
    if key.public_key().public_numbers() != certificate_key.public_numbers():
        raise ValueError('Private key does not match the certificate')

    return ParsedCsd(
        certificate_id=_certificate_number(certificate),
        taxpayer=_taxpayer(certificate),
        # The column is naive; SAT certificates are issued in UTC.
        valid_from=certificate.not_valid_before_utc.replace(tzinfo=None),
        valid_to=certificate.not_valid_after_utc.replace(tzinfo=None),
    )


async def parse_csd(certificate_data: bytes, key_data: bytes, key_password: bytes) -> ParsedCsd:
    """Validate a CSD pair and return the metadata read from the certificate.

    Raises ValueError when the certificate or key is unreadable, the password does not open
    the key, or the key does not belong to the certificate.
    """
    return await asyncio.to_thread(_parse_sync, certificate_data, key_data, key_password)
