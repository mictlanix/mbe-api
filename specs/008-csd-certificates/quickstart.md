# Quickstart: Validating Digital Seal Certificates

Run from the repo root. Steps 3 and 4 read real key material out of the database in memory;
they print derived comparisons only, never a password or key byte.

## Prerequisites

- `uv sync`
- `.env` pointing at a populated database (17 certificates in `mbe_demo`)

## 1. Test suite

```bash
uv run pytest -q tests/unit/test_csd_service.py tests/unit/test_taxpayer_certificate_service.py tests/api/test_taxpayer_certificates.py
```

`test_csd_service.py` builds genuine DER certificate/key pairs with `cryptography` and
exercises the real parser — wrong password, foreign key, unencrypted key, unreadable
certificate, and the local-time conversion. Mocking the crypto library would prove nothing
about the conventions being assumed.

## 2. Secret columns are not selected

```bash
uv run python -c "
from sqlalchemy.dialects import mysql
from sqlalchemy import select
from app.services.taxpayer_certificate_service import _METADATA_ONLY
from app.models.fiscal import TaxpayerCertificate
print(select(TaxpayerCertificate).options(_METADATA_ONLY).compile(dialect=mysql.dialect()))
"
```

Expected: the generated SQL names exactly five columns, and none of `certificate_data`,
`key_data` or `key_password` appears (FR-005).

## 3. Reads carry metadata only, against real rows

```bash
PYTHONPATH=. uv run python - <<'PY'
import asyncio
from app.db.session import AsyncSessionLocal, engine
from app.schemas.fiscal import TaxpayerCertificateResponse
from app.services import taxpayer_certificate_service

async def main():
    async with AsyncSessionLocal() as db:
        items, total = await taxpayer_certificate_service.list_taxpayer_certificates(db, limit=50)
        fields = set()
        for i in items:
            fields |= TaxpayerCertificateResponse.model_validate(i).model_dump().keys()
        print(f'  {len(items)}/{total} certificates validated')
        print(f'  fields exposed: {sorted(fields)}')
    await engine.dispose()

asyncio.run(main())
PY
```

Expected: every row validates, and the field set is exactly
`['status', 'taxpayer', 'taxpayer_certificate_id', 'valid_from', 'valid_to']`.

## 4. The parser reproduces the stored columns — the ground-truth check

This is the step that caught the timezone defect. The stored columns were written by the
previous system, so the parser should reproduce them from the binaries alone.

```bash
PYTHONPATH=. uv run python - <<'PY'
import asyncio
from sqlalchemy import select
from app.db.session import AsyncSessionLocal, engine
from app.models.fiscal import TaxpayerCertificate
from app.services.csd_service import _parse_sync

async def main():
    ok = {'number': 0, 'rfc': 0, 'from': 0, 'to': 0, 'parse': 0}
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(TaxpayerCertificate))).scalars().all()
        for r in rows:
            try:
                p = _parse_sync(r.certificate_data, r.key_data, r.key_password)
            except ValueError as e:
                print(f'  parse failed for {r.taxpayer_certificate_id}: {e}'); continue
            ok['parse'] += 1
            ok['number'] += p.certificate_id == r.taxpayer_certificate_id
            ok['rfc'] += p.taxpayer == r.taxpayer
            ok['from'] += p.valid_from == r.valid_from
            ok['to'] += p.valid_to == r.valid_to
        n = len(rows)
        for k, v in ok.items():
            print(f'  {k:<8} {v}/{n}')
    await engine.dispose()

asyncio.run(main())
PY
```

Expected on `mbe_demo` (17 certificates):

```
  parse    17/17     password opens the key and the key matches the certificate
  number   17/17     serial convention correct
  rfc      17/17     subject convention correct
  from     17/17     local-time conversion correct
  to       13/17     4 pre-2022 records follow the older inconsistent convention
```

The four `to` differences are expected and documented in research R6 — they cannot recur.
Anything else failing means a convention assumption is wrong.

## 5. Lint and types

```bash
uv run ruff check app tests && uv run ruff format --check app tests && uv run mypy app
```

## Success criteria mapping

| Criterion | Verified by |
|-----------|-------------|
| SC-001 administrator can see holdings and expiries | Step 3 |
| SC-002 no secret material obtainable | Steps 2, 3 |
| SC-003 unusable certificate rejected at registration | Steps 1, 4 (`parse 17/17`) |
| SC-004 rejection reasons distinguishable | Step 1 |
| SC-005 dates agree with the previous system | Step 4 (`from 17/17`) |
| SC-006 no expiry overstated | Step 4 (`to`: differences understate, never overstate) |
