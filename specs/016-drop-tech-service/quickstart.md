# Quickstart: verifying the retirement

One runnable command per success criterion. Every one was run against the finished branch; the
recorded output is what it produced.

## SC-001 — the live-schema derivation excludes the seven

```bash
uv run python -c "
import importlib.util
s = importlib.util.spec_from_file_location('t', 'tests/unit/test_data_dictionary.py')
m = importlib.util.module_from_spec(s); s.loader.exec_module(m)
seven = ['tech_service_receipt', 'tech_service_receipt_component', 'tech_service_report',
         'tech_service_request', 'tech_service_request_component',
         'vehicle_service_order', 'service_order_detail']
print([t for t in seven if t in m.LIVE] or 'none')"
```

→ `none`

## SC-002 — no mapped model references a retired table

```bash
uv run python -c "
import importlib, pkgutil, app.models
for _, n, _ in pkgutil.iter_modules(app.models.__path__): importlib.import_module(f'app.models.{n}')
from app.db.base import Base
seven = {'tech_service_receipt','tech_service_receipt_component','tech_service_report',
         'tech_service_request','tech_service_request_component',
         'vehicle_service_order','service_order_detail'}
print(sorted(seven & set(Base.metadata.tables)) or 'none')"
```

→ `none`

## SC-003 — no dictionary section and no pending-removal note

```bash
grep -c '^### `tech_service\|^### `vehicle_service_order`\|^### `service_order_detail`' docs/data-dictionary.md
grep -c 'marked for a future drop' docs/data-dictionary.md
```

→ `0` and `0`. Section 11 held exactly these seven and nothing else, so the section is gone and
12–15 renumbered to 11–14.

## SC-004 — no waiver names a retired table

```bash
uv run python -c "
import importlib.util
s = importlib.util.spec_from_file_location('t', 'tests/unit/test_data_dictionary.py')
m = importlib.util.module_from_spec(s); s.loader.exec_module(m)
print('column waivers:', len(m.UNDOCUMENTED_COLUMNS))"
```

→ `column waivers: 0`. #179 opened with 25 undocumented columns; the list is now empty.

## SC-005 / SC-006 — the matrix width, and what did not move

```bash
uv run python -c "
from app.enums import SystemObject
v = {int(x) for x in SystemObject}
print('members:', len(v), '| retired present:', sorted({58,64,65,90} & v) or 'none',
      '| PRODUCTION_SITES:', int(SystemObject.PRODUCTION_SITES))"
```

→ `members: 103 | retired present: none | PRODUCTION_SITES: 107`

The last field is the R4 guard: the width moved, the identifier did not.

## SC-007 — the suite

```bash
uv run pytest -q -W error::DeprecationWarning
```

→ `2244 passed, 15 skipped`. Baseline before the feature was 2243 (2285 before spec 016's branch
point, less the tests whose subject no longer exists). One test was added — the retired-objects
assertion in `test_system_objects.py` — and one rewritten rather than deleted; see the note below.

## SC-008 — no migration added

```bash
git diff --name-only main...HEAD -- migrations/ | wc -l
```

→ `0`

## SC-009 — every remaining mention is historical record

```bash
grep -rln 'tech_service\|vehicle_service_order\|service_order_detail\|TechnicalService' \
  --include='*.py' --include='*.md' --include='*.sql' . | grep -v '^./.git/'
```

→ `CHANGELOG.md`, this spec's own files, `specs/010-product-merge-integrity/*` (the merge feature's
record of what it covered), and `tests/unit/test_product_service.py`.

That last one is **not** a stale reference and is worth stating: `service_order_detail` was the only
table in the schema pointing at a product through a differently-named column (`spare_part`), which
made it the sole witness for "the merge writes each relation's own column name, not the literal
`product`". The table is gone; the property and its code path are not. The test now synthesizes the
reference through a patch rather than losing the guard with its example — and deliberately not by
adding a table to `Base.metadata`, which is global and would appear in every integration database.

## Not a success criterion — the finding

```bash
uv run pytest -q tests/unit/test_model_schema.py
```

Run mid-feature with the dump corrected and the models still present, this returned
**`375 passed, 43 skipped`** — green. `test_model_schema.py` *skips* a mapped table absent from both
the dump and the migrations rather than failing on it, so a model pointing at a dropped table is
invisible to the check that exists to catch schema drift. Recorded as research R5 and raised
separately; not fixed here.
