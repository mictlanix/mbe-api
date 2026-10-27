# Research: Credit Note Balances and the Facility Rule

All figures are from mbe_dev, read-only, on 2026-10-27. The current system was read on `origin/casa-maestra-2026` of mictlanix/mbe.

## R1. A credit note's amount and remaining (FR-001 to FR-004)

**Decision:**
- **Amount issued:** the backing payment's `amount`.
- **Remaining:**
  - 0 when the note was paid back in cash (`credit_note.cash_session` is not NULL);
  - otherwise the backing payment's unapplied amount, `amount − Σ(amount + amount_change)` over its live applications (spec 033's `unapplied_amount`), floored at 0.
- **Open:** remaining above 0.01, not paid back in cash, and the customer is not the walk-in customer (`settings.default_customer_id`).
- The list computes all three in one SQL query: `credit_note` joined to its `customer_payment`, outer-joined to an aggregate of live applications per payment. The detail runs the same query for one note.

**Rationale:**
- **The current system has no note amount.** Confirming a refund writes `refunded = 0` and leaves `cash_session` unset (`CustomerRefundsController.cs:353-378`). The note's balance is its payment's `Balance = Amount − Σ(Amount + Change)` (`CustomerPayment.cs:135-142`). The API's `refunded − Σ applied` reads 0 on 982 of 984 notes.
- **Paid back in cash means nothing to spend.** `IsRefundedToCustomer` is `CashSession != null` (`CreditNote.cs:53`), and `GetCreditNotes` drops those notes (`ModelHelpers.cs:125-134`). Notes 158 and 462 are the only two. On both, `refunded` equals what the payment still held (1,025.00, and 3,510.00 − 1,965.00 = 1,545.00), so it was handed back. Reporting their payment's unapplied amount would show money nobody can spend.
- **Change counts as used,** as on the payment and its ticket (spec 033 FR-005a). One figure for the note and its payment.

**Measured:**

| Figure | Count | Amount |
|---|---|---|
| Notes | 984 | 11.27M issued |
| Open by the payment's unapplied amount | 330 | 1,778,475.54 |
| Paid back in cash | 2 | 2,570.00 |
| Walk-in customer, open | 191 | 48,147.45 |
| **Spendable** (FR-004) | **137** | **1,727,758.09** |

- No note's payment has a negative unapplied amount.
- Note 990 reads 3.00.

**Alternatives considered:**
- `remaining = unapplied − refunded` for every note: it gives the same 0 for the two notes paid back in cash. But it's a second figure the payment doesn't know about. Rejected for the explicit rule.
- Keeping notes paid back in cash at their payment's unapplied amount, flagged but not open: rejected, because the detail would show 1,025.00 left on note 158.

## R2. What this API writes for a credit note (FR-005)

**Decision:** `_pay_customer_back` writes the note with `refunded = 0` and `cash_session = None`, the latter set explicitly. The cash session stays on the backing payment, as now.

