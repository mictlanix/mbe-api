# Contract: new error codes (spec 036)

These rows are added to `app/core/error_codes.py` and to `specs/036-localizable-error-codes/contracts/error-codes.md`, keeping its alphabetical order within each domain. `tests/unit/test_contract_docs.py` checks the two against each other.

## fiscal_document (additions)

| Code | HTTP | Params | Meaning |
|---|---|---|---|
| `fiscal_document.cancellation_open` | 409 | — | The document already has a cancellation request in process |
| `fiscal_document.has_live_documents` | 409 | `documents` | Payment receipts or credit notes related to the invoice must be cancelled first |
| `fiscal_document.not_cancellable` | 409 | — | SAT reports the document cannot be cancelled |
| `fiscal_document.nothing_to_refresh` | 409 | — | The document has no stamp or cancellation in process |
| `fiscal_document.stamping` | 409 | — | A stamp is in process; refresh the document instead |
| `fiscal_document.substitution_invalid` | 422 | `substitution` | The substitute is not a stamped, live document of the same issuer and recipient |
| `fiscal_document.substitution_required` | 422 | — | Reason 01 needs the substitute document's UUID |
| `fiscal_document.unknown_cancellation_reason` | 422 | `reason` | The reason is not a SAT cancellation reason (01–04) |

## pac (new domain)

| Code | HTTP | Params | Meaning |
|---|---|---|---|
| `pac.not_configured` | 503 | — | No PAC provider is configured on this instance |
| `pac.rejected` | 422 | `pac_code`, `pac_message` | The PAC or SAT rejected the request |
| `pac.timeout` | 504 | `state` | The PAC did not answer in time. The document is left `stamping` or `cancellation_pending`; refresh it |
| `pac.unavailable` | 503 | — | The PAC could not be reached. Nothing changed; retry |

## Reused codes

| Code | Where |
|---|---|
| `fiscal_document.not_sealed` | Stamping a draft |
| `fiscal_document.not_issued` | Cancelling a document that is not issued |
| `fiscal_document.issue_date_out_of_range` | Stamping after the 72-hour window |
| `fiscal_document.invoice_not_payable` | Adding a receipt to, or confirming one against, an invoice with an open cancellation |
| `fiscal_document.invoice_not_creditable` | Same, for credit notes |
| `lock.busy` | Another request holds the document's row lock; `resource` is `fiscal_document` |
