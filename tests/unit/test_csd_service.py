"""Covers CSD parsing: a wrong password, a mismatched key pair and a foreign certificate
must all be rejected before anything is stored."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509 import load_der_x509_certificate
from cryptography.x509.oid import NameOID

from app.services.csd_service import parse_csd

# SAT carries the 20-digit certificate number as the ASCII bytes of the X.509 serial.
_CERTIFICATE_NUMBER = '00001000000500003416'
_RFC = 'AAA010101AAA'
_PASSWORD = b'12345678a'


def _serial_from(number: str) -> int:
    return int.from_bytes(number.encode('ascii'), 'big')


def _make_csd(
    *,
    rfc: str = _RFC,
    number: str = _CERTIFICATE_NUMBER,
    password: bytes = _PASSWORD,
    key: rsa.RSAPrivateKey | None = None,
) -> tuple[bytes, bytes]:
    """Build a self-signed stand-in for a SAT CSD pair, in the DER encodings SAT ships."""
    signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    key = key or signing_key
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, 'Test Issuer'),
            x509.NameAttribute(NameOID.X500_UNIQUE_IDENTIFIER, rfc),
        ]
    )
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(signing_key.public_key())
        .serial_number(_serial_from(number))
        .not_valid_before(datetime(2024, 1, 1, tzinfo=UTC))
        .not_valid_after(datetime(2024, 1, 1, tzinfo=UTC) + timedelta(days=1461))
        .sign(signing_key, hashes.SHA256())
    )
    return (
        certificate.public_bytes(serialization.Encoding.DER),
        key.private_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.BestAvailableEncryption(password),
        ),
    )


@pytest.mark.asyncio
async def test_parses_number_rfc_and_validity_from_the_certificate() -> None:
    certificate_data, key_data = _make_csd()

    parsed = await parse_csd(certificate_data, key_data, _PASSWORD)

    assert parsed.certificate_id == _CERTIFICATE_NUMBER
    assert parsed.taxpayer == _RFC
    # The certificate says 2024-01-01T00:00Z; the column stores Mexico City local time,
    # which since October 2022 is a fixed UTC-6.
    assert parsed.valid_from == datetime(2023, 12, 31, 18, 0)
    assert parsed.valid_to == datetime(2027, 12, 31, 18, 0)


@pytest.mark.asyncio
async def test_validity_is_converted_to_mexico_city_local_time() -> None:
    """Matches the convention of every row the legacy system wrote (verified against
    mbe_demo): storing UTC would put new certificates 6 hours off from the existing ones."""
    certificate_data, key_data = _make_csd()

    parsed = await parse_csd(certificate_data, key_data, _PASSWORD)

    certificate = load_der_x509_certificate(certificate_data)
    expected = certificate.not_valid_after_utc.astimezone(ZoneInfo('America/Mexico_City'))
    assert parsed.valid_to == expected.replace(tzinfo=None)
    assert parsed.valid_to != certificate.not_valid_after_utc.replace(tzinfo=None)


@pytest.mark.asyncio
async def test_wrong_password_is_rejected() -> None:
    certificate_data, key_data = _make_csd()

    with pytest.raises(ValueError, match='Wrong password'):
        await parse_csd(certificate_data, key_data, b'not-the-password')


@pytest.mark.asyncio
async def test_key_from_another_pair_is_rejected() -> None:
    """A correct password on the wrong key is otherwise undetectable until stamping fails."""
    certificate_data, _ = _make_csd()
    _, foreign_key = _make_csd(key=rsa.generate_private_key(public_exponent=65537, key_size=2048))

    with pytest.raises(ValueError, match='does not match'):
        await parse_csd(certificate_data, foreign_key, _PASSWORD)


@pytest.mark.asyncio
async def test_unreadable_certificate_is_rejected() -> None:
    _, key_data = _make_csd()

    with pytest.raises(ValueError, match='Unreadable certificate'):
        await parse_csd(b'not a certificate', key_data, _PASSWORD)


@pytest.mark.asyncio
async def test_unencrypted_key_is_rejected() -> None:
    certificate_data, _ = _make_csd()
    plain_key = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )

    with pytest.raises(ValueError, match='not password protected'):
        await parse_csd(certificate_data, plain_key, _PASSWORD)
