# mbe-api

MBE Web API — FastAPI/Python 3.12+ backend for the business management system, backed by MariaDB 10.11.

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager
- MariaDB 10.11 (TCP or Unix socket)

## Setup

```bash
# Install dependencies
uv sync

# Copy and configure environment
cp .env.example .env   # edit credentials and DATABASE_URL
```

### Environment variables

| Variable | Description | Default |
|---|---|---|
| `DATABASE_URL` | SQLAlchemy async URL (aiomysql) | `mysql+aiomysql://user:password@localhost/mbe` |
| `JWT_SECRET_KEY` | Secret used to sign JWT tokens | `change-me-in-production` |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | Access token TTL in minutes | `480` |
| `JWT_RECOVERY_TOKEN_EXPIRE_HOURS` | Password-recovery token TTL in hours | `24` |
| `DEBUG` | Enable SQLAlchemy query logging | `false` |

**Unix socket example:**
```
DATABASE_URL=mysql+aiomysql://user:password@/dbname?unix_socket=/tmp/mysql.sock
```

## Run

```bash
# Development (auto-reload)
uv run uvicorn app.main:app --reload

# Production
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
```

The API will be available at `http://localhost:8000`.  
Interactive docs: `http://localhost:8000/docs`  
ReDoc: `http://localhost:8000/redoc`

## Database migrations

```bash
# Apply all pending migrations
uv run alembic upgrade head

# Create a new migration (after changing models)
uv run alembic revision --autogenerate -m "describe the change"

# Downgrade one step
uv run alembic downgrade -1
```

## Test

```bash
# Run all tests
uv run pytest

# With coverage
uv run pytest --cov=app

# Run a specific test file
uv run pytest tests/test_users.py -v
```

## Lint & type check

```bash
# Lint (ruff — line length 100, rules E/F/I/UP)
uv run ruff check .

# Auto-fix fixable violations
uv run ruff check --fix .

# Type check (mypy strict)
uv run mypy app

# Format (ruff — single quotes; docstrings keep """)
uv run ruff format app tests

# Verify formatting without writing
uv run ruff format --check app tests
```

Formatting is defined by `[tool.ruff.format]` in `pyproject.toml` and applied by
the editor: `.vscode/settings.json` sets ruff as the Python formatter with
format-on-save plus fix-all and organize-imports, and `.vscode/extensions.json`
recommends the extension. `ruff.importStrategy` is `fromEnvironment`, so the
editor uses the ruff pinned in the dev dependency group rather than the one
bundled with the extension — the editor and `uv run ruff` cannot disagree.

If you work outside VS Code, run `uv run ruff format app tests` before
committing.
