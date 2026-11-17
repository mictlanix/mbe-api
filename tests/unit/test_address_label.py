"""`address_label` must read exactly as mbe-ui's `formatAddress` does (#324, #326)."""

from types import SimpleNamespace

from app.services.address_service import address_label


def _address(**overrides: str | None) -> SimpleNamespace:
    fields = {
        'street': 'Av. Reforma',
        'exterior_number': '100',
        'interior_number': None,
        'neighborhood': 'Centro',
        'city': 'Ciudad de México',
        'state': 'CDMX',
    }
    return SimpleNamespace(**(fields | overrides))


def test_street_number_neighbourhood_and_city() -> None:
    assert address_label(_address()) == 'Av. Reforma 100, Centro, Ciudad de México'


def test_an_interior_number_follows_the_exterior_one() -> None:
    assert address_label(_address(interior_number='3B')) == (
        'Av. Reforma 100 Int. 3B, Centro, Ciudad de México'
    )


def test_an_empty_interior_number_is_left_out() -> None:
    assert address_label(_address(interior_number='')) == (
        'Av. Reforma 100, Centro, Ciudad de México'
    )


def test_the_state_stands_in_for_a_missing_city() -> None:
    assert address_label(_address(city=None)) == 'Av. Reforma 100, Centro, CDMX'
    assert address_label(_address(city='')) == 'Av. Reforma 100, Centro, CDMX'
