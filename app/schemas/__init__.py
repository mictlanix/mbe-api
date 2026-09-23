"""Response and request shapes.

**`Field(description=...)` — when to use it (#175).** Use it where a field's *name* does not carry
its meaning, and getting it wrong is silent. Not as general documentation: a `#:` comment is the
house style for explaining a field to the next person reading this file, and it does not reach the
OpenAPI schema, which is the right trade for most fields.

The case that earns a description is a field a client can plausibly reach for and be wrong about
without anything failing. `customer_name` and `customer_display_name` are the example the
convention was written for: two nullable strings, one the per-document override and one the
customer's own name, where the more natural-sounding name is the wrong one and is null on
essentially every row — so the mistake reproduces silently on every request (#172, #173, #174).

`tests/unit/test_field_descriptions.py` pins the fields that carry one, so a rename or a new
lookalike field does not quietly drop the distinction from the generated client.

**Every datetime earns one (#228).** `format: date-time` promises RFC 3339, which carries an
offset; the wire carries naive business wall-clock time. A generated client read that as UTC and
shifted every date it showed by six hours (mictlanix/mbe-ui#176). So datetimes are declared
`LocalDateTime`, which carries the convention into the schema and converts an inbound offset —
the database driver would otherwise drop it without converting.
`tests/unit/test_datetime_contract.py` fails on any datetime declared bare.
"""

from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field

from app.core.clock import to_local
from app.core.config import settings

#: Shared wording for the two customer-name fields, which appear on more than one list schema
#: (#173, #174). One definition so the two lists cannot drift into describing the same pair
#: differently — see `app/schemas/__init__.py` for when a field earns a description at all (#175).
CUSTOMER_NAME_DESCRIPTION = (
    "The name printed on this document instead of the customer's own, when one was set. "
    'Null on an ordinary sale — most rows. To show who the customer is, read '
    '`customer_display_name`; this field only says whether the document overrides that name.'
)
CUSTOMER_DISPLAY_NAME_DESCRIPTION = (
    "The customer's own name, joined from the customer record. This is the field to render "
    'in a list. Null only if the customer row is missing.'
)


_ZONE = settings.business_timezone.key
LOCAL_DATETIME_DESCRIPTION = (
    f'Local wall-clock time in {_ZONE}, with no UTC offset. A value sent with an offset is '
    f'converted to {_ZONE}; a value without one is taken as already local.'
)

#: A datetime as the whole system means it: naive wall-clock time in the business timezone (#228).
LocalDateTime = Annotated[
    datetime, AfterValidator(to_local), Field(description=LOCAL_DATETIME_DESCRIPTION)
]


class ListResponse[T](BaseModel):
    items: list[T]
    total: int
