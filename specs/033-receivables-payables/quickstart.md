# Quickstart: validating spec 033

The steps run in the worktree `mbe-api-033`, against mbe_dev and the dev instance (xolotl, tmux :8765, deployed as `mictlanix@`). Every write to mbe_dev, every deployment and every real email needs confirmation first.

## 0. Local gates

```sh
uv run ruff check . && uv run ruff format --check .
uv run pytest -q
```

## 1. Before: measure on mbe_dev (read-only), before deploying

Measure the customer side with today's arithmetic: credit orders that are completed, not cancelled and unpaid by flag, with a balance above 0.01. Record:
- owed, number of orders and number of customers (expected around $6.86M, 606 orders, 136 customers);
- customers on hold by flag (199) and by balance (130);
- unpaid orders with a balance of 0.01 or less (257).

Also read the current AR screen's figures with its own SQL (`AccountsReceivablesController.cs:80-121` on `origin/casa-maestra-2026`), and list the customers whose figure differs from the API's (3 expected, about $6.4k).

Keep the output; step 6 compares against it.

## 2. Deploy the branch to dev

This needs confirmation. Pull the branch on xolotl, restart the tmux :8765 instance, and check `/health`.

## 3. One balance (SC-001)

For every customer with an open order, compare:
- `GET /accounts-receivable?party={id}` (with an all-facilities caller): `owed`;
- the credit check's debt, through a probe that calls `_customer_credit_debt`, in a read-only session;
- the sum of the `balance` values from `GET /customer-payments/outstanding-orders?customer={id}`, called as a user of each facility that has orders, and summed.

**Expected:** 0 mismatches.

## 4. The AR view (US2, SC-004, SC-008)

- As an all-facilities caller (admin), and as a user with 18 and without 101:
  - the scoped user sees only orders of their facility;
  - `owed_company`, `utilization` and `available_credit` are the same for both callers;
  - `facility=` from the scoped user, for another facility, is refused (403).
- For every row, the buckets add up to `owed`, with a difference of 0.
- **Timings:** the summary of all customers in under 2 s; a drill-down in under 1 s.
- **Holds:** confirming an order for a customer whose only overdue orders have a zero balance succeeds. Use an existing stale order from the 257, before step 7 runs.

## 5. Applications (US4, SC-005)

These write to mbe_dev, so they need confirmation. Use a test customer and orders created for the purpose.
- Apply 600 and 400 from one 1,000 payment to two orders owing 600 and 700. **Expected:** the first is paid, the second owes 300, and the payment has 0 left.
- **Expected refusals:**
  - another customer's payment;
  - 350 against an order owing 300;
  - any amount against a paid order.
- **Concurrency:** 20 simultaneous applications of 10 against one order owing 100. **Expected:** exactly 10 succeed, the rest get 409 or 422, and the balance never goes below 0.
- **Reversal:** reverse one application on the paid order. **Expected:** `paid` is cleared, because the balance crossed back above 0.01.
- **Legacy order:** an application to a legacy paid order with a computed balance is refused (409), and the order stays paid.

## 6. After: re-measure (SC-002)

Rerun step 1's measurement through the API's `order_balances`.

**Expected:**
- owed equals step 1's figure to the cent, since credit notes touch no unpaid order;
- holds are 130;
- the current AR screen differs only for the same 3 customers.

## 7. The stale-flag job (US6, SC-007)

It writes to mbe_dev, so it needs confirmation.

```sh
uv run python -m app.jobs.settle_paid_flags            # dry run: 257 rows; zero times only for 2026 orders
uv run python -m app.jobs.settle_paid_flags --apply
uv run python -m app.jobs.settle_paid_flags            # selects 0
```

**Expected after the job:**
- 0 open orders flagged unpaid with a balance of 0.01 or less;
- 0 orders dated before 2026 with a new zero time;
- 257 incidences with source 3.

## 8. The AP view with a test cutoff (US3, SC-003)

It writes to mbe_dev, so it needs confirmation.

1. Set `supplier_balance_cutoff.cutoff` to a test date, for example `2026-01-01`.
2. For every supplier the view lists, `owed`, `credit` and `net` must equal `GET /supplier-payments/suppliers/{id}/balance` to the cent.
3. Spot-check due dates: first confirmed receipt plus credit days.
4. Check that a PO without a receipt is in `current`, with no due date.
5. Check that a scoped user with 19 sees every supplier.
6. **Reset:** `UPDATE supplier_balance_cutoff SET cutoff = NULL WHERE supplier_balance_cutoff_id = 1;`. With that, the view is empty again.

## 9. Statements (US5, SC-006)

- Print `GET /accounts-receivable/{id}/statement` for 20 customers (include one with orders in two facilities, printed by a scoped user), and `GET /accounts-payable/{id}/statement` for 5 suppliers, under the test cutoff.
- Each statement's totals must equal the view's all-facilities figures.
- **Email:** it needs confirmation, and goes only to an internal test address. Send one customer statement and one supplier statement. **Expected:** one incidence each (sources 7 and 16), and the PDF arrives.

## 10. Cleanup

This needs confirmation.
- Delete the test applications, payments and orders from step 5 by SQL. Their ids are recorded by the script.
- Make sure the cutoff is NULL.
- Leave step 7's corrections in place: they're the intended data fix.
