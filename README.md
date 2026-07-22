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

Migrations are hand-written SQL files in `migrations/`, applied in order and recorded in a
`schema_migrations` table inside the target database (the one named by `DATABASE_URL`).

```bash
# Apply everything this database has not yet received
uv run python -m app.db.migrate

# Report which migrations this database has and has not received
uv run python -m app.db.migrate status

# Record migrations as applied WITHOUT executing them
# (only for changes applied by hand before this tooling existed)
uv run python -m app.db.migrate mark 004_facility_rename
```

### Writing a migration

Create `migrations/NNN_short_description.sql`. That's the whole registration step — the
runner discovers files from the directory, so there is no index or manifest to update.

- `NNN` is the ordering prefix, parsed as a number (`010` runs after `009`). Use the next
  free number; two migrations may not share a prefix.
- Optionally add `migrations/NNN_short_description_rollback.sql` to reverse it. Rollback
  files are never applied automatically — run one by hand when you need it:
  `mysql <database> < migrations/005_unified_entity_status_rollback.sql`
- Statements are split on `;`. `--` comments and semicolons inside quoted strings are
  handled; **`DELIMITER` blocks (stored procedures, triggers) are not supported.**

MariaDB does not roll back DDL. If a migration fails partway, the statements before the
failure remain applied and the migration is *not* recorded — the runner prints exactly
which statement failed so you can finish or reverse it by hand.

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
