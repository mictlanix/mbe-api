"""Fields whose name does not carry their meaning must say so in the OpenAPI schema (#175).

`#:` comments are the house style for explaining a field to the next person reading the source, and
they do **not** reach the generated schema. That is the right trade for almost every field. It is
the wrong trade for a field a client can plausibly reach for and be wrong about with nothing
failing.

`customer_name` is the case the convention was written for. It is the per-document *override*, and
`customer_display_name` beside it is the customer's own name. Both are nullable strings, the
more natural-sounding name is the wrong one, and it is null on essentially every row: measured
against the deployment, 1,840 of 1,840 outstanding orders, and 32,478 of the 32,488 sales matching
the walk-in customer. So a client dev picking the obvious field gets `null` every time, silently —
which is #172 relocated from the server to the generated client.

This asserts the descriptions survive into `app.openapi()`, because that output is the only thing a
generated client sees. A rename, a new lookalike field, or someone tidying `Field(...)` back to a
bare annotation drops the distinction without any other test noticing.
"""

import pytest

from app.main import app

SCHEMAS = app.openapi()['components']['schemas']

#: (component, field) pairs that must carry a description, and a phrase each one has to contain.
#: The phrase is checked rather than the exact string so wording can be improved without editing
#: this file, while the load-bearing distinction cannot quietly go missing.
DESCRIBED = [
    ('SalesOrderSummary', 'customer_name', 'instead of'),
    ('SalesOrderSummary', 'customer_display_name', "customer's own name"),
    ('OutstandingOrderResponse', 'customer_name', 'instead of'),
    ('OutstandingOrderResponse', 'customer_display_name', "customer's own name"),
    ('SalesOrderCreate', 'fulfillment_intent', 'never recorded'),
    ('SalesOrderUpdate', 'fulfillment_intent', 'never recorded'),
    ('SalesOrderResponse', 'fulfillment_intent', 'never recorded'),
]


@pytest.mark.parametrize(('component', 'field', 'phrase'), DESCRIBED)
def test_the_field_carries_a_description_into_the_schema(
    component: str, field: str, phrase: str
) -> None:
    properties = SCHEMAS[component]['properties']
    assert field in properties, f'{component}.{field} is gone — renamed, or removed'

    description = properties[field].get('description', '')
    assert description, (
        f'{component}.{field} lost its description. A `#:` comment does not reach the generated '
        f'client; see app/schemas/__init__.py for when a field earns a real one.'
    )
    assert phrase in description, f'{component}.{field}: expected {phrase!r} in {description!r}'


def test_the_two_customer_fields_do_not_share_one_description() -> None:
    """Describing both identically would satisfy the check above and help nobody."""
    properties = SCHEMAS['SalesOrderSummary']['properties']

    assert (
        properties['customer_name']['description']
        != properties['customer_display_name']['description']
    )


def test_both_lists_describe_the_pair_the_same_way() -> None:
    """A sales row and an outstanding row carry the same two fields, so a client that learns the
    distinction on one list must not meet different wording on the other."""
    for field in ('customer_name', 'customer_display_name'):
        assert (
            SCHEMAS['SalesOrderSummary']['properties'][field]['description']
            == SCHEMAS['OutstandingOrderResponse']['properties'][field]['description']
        )


def test_the_override_description_points_at_the_field_to_use_instead() -> None:
    """The whole point: a dev who lands on the wrong field is told where the right one is."""
    description = SCHEMAS['SalesOrderSummary']['properties']['customer_name']['description']

    assert 'customer_display_name' in description
