"""Every GET of a missing record is `resource.not_found`, naming the resource and id (spec 036).

FR-006 and FR-020: one parametrized case per GET route that takes exactly one integer id, run
against an empty-but-real database, so a route whose 404 is not coded fails here by name.
"""

import pytest
from fastapi.routing import APIRoute

from app.core.errors import EXTRA_RESOURCES, RESOURCE_NAMES
from app.main import app
from tests.problem import assert_problem

MISSING = 999999

#: Routes that answer 404 for something other than the id in their path, with the reason.
SKIP: dict[str, str] = {}


def _routes() -> list[str]:
    paths = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or 'GET' not in route.methods:
            continue
        params = route.dependant.path_params
        if len(params) == 1 and params[0].field_info.annotation is int:
            paths.append(route.path)
    return sorted(paths)


@pytest.mark.asyncio
@pytest.mark.parametrize('path', _routes())
async def test_a_missing_record_is_coded(client, path: str) -> None:  # noqa: ANN001
    if path in SKIP:
        pytest.skip(SKIP[path])
    url = path.replace(path[path.index('{') : path.index('}') + 1], str(MISSING))

    response = await client.get(url)

    body = assert_problem(response, 404, 'resource.not_found')
    assert body['params']['id'] == MISSING
    assert body['params']['resource'] in set(RESOURCE_NAMES.values()) | EXTRA_RESOURCES
