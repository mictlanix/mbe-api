# Quickstart: Unified Entity Status validation

Prerequisites: repo checked out on `005-unified-entity-status`, `uv sync` done.

## 1. Static gates

```bash
uv run ruff check app/ migrations/ tests/          # zero violations
grep -rnE '\b(disabled|deactivated|enabled)\b' app/ --include='*.py' | grep -v enums.py
# → no hits; lifecycle 'active' also gone (domain booleans like completed/cancelled remain)
```

## 2. Test suite

```bash
uv run pytest       # all green
```

Must include (see [contracts/entity-status.md](contracts/entity-status.md)):
- every status-bearing endpooint's tests asserting `status` in responses and absence of legacy fields
- `?status=0` / `?status=1` filter tests per list endpoint
- login rejection for users with status INACTIVE (1) and ARCHIVED (2)
- 422 tests for out-of-range status on create/update/filter

## 3. OpenAPI contract check

```bash
uv run python -c "
from fastapi.openapi.utils import get_openapi
from app.main import app
import json
spec = get_openapi(title='t', version='1', routes=app.routes)
s = json.dumps(spec)
for legacy in ('\"disabled\"', '\"deactivated\"', '\"enabled\"', '\"active\"'):
    assert legacy not in s, legacy
assert '\"status\"' in s
print('OpenAPI OK')
"
```

## 4. Migration (SQL script, requires a MariaDB with current schema)

```bash
mysql <db> < migrations/sql/005_unified_entity_status.sql           # migrate
mysql <db> < migrations/sql/005_unified_entity_status_rollback.sql  # roll back if needed
```

Executed against the real `mbe_demo` DB on 2026-07-19: 53,582 rows / 13 tables, zero mapping
mismatches vs. a pre-migration snapshot. Spot-check backfill (expected: rows previously
disabled/deactivated/not-active → status=1, everything else → status=0):

```sql
SELECT status, COUNT(*) FROM customer GROUP BY status;
SELECT status, COUNT(*) FROM employee GROUP BY status;
```

## 5. Manual end-to-end (optional)

```bash
uv run uvicorn app.main:app --reload
# login as an ACTIVE user → 200; set a test user status=1 → login now rejected
# GET /api/v1/customers?status=0 → only ACTIVE customers, total matches
# PUT /api/v1/vehicles/{id} {"status": 2} → response shows status 2; DELETE still hard-deletes
```
