# Quickstart: Expense Vouchers and Supplier Payments

## Local checks

```bash
uv run ruff check app tests migrations && uv run ruff format --check <changed files>
uv run pytest -q
```

What the tests cover (contracts/payments.md, research):
- **Vouchers:**
  - create, edit, confirm, cancel and void, with every refusal;
  - the creator rule and the facility rule;
  - the session moving on confirm;
  - the ticket.
- **Supplier payments:**
  - create with and without applications;
  - apply, reverse and cancel, with every refusal;
  - the cutoff (NULL, before, after);
  - legacy payments' zero credit;
  - the PO balance and the supplier summary;
  - serials per facility.
- **Cash cut:**
  - vouchers and cash supplier payments are subtracted;
  - voided and cancelled ones are not;
  - non-cash payments never touch a session.
- **Every route** answers 401, 403, 404, 409 and 422 as the contract says, and no route answers 500.

SQLite cannot show row-lock races, so SC-002, SC-003 and SC-004 are run on the deployment.

## Deployment (xolotl, mbe_dev)

Each step needs the user's confirmation first.

1. **Migration 029.** Apply it, then check:
   - the new `supplier_payment` columns, including the legacy backfill and `UNIQUE (facility, serial)`;
   - `purchase_order_payment`;
   - `supplier_balance_cutoff`, with its one row and a NULL cutoff.
2. **Deploy** the branch.
3. **SC-001 (read only):** for 50 closed sessions, compare the cut's expected cash with an independent SQL sum:
   - opening plus cash sales net of change;
   - minus confirmed, non-cancelled vouchers;
   - minus cash supplier payments;
   - minus cash refunds.

   It must match to the cent.
4. **US1, US2 and SC-002:** in a test session:
   - create and confirm a voucher, and void another;
   - then run 20 voids or confirms at the same moment as the session's close.

   Afterwards, the closed cut equals what the closing cut reported, and every operation either landed before the close or answered 409.
5. **US4 to US6, SC-003 and SC-004:** set a test cutoff through a scratch migration on mbe_dev only, agreed with the user, then:
   - record 20 payments at the same moment in one facility: the serials are distinct and consecutive;
   - make 20 applications at the same moment against one payment and one PO: neither goes below 0;
   - record a cash payment and check the cut subtracts it;
   - reverse an application and cancel a payment, and check the balances;
   - in a test session, run 20 cash payment creates and cancels at the same moment as the session's close: the closed cut equals what the close reported, and each operation either landed before the close or answered 409.
   - **SC-006 and the plan's goals:** time voucher create, add a line and confirm (each under 1 s), and `GET /supplier-payments/suppliers/{id}/balance` for the supplier with the most counting POs (under 2 s). Record the times.
6. **Cleanup**, agreed with the user before running:
   - cancel the test payments and void the test vouchers;
   - reset the cutoff to NULL.
