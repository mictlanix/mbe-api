# Quickstart: Physical Inventory Count, Stock and Kardex

## Local checks

```bash
export BUSINESS_TIMEZONE=America/Mexico_City   # the worktree has no .env
uv run ruff check app/ migrations/ tests/
uv run pytest tests/unit/test_inventory_count.py tests/unit/test_kardex.py \
  tests/integration/test_inventory_count_flow.py tests/integration/test_stock_queries.py \
  tests/integration/test_kardex_flow.py tests/api/test_inventory_counts.py \
  tests/api/test_stock.py tests/api/test_kardex.py
uv run pytest -q
```

The local tests cover:
- **SC-001:** counted products end at the counted quantity, and entries sum to the line differences.
- **SC-004:** the kardex balances.
- **SC-006:** rights and 404s.
- **Pickups:** a counter pickup posts type 13.

## Test deployment (xolotl, mbe_dev)

Each step needs the user's confirmation first.

1. **Migration 027.** Apply it, then check that both tables and their unique keys exist.
2. **Deploy** the branch to the test instance (`mbe-api-test`).
3. **SC-005, stock:** for 3 warehouses and about 20 products each, including products with several labels, compare `/stock` with direct ledger and reservation sums. Read only.
4. **SC-004, kardex:** for 5 products, compare the closing balance with the ledger sum up to the range end. Read only.
5. **SC-001 and SC-002, a small count** on a regular warehouse:
   - count 3 products, one equal to on hand and two different;
   - confirm twice at once: one 200, one 409, one set of type-12 entries;
   - check the balances, then record the ids.

   A count cannot be cancelled. To restore the balances, either count again at the original quantities (a second count) or leave the change recorded, whichever the user chooses.
6. **SC-003, the opening count at scale,** on a warehouse the user designates: enter 15,000 lines in batches of 2,000, then confirm, and time it. The balances it changes have to be restored afterwards with a second count, so the user must agree to the choice of warehouse and the restoration first.

## Follow-ups

- Spec 029's follow-ups stand (`assign_folio`, the sales-order availability lock, the `(source, reference)` index).
