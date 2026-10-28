# Quickstart: validating spec 035

Run in the worktree `mbe-api-035`, against mbe_dev and the dev instance (xolotl, tmux :8765). Deploying the branch to dev needs no confirmation. Any write to mbe_dev does: the race runs, their cleanup, and migration 030.

The race scripts live in the session scratchpad, not the repo. Each one:
- creates its own test customer and draft documents;
- fires its requests concurrently through dev's API with `httpx`, using tokens minted as in spec 034;
- records what came back;
- deletes every row it created in a `finally` block;
- prints what is left, which must be 0.

The race user needs an open cash session at facility 51, because a refund cannot be confirmed without one. A script reuses the user's open session and records which, or opens one and removes it in its cleanup.

## 0. Local gates

```sh
uv run ruff check app/ migrations/ tests/
uv run pytest -q
```

## 1. Before (dev still runs main)

**Read-only:**
- As a user with Products read only, `GET /suppliers/{id}` for a supplier with bank accounts. Record the non-empty `bank_accounts`.
- `EXPLAIN SELECT … FROM lot_serial_tracking WHERE source = ? AND reference = ? ORDER BY lot_serial_tracking_id` for one confirmed receipt. Expect a full index scan of about 670k rows.
- Count `customer_payment` rows with `serial = 0`: 20, all at facility 51. Record facility 51's `MAX(serial)`.

**Races, with confirmation, at facility 51:**
- **SC-003 before:** 20 draft orders for a non-stockable product, confirmed at once. Expect at least one generic 409 "The request conflicts with existing data", and fewer than 20 serials.
- **SC-005 before:** a stockable product with exactly 5 available in one warehouse, and two draft orders for 5, confirmed at once. Expect both to confirm and availability to read −5. In the same run, confirm one draft order twice at once and record the outcome.

## 2. Deploy the branch to dev and check `/health`

## 3. Migration 030 (with confirmation, right after the deploy, research R7)

- Apply `migrations/030_serials_ledger_index.sql`, then run it a second time. The second run changes 0 rows.
- **Expect:**
  - no `serial = 0` payments;
  - the 20 carry facility 51's next serials in id order;
  - every other payment's serial matches a snapshot taken before (compare a checksum over `(customer_payment_id, serial)`);
  - `SHOW INDEX` lists the three new indexes.

## 4. Supplier panels (SC-001, SC-002)

- **Products read only:** the same supplier returns all four panels empty.
- **Administrator:** all four panels filled.
- **Suppliers read without Bank Accounts read** (one of the 3 such users): `bank_accounts` empty, the other panels per their privileges.
- **SC-002** is covered by the API tests. No supplier write is sent to dev.

## 5. Races after (with confirmation)

- **SC-003:** 20 concurrent confirmations at facility 51. All 200, with serials max+1 … max+20. In the same burst, 5 quotes and 2 refunds: each type's serials are consecutive within that type (US2 scenario 2).
- **SC-004:** 20 concurrent `POST /customer-payments` at facility 51. All 201, with 20 distinct, consecutive serials.
- **SC-005:**
  - the last-5-units pair: one 200 and one 409 "Insufficient stock" with its line, and availability reads 0;
  - the double confirm: one 200 and one 409 "Document is completed …", with one serial and one set of reservations.
- **US3 scenario 2:** an order for 5 and an inventory issue for 5 from the same warehouse, confirmed at once: one succeeds and the other is refused for insufficient stock.
- After cleanup, the scripts report 0 rows left. Each run uses up the serials it confirmed, as spec 034's test refund did. Those numbers stay unused.

## 6. Ledger plan after (SC-007)

The same `EXPLAIN` as in step 1. Expect `lot_serial_tracking_source_reference_idx` with `ref` access, and rows in the tens.

## 7. Record

Record the before and after outputs for the PR's Verification section: the race outcomes, the migration counts, the EXPLAIN plans, and the supplier panels by user.
