# Quickstart: Purchase Requests, Purchase Orders and Average Cost

## Local checks

```bash
uv run ruff check app/ migrations/ tests/
uv run pytest tests/unit/test_purchase_requests.py tests/unit/test_purchase_orders.py \
  tests/unit/test_purchase_cost.py tests/integration/test_purchasing_flow.py \
  tests/api/test_purchase_requests.py tests/api/test_purchase_orders.py \
  tests/api/test_inventory_receipts.py
uv run pytest -q
```

The local tests cover:
- **US1:** state transitions, the serial on submit and on resubmit, the minimum-quantity warning, reject incidences, and the facility rule.
- **US2:** pending lines, grouping by supplier and facility, `skipped`, and release on PO cancel or line removal.
- **US3:** draft-only edits, receipts per warehouse (stockable lines only), and a refused second confirm.
- **US4:** the formula, with Q < 0, U = 0, two lines of one product, no PO, a missing cost row, 4-decimal rounding, and restore or warn on cancel.
- **US5:** the PDF context totals, the email incidence written only after a send, and 502 and 503.
- **SC-007:** 401, 403 and 404 on every route.

SQLite can't show row-lock races. SC-001, SC-002, SC-003 and US4 scenario 4 are checked on the test deployment.

## Test deployment (xolotl, mbe_dev)

Each step needs the user's confirmation first.

1. **Migration 028.** Apply it, then check:
   - the two `purchase_request` columns;
   - the `purchase_order_detail` index and FK;
   - `product_cost_change`.
2. **Deploy** the branch to the test instance (`mbe-api-test`).
3. **US1 and SC-002:**
   - create 20 drafts in one facility and submit them concurrently: the serials are distinct and consecutive from the facility's maximum;
   - approve one and reject one;
   - check the approver and time, and the source-4 incidence.
4. **SC-001:** with one approved request whose lines are marked, run 20 concurrent `POST /purchase-orders/to-purchase` and `POST /{id}/request-lines` calls. Each request line ends up on exactly one live PO line, and the others report it under `skipped` or answer 409.
5. **SC-003:** confirm a 2-warehouse PO twice at the same moment: one 200 and one 409, and exactly one receipt per warehouse.
6. **SC-004 and US4:**
   - confirm a generated receipt for a product with known Q and C, and compare the new cost with the formula;
   - confirm two receipts of the same product in two facilities at the same moment: both changes are recorded, the second uses the first one's result, and the final cost matches.
   - Then cancel the latest receipt: cost is restored. Cancelling an earlier one warns instead.
7. **SC-005:** a 212-line PO on a test warehouse:
   - confirm it, then confirm its receipt, and time each against the 5 s limit;
   - then cancel the receipt, restoring the stock and the cost.
8. **SC-006:** download the PDF of a confirmed PO and email it to an address the user names. Check the attachment and the source-5 incidence.

The cleanup of what steps 3 to 7 create (cancelling the requests, POs and receipts) is agreed with the user before running it.

## Follow-ups

- Spec 029's follow-ups stand: `assign_folio`, the sales-order availability lock, and the `(source, reference)` index.
