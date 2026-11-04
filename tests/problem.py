"""`assert_problem`: the shared assertion for coded errors (spec 036, FR-020)."""

from typing import Any

from httpx import Response


def assert_problem(response: Response, status: int, code: str) -> dict[str, Any]:
    """The response is a Problem with this status and code. Returns the body for params checks."""
    assert response.status_code == status, (response.status_code, response.text)
    assert response.headers['content-type'].startswith('application/problem+json')
    body = response.json()
    assert body['status'] == status
    assert body['code'] == code, body
    return body
