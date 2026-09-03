"""Server-generated identifiers for records whose code a client may leave blank."""

import secrets
from typing import Final

#: Digits and letters minus 0/O, 1/I/L and U — the pairs a human mistypes reading a code aloud,
#: and the vowel that turns a random token into a word someone has to apologise for.
_ALPHABET: Final = '23456789ABCDEFGHJKMNPQRSTVWXYZ'

#: Machine-assigned customer code (#197). `CUS-` is unused across all 10,933 rows in mbe_dev, and
#: collides with neither live convention there — bare digits (1,292 rows) and `ID<digits>` (1,281).
CUSTOMER_PREFIX: Final = 'CUS-'


def generate_code(prefix: str, *, length: int = 8) -> str:
    """A `<prefix><token>` code, unique by construction rather than by index (#197).

    Random rather than a sequence (#193's option B): a sequence needs a `MAX()` lookup or a
    counter row, and that lookup is what makes concurrent creates race. This has no lookup.

    No collision check, deliberately. `customer.code` carries no index at all, so a check-then-
    insert could not close the race anyway, and a collision's outcome is a duplicate code — which
    the table already holds 1,634 of. At 30^8 the per-insert chance against 10,933 rows is ~2e-8.

    Parameterised by prefix because #197 asked that customers and products not end up with two
    different-looking machine codes; a second caller changes the prefix, not the scheme.
    """
    return prefix + ''.join(secrets.choice(_ALPHABET) for _ in range(length))
