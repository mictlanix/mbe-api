"""The catalog's invariants and the guards that keep free text out (spec 036)."""

import ast
import re
from pathlib import Path

import pytest

import app.models  # noqa: F401  (registers every table)
from app.core.error_codes import ErrorCode
from app.core.errors import EXTRA_RESOURCES, RESOURCE_NAMES
from app.db.base import Base

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_DIR = REPO_ROOT / 'app'
ERRORS_MODULE = APP_DIR / 'core' / 'errors.py'

#: Tables whose public name differs: their table name must never be a public name.
RENAMED = {table for table, name in RESOURCE_NAMES.items() if table != name}


def test_every_code_is_domain_dot_condition_with_an_error_status() -> None:
    bad = [
        code.value
        for code in ErrorCode
        if not re.fullmatch(r'[a-z][a-z_]*\.[a-z][a-z_]*', code.value)
        or not 400 <= code.status <= 599
    ]
    assert not bad


def test_every_table_has_a_public_name() -> None:
    missing = sorted(set(Base.metadata.tables) - set(RESOURCE_NAMES))
    assert not missing, f'tables with no public resource name: {missing}'


def test_no_public_name_is_a_renamed_table() -> None:
    public = set(RESOURCE_NAMES.values()) | EXTRA_RESOURCES
    assert not public & RENAMED


def _http_exception_calls() -> list[str]:
    found = []
    for path in sorted(APP_DIR.rglob('*.py')):
        if path == ERRORS_MODULE:
            continue
        for node in ast.walk(ast.parse(path.read_text(), str(path))):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, 'id', None)
                if name == 'HTTPException':
                    found.append(f'{path.relative_to(REPO_ROOT)}:{node.lineno}')
    return found


@pytest.mark.xfail(strict=True, reason='spec 036 phase 5 (T082): raises still being migrated')
def test_no_http_exception_is_constructed_outside_the_error_module() -> None:
    found = _http_exception_calls()
    assert not found, 'raise DomainError or not_found() instead:\n' + '\n'.join(found)


def _test_sources() -> str:
    here = Path(__file__).resolve()
    return '\n'.join(
        p.read_text() for p in (REPO_ROOT / 'tests').rglob('*.py') if p.resolve() != here
    )


@pytest.mark.xfail(strict=True, reason='spec 036 phase 5 (T083): codes still being tested')
def test_every_code_is_asserted_by_some_test() -> None:
    sources = _test_sources()
    untested = [
        code.value
        for code in ErrorCode
        if f"'{code.value}'" not in sources
        and f'"{code.value}"' not in sources
        and f'ErrorCode.{code.name}' not in sources
    ]
    assert not untested, f'{len(untested)} codes no test asserts: {untested}'
