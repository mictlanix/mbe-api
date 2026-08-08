"""No endpoint may answer 500, whatever it is sent — one case per route, real database behind it.

This is the blast-radius net. It does not assert that any endpoint is *correct*; it asserts that
every one of them can be reached without the server falling over, which is the property both #149
and #154 broke and no existing test held.

**Every answer other than 500 passes, deliberately.** A `404` for an id that was not seeded, a `422`
for an empty body, a `409` for a state that does not permit the transition — all of those are the
endpoint working. Insisting on `200` here would mean seeding a valid precedent state for 212 routes,
which is what the per-flow tests in this directory do for the paths that matter. Mixing the two
would make this net either narrow or permanently amber.

`500` and `501` are the failures. A 500 is an unhandled exception: a wrong column name, a shadowed
variable, a `None` where the code assumed a row. That is the class of bug this exists to catch, and
it is caught for every route at once, including routes nobody has written a test for.
"""

import pytest
from fastapi.routing import APIRoute
from httpx import AsyncClient

from app.main import app

#: Routes whose body is a file upload. Sent without one they answer 422 from the multipart parser,
#: which is a real answer, so they are exercised rather than skipped.
ROUTES = sorted(
    (
        (method, route.path)
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods
        if method != 'HEAD'
    ),
    key=lambda pair: (pair[1], pair[0]),
)


def _url(path: str) -> str:
    """`/api/v1/products/{product_id}` -> `/api/v1/products/1`.

    `1` rather than a nonexistent id on purpose: the seeded fixtures use low ids, so a route that
    can reach real data does, and one that cannot answers 404 — which still exercises its lookup.
    """
    parts = [
        '1' if segment.startswith('{') and segment.endswith('}') else segment
        for segment in path.split('/')
    ]
    return '/'.join(parts)


def test_every_route_was_collected() -> None:
    """The failure mode of a generated suite: collect nothing, assert nothing, report success."""
    assert len(ROUTES) > 200, f'only {len(ROUTES)} routes collected from the application'


@pytest.mark.parametrize(('method', 'path'), ROUTES, ids=lambda v: v if isinstance(v, str) else v)
async def test_the_endpoint_does_not_fail_with_500(
    method: str, path: str, client: AsyncClient, seeded: None
) -> None:
    url = _url(path)
    # An empty object for writes: enough to reach validation, and for the endpoints whose fields are
    # all optional, enough to reach the service. Query-parameter validation answers 422 by itself.
    body = {} if method in {'POST', 'PUT', 'PATCH'} else None

    response = await client.request(method, url, json=body)

    assert response.status_code not in (500, 501), (
        f'{method} {path} answered {response.status_code}: '
        f'{response.text[:400]}'
    )