**Rationale:**
- Today it writes `refunded = amount` and `cash_session = session`. By the current system's rule, that marks every API note as paid back in cash: its cashiers couldn't spend it, and this API's open filter (R1) would drop it too.
- The column defaults to 0 in the schema (`cash_session int(11) DEFAULT 0`). So the writer sets NULL explicitly, rather than leaving the column out.
- mbe_dev has no API-issued note (only 158 and 462 have `cash_session` set, and neither matches the API's shape), so there's nothing to correct.

**Alternatives considered:** a migration to rewrite API notes, rejected: there are none.

## R3. Open-only paging (FR-007, FR-008)

**Decision:**
- `open_only` adds R1's open condition to the query's `WHERE`, so the count and the page share it.
- The page's `remaining` and `amount` come from the same query's columns, attached as derived attributes.
- `attach_remaining` and `attach_summary_remaining` are replaced by that query.

**Rationale:** today the filter runs on the page in Python, after `offset/limit`, and `total` counts every note (`credit_note_service.py:97-106`).

**FR-008 check:** every paged list in `app/services` and `app/api/v1/endpoints` was read:
- the credit-note list is the only one filtering after paging;
- `search_outstanding` (spec 033), `accounts_service.summary`, `stock_query_service.sort_and_page` and the itinerary buckets filter before slicing, and count what they keep;
- every other list counts with the clauses its page uses.

## R4. One helper for "this caller sees every facility" (FR-009, FR-014)

**Decision:** add `can_see_every_facility(db, current, system_object)` to `app/core/deps.py`:
- `current.administrator`, or `has_any_privilege(db, current.user_id, [(system_object, AccessRight.READ)])`;
- the two existing private helpers call it: `customer_payments._sees_every_facility` (100) and `accounts_receivable._sees_every_facility` (101);
- the new checks use it too.

**Rationale:** the rule is the same everywhere; only the privilege differs (100 for payments and notes, 101 for sales orders). A fourth and fifth private copy would be the duplication the constitution's Reuse principle forbids.

**Answers:**
- another facility's record: 404 ("… not found"), as `_visible_payment_or_404` answers since #271;
- a named other facility: 403, as `GET /customer-payments?facility=` answers.

## R5. Customer payment writes (FR-010, FR-012)

**Decision:**
- `POST /{id}/applications` and `POST /{id}/applications/{application_id}/reverse` use `_visible_payment_or_404` (PaymentsEditor 100) instead of `_payment_or_404`.
- `POST /{id}/verify` and `POST /{id}/reject` keep `_payment_or_404`: they are company-wide for PaymentsVerification (108) update (clarified).
- `GET /unverified` calls `list_payments` with `cross_facility=True`, so it covers every facility and narrows to a named `facility`. Its `method` filter stays.

**Rationale and impact:**
- The current system's verification screen has no store filter (`PaymentsController.cs:234-268`). No payment dated 2026 has a verifier.
- **Applications:** 742 of the 28,270 live applications in 2026 were made by someone other than the payment's creator. In 613 of those, the person applying took payments only at another facility that day, about 2%. They now need PaymentsEditor read.
- **Credit notes:** 5 of the 647 credit-note applications in 2026 spent a note at another facility.
- **Holders:** 15 of the 22 non-administrators who read customer payments don't hold 100.
- The order's facility stays free (spec 033 FR-021).

**Verification methods:** the current system limits the screen to the methods in `PaymentMethodsVerificationRequired` (`EFT,CreditCard` in its example config). The API already has a `method` filter, and the spec adds no setting (FR-012).

## R6. Sales orders (FR-011)

**Decision:** SearchCreditsFromAllStores (101) read is the cross-facility privilege (clarified).
- `GET /sales-orders`: a `facility` other than the caller's answers 403 without it.
- **Writes:** `PUT /{id}`, `POST /{id}/lines`, `PUT /{id}/lines/{line_id}`, `DELETE /{id}/lines/{line_id}`, `POST /{id}/confirm` and `POST /{id}/cancel` fetch the order through a new `_writable_order_or_404`, which answers 404 for another facility's order without 101.
- **Reads** stay on `_order_or_404`: `GET /{id}`, `/ticket`, `/document`, `/payments`.

**Rationale and impact:**
- The current system has no store privilege for orders: 104 and 105 are commented out (`SystemObjects.cs:242-245`). It opens any store's order by id.
- 101 is the one all-stores privilege in use, scoping accounts receivable's orders (spec 033). 6 non-administrators hold it; 20 of the 26 who read sales orders don't.
- 221 of the 27,942 orders created in 2026 (0.8%) were last changed by someone working at another facility that day.
- Collections pay other facilities' orders (14% of 2026 credit applications, spec 033), so the reads stay open.

## R7. Credit notes and the facility rule (FR-006, FR-013)

**Decision:**
- A note's facility is its backing payment's `facility`, read through the join in R1.
- **List:** filtered to `facility or current.facility_id`, the way `list_payments` filters. A named other facility answers 403 without PaymentsEditor (100) read.
- **Detail:** answers 404 for another facility's note without it.
- The CreditPayments (83) read gate stays.

**Rationale:** a note is a customer payment, and spending one is applying that payment (FR-070a), which R5 scopes the same way.

**Measured:** spendable notes by facility: 8 → 31; 47 → 13; 50 → 33; 51 → 10; 52 → 50.

## R8. Response shape (FR-001 to FR-006)

**Decision:** `CreditNoteResponse` gains:
- `amount` (issued, from the payment);
- `facility`;
- `refunded_to_customer` (paid back in cash).

`refunded` now carries the column's meaning, the amount handed back in cash: 0 on every note but 158 and 462. It previously restated the issued amount.

**Rationale:**
- The column means cash handed back in the current system, and FR-005 writes 0 there.
- A field that restated the issued amount would have to be computed from a different column than its name.
- mbe-ui doesn't read credit notes yet (roadmap#28 is blocked on this spec), so changing the field's meaning breaks no client.

## R9. No schema change

Every figure comes from existing columns, and the cross-facility privileges are existing objects. There is no migration.

## R10. Measurement before and after (SC-001 to SC-005)

- **Before:** recorded above and in the spec. 330 open notes by the payment's unapplied amount; 137 spendable, 1,727,758.09.
- **After:** on the dev deployment.
  - **SC-001:** every note's `amount` and `remaining` against SQL.
  - **SC-002:** page through the open notes at `limit=7` and at 100.
  - **SC-004:** as a non-editor clerk (`carmengodinez`, facility 51: reads 7, 8 and 83, holds neither 100 nor 101), call each path on another facility's record.
  - **SC-005:** the unverified total against SQL.
  - **SC-003:** a refund with a credit-note payout on dev, after confirmation. The check is that the note is spendable here and has `cash_session` NULL.
