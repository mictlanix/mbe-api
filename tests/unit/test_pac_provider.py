"""The PAC provider's types (spec 039, research R14): the CSD never reaches a log."""

from app.services.pac.provider import SIMULATED_PAC_RFC, Csd


def test_the_csd_repr_hides_the_key_and_the_password() -> None:
    csd = Csd(certificate=b'CERT-BYTES', key=b'SECRET-KEY-BYTES', password=b'hunter2-password')

    text = repr(csd)

    assert 'SECRET-KEY-BYTES' not in text
    assert 'hunter2-password' not in text
    assert '53454352' not in text  # nor the key's bytes in another encoding
    assert str(csd) == text


def test_the_simulator_has_its_own_provider_rfc() -> None:
    assert SIMULATED_PAC_RFC == 'SIM010101SIM'
