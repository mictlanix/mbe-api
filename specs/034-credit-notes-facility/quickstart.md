# Quickstart: validating spec 034

Run in the worktree `mbe-api-034`, against mbe_dev and the dev instance (xolotl, tmux :8765). Deploying the branch to dev needs no confirmation. Writing to mbe_dev does.

## 0. Local gates

```sh
uv run ruff check app/ migrations/ tests/
uv run pytest -q
```

## 1. Before (read-only, before deploying)

- Through `GET /credit-notes` as an administrator, record:
  - the first page of `open_only=true&limit=100`: 0 items, total 984 (#278);
  - `GET /credit-notes/990`: `remaining` 0 (#277).
- As clerk `carmengodinez` (facility 51; reads 7, 8 and 83; holds neither 100 nor 101), record what these reads return today for facility 8:
  - `GET /sales-orders?facility=8`;
  - `GET /customer-payments/unverified?facility=8`;
  - `GET /credit-notes/990`.

  No write is sent: the writes' code paths are read in the diff, and the tests cover them.

## 2. Deploy the branch to dev and check `/health`

## 3. Credit note balances (SC-001)

For all 984 notes: `amount` equals the backing payment's amount. `remaining` equals the payment's unapplied amount, floored at 0, and is 0 for notes 158 and 462. **Expect 0 mismatches.** Note 990 reads 27.00 and 3.00.

## 4. Open-only paging (SC-002)

As an administrator naming each facility in turn (8, 47, 50, 51, 52):
- page `open_only=true` at `limit=7`, then at `limit=100`;
- each note comes back exactly once;
- `total` equals the page sum;
- the five totals add up to the spendable count, 137 on 2026-10-27, give or take the day's activity;
- no walk-in customer note and neither 158 nor 462 appears.

## 5. The facility rule (SC-004, SC-005)

**As `carmengodinez`:**
- `GET /credit-notes/990` → 404.
- `GET /credit-notes?facility=8` → 403.
- `GET /credit-notes` lists only facility 51.
- `POST /customer-payments/{a facility-8 payment}/applications` → 404.
- `POST …/reverse` on a facility-8 application → 404. Neither changes anything.
- `GET /sales-orders?facility=8` → 403.
- `PUT /sales-orders/{a facility-8 order}` with an empty body → 404.
- The same order's `GET`, `/ticket` → 200.

**As an administrator:** the same calls proceed. Only the reads are run, so no data changes.

**As a PaymentsVerification reader without PaymentsEditor** (the one such user): `GET /customer-payments/unverified` covers every facility. Its total equals SQL's count of unverified payments, and `facility=8` narrows it to 8.

## 6. A refund's credit note (SC-003)

This writes to mbe_dev, so it needs confirmation.
- Open a session, refund a paid test order with `payout: credit_note`, and read the note.
- **Expect:** `amount` = the refund total, `remaining` = the same, `refunded` 0, `cash_session` NULL in SQL, listed as open.
- Remove the test rows by SQL afterwards.
