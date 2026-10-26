# Quickstart: Warehouse Receipts, Issues and Transfers

## Local checks

```bash
uv run ruff check . && uv run ruff format --check .
uv run pytest tests/unit/test_inventory_documents.py tests/unit/test_inventory_transfer_state.py
uv run pytest tests/integration/test_inventory_receipt_flow.py \
  tests/integration/test_inventory_issue_flow.py tests/integration/test_inventory_transfer_flow.py
uv run pytest tests/api/test_inventory_receipts.py tests/api/test_inventory_issues.py \
  tests/api/test_inventory_transfers.py
uv run pytest            # whole suite
```

The integration tests cover:
- **SC-001:** each confirmed document's ledger entries match its lines, and a received transfer nets to zero.
- **SC-004:** cancelling restores the balances, and is refused when stock was already used.
- **SC-005:** a transfer in transit is listed at both ends and appears in `/in-transit`.
- **SC-006:** every route answers 401, 403 and 404.

## Test deployment (xolotl, mbe_dev)

Each step needs the user's confirmation before it runs.

1. **Migration 026.**
   - Apply `migrations/026_inventory_transfer_transit.sql`.
   - Then confirm `DESCRIBE inventory_transfer` shows `in_transit` and `DESCRIBE inventory_transfer_detail` shows `quantity_received`.
   - Confirm that the legacy transfers, read through `GET /inventory-transfers/{id}`, show `received` with `quantity_outstanding` 0.
2. **Deploy** the branch to the test instance (`mbe-api-test`, :8766).
3. **SC-007, a full cycle** on test data, recording the ids:
   - a receipt: confirm, then cancel;
   - an issue: confirm, then cancel;
   - a transfer between two facilities: dispatch, receive part of it, close the remainder, then cancel.
   - After each step, compare `SUM(quantity)` in `lot_serial_tracking` per (warehouse, product) against the values before.
4. **SC-002 and SC-003, concurrency:**
   - Fire 20 confirms of prepared drafts at once in one facility. The serials must be distinct and consecutive.
   - Fire two issues at once that together exceed the available stock. Exactly one must succeed.
   - Confirm one draft from two requests at once: it posts once, and the other request gets 409.
   - Fire two partial receives of 8 on a line of 10 at once. At most 10 may be received.
   - Race a receive against a cancel on one transfer. Either way, the ledger nets to zero per (warehouse, product).
   - Fire two complementary requests for one purchase order at once. There must be one draft.
   - Confirm issues of the same product in two facilities at once. There must be no deadlock and no 500.
5. **Clean-up:** the test documents stay in mbe_dev as cancelled, as with spec 028's refund check. Delete them only if the user asks.

## Expected results

- Serials: no duplicates and no gaps; a refused confirm leaves no gap.
- Ledger: no entry with a lot, serial or expiry. Every cancelled document nets to zero per (warehouse, product).
- Legacy transfers read as `received`; legacy receipts and issues read with their stored state.
