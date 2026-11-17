# Contract: new error codes (spec 036)

These rows are added to `app/core/error_codes.py` and to `specs/036-localizable-error-codes/contracts/error-codes.md`, keeping its alphabetical order within each domain. `tests/unit/test_contract_docs.py` checks the two against each other.

## fiscal_document (additions)

| Code | HTTP | Params | Meaning |
|---|---|---|---|
| `fiscal_document.cancellation_open` | 409 | — | The document already has a cancellation request in process |
| `fiscal_document.has_live_documents` | 409 | documents: list[str] (`batch + serial` of each live document) | Payment receipts or credit notes related to the invoice must be cancelled first |
| `fiscal_document.not_cancellable` | 409 | — | SAT reports the document cannot be cancelled |
| `fiscal_document.nothing_to_refresh` | 409 | — | The document has no stamp or cancellation in process |
| `fiscal_document.stamping` | 409 | — | A stamp is in process; refresh the document instead |
| `fiscal_document.substitution_invalid` | 422 | substitution: str (the UUID given) | The substitute is not a stamped, live document of the same issuer and recipient |
| `fiscal_document.substitution_required` | 422 | — | Reason 01 needs the substitute document's UUID |
| `fiscal_document.unknown_cancellation_reason` | 422 | reason: str (the value given) | The reason is not a SAT cancellation reason (01–04) |

## pac (new domain)

| Code | HTTP | Params | Meaning |
|---|---|---|---|
| `pac.not_configured` | 503 | — | No PAC provider is configured on this instance |
| `pac.rejected` | 422 | pac_code: str, pac_message: str (display-only PAC text, the one free-text param; stated exception to 036's raw-values rule) | The PAC or SAT rejected the request |
| `pac.timeout` | 504 | state: str (`draft`, `sealed`, `stamping`, `issued`, `cancellation_pending` or `cancelled`: the document's current state, normally `stamping` or `cancellation_pending`. Not `enum:DocumentStatus`, which 036 uses for the sales documents' status) | The PAC did not answer in time. The document is normally left `stamping` or `cancellation_pending`; refresh it |
| `pac.unavailable` | 503 | — | The PAC could not be reached. The document's state is unchanged (a cancel request is closed as `failed`); retry |

## Reused codes

| Code | Where |
|---|---|
| `fiscal_document.not_sealed` | Stamping a draft; the simulator routes on a draft; reopening a stamping document (R15 rule 8). Its 036 Situation text is widened to cover these |
| `fiscal_document.not_issued` | Cancelling a document that is not issued (draft, sealed, stamping or cancelled). Its spec 036 Situation text is widened to cover cancel as well as XML/PDF |
| `fiscal_document.no_valid_certificate` | Cancelling when the issuer has no certificate valid now. Its 036 Situation text (valid at the issue date) is widened to cover cancel |
| `fiscal_document.issue_date_out_of_range` | Stamping after the 72-hour window |
| `fiscal_document.invoice_not_payable` | Adding a receipt to, or confirming one against, an invoice with an open cancellation |
| `fiscal_document.invoice_not_creditable` | Same, for credit notes |
| `lock.busy` | Another request holds the document's row lock; `resource` is `fiscal_document` |
