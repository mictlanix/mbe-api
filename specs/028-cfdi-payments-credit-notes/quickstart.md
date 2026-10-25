# Quickstart: CFDI Payment Receipts, Credit Notes and Email

## 1. Local checks

```bash
uv run ruff check app/ migrations/ tests/
uv run pytest -q
```

Expected new coverage:
- **Unit:** `tests/unit/test_cfdi_payment.py`:
  - DR tax shares for 16 %, 0 % and exempt invoices;
  - the single-16 % case reduces to legacy's values;
  - `Totales` per rate is always written;
  - the rebuilt `tests/fixtures/cfdi/payment.xml` has the same original string;
  - sealed receipts validate against `cfdv40.xsd` and `Pagos20.xsd`.
- **Unit:** credit-note XML (E, relation 01, PUE), and the balance and installment formulas.
- **Integration:**
  - receipt flow: two installments, a concurrent-safe recompute at confirm, created from a customer payment, one live receipt per payment;
  - credit-note flow: copied lines and caps;
  - the refund rule;
  - email with a fake mailer.
- **API:** the routes in `contracts/fiscal-receipts-credit-notes.md`: success, 401, 403, 404, 409 and 422.

## 2. Migration (confirm first; DDL on mbe_dev)

```bash
uv run python -m app.db.migrate status      # from /var/www/mbe-api-test when the tunnel is down
uv run python -m app.db.migrate             # applies 025 after approval
```

## 3. SC-002: parity on mbe_dev (read only)

Export the 21 live receipts and 3 credit notes of the last 12 months with their rows and related invoices' XML, as the spec 027 parity script did. Rebuild each locally.

Expect:
- identical original strings, apart from the listed FR-007 differences;
- the computed installment and previous balance equal to the stored ones where consistent.

## 4. Deploy and SC-004 (confirm first)

1. Apply migration 025 first (§2), then deploy the branch to the test instance (spec 027's procedure). The deployed model reads the new column.
2. On a real order whose lines are on a stamped invoice:
   - a refund of an invoiced unit is refused, naming the invoice;
   - a refund of an uninvoiced line of the same order is accepted when its line is edited on a draft refund;
   - the draft is cancelled afterwards and never completed, so no stock or money moves.

   No row outside that draft refund changes.

## 5. SC-005: email (confirm first; sends real mail)

Email one stamped CFDI 4.0 invoice from mbe_dev to an address the user names. Check that both attachments arrive and the incidence is recorded. Repeat with a refused address to see the 502.
