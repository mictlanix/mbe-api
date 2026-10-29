"""`/openapi.json` publishes the error contract (spec 036, FR-002, FR-004)."""

from app.core.error_codes import ErrorCode
from app.main import app

HTTP_METHODS = {'get', 'post', 'put', 'patch', 'delete'}


def test_problem_and_the_code_catalog_are_published() -> None:
    schemas = app.openapi()['components']['schemas']

    assert set(schemas['Problem']['properties']) == {'status', 'code', 'detail', 'params', 'errors'}
    assert schemas['ErrorCode']['enum'] == [code.value for code in ErrorCode]
    assert len(schemas['ErrorCode']['enum']) == 241


def test_every_v1_operation_defaults_to_a_problem() -> None:
    missing = []
    for path, operations in app.openapi()['paths'].items():
        if not path.startswith('/api/v1'):
            continue
        for method, operation in operations.items():
            if method not in HTTP_METHODS:
                continue
            content = operation['responses'].get('default', {}).get('content', {})
            schema = content.get('application/problem+json', {}).get('schema', {})
            if schema.get('$ref') != '#/components/schemas/Problem':
                missing.append(f'{method.upper()} {path}')
    assert not missing
