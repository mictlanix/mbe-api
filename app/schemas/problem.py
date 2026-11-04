"""The error body every v1 route can answer (spec 036, RFC 9457). Declared for OpenAPI only."""

from typing import Any

from pydantic import BaseModel, ConfigDict

from app.core.error_codes import ErrorCode


class ProblemPart(BaseModel):
    """One part of a multi-part failure, with its own code. Other keys are kept as sent."""

    model_config = ConfigDict(extra='allow')

    code: ErrorCode
    params: dict[str, Any] | None = None


class Problem(BaseModel):
    status: int
    code: ErrorCode
    # A dict only for four codes that keep today's dict detail; a later spec narrows it to `str`.
    detail: str | dict[str, Any]
    params: dict[str, Any] | None = None
    errors: list[ProblemPart] | None = None
