# Contract: endpoints

All routes are under `/api/v1`. Every route needs a bearer token. Like the existing fiscal-document routes, none has a facility scope: fiscal documents belong to an issuer. Every error body follows spec 036 (`code`, `params`, `detail`).

## Fiscal documents

| Method | Route | Privilege | Body | Returns |
|---|---|---|---|---|
| POST | `/fiscal-documents/{id}/stamp` | Update on 23 | – | `FiscalDocumentResponse`, status `issued` |
| POST | `/fiscal-documents/{id}/cancel` | Update on 23 | `{reason: "01".."04", substitution?: uuid}` | `FiscalDocumentResponse`, status `cancelled` or `cancellation_pending` |
| POST | `/fiscal-documents/{id}/refresh` | Update on 23 | – | `FiscalDocumentResponse` in its resolved (or unchanged) state |
| GET | `/fiscal-documents?status=…` | Read on 23 | – | The existing list. `status` also accepts `stamping` and `cancellation_pending` |
| GET | `/fiscal-documents/{id}` | Read on 23 | – | The existing detail, plus `stamp`, `stamping_since`, `cancellation_substitution` and `cancellations[]` (data-model.md) |

Not changed: `/xml` and `/pdf` (issued, pending and cancelled documents) and `/email` (issued and pending).

### `POST /stamp`

| Document state | Result |
|---|---|
| sealed, PAC stamps | 200, issued |
| issued, cancellation_pending, cancelled | 200, unchanged (idempotent repeat) |
| stamping | 409 `fiscal_document.stamping` |
| draft | 409 `fiscal_document.not_sealed` |
| issue date older than 72 h | 422 `fiscal_document.issue_date_out_of_range`; the PAC is not called |
| PAC rejects | 422 `pac.rejected {pac_code, pac_message}`; sealed |
| PAC unavailable | 503 `pac.unavailable`; sealed |
| PAC timeout | 504 `pac.timeout {state: "stamping"}`; stamping |
| no provider | 503 `pac.not_configured` |
| row locked by another request | 409 `lock.busy {resource: "fiscal_document"}` |

### `POST /cancel`

| Condition | Result |
|---|---|
| SAT cancels at once | 200, cancelled |
| SAT waits for the receiver | 200, cancellation_pending |
| not issued (draft, sealed, stamping, cancelled) | 409 `fiscal_document.not_issued` |
| row locked by another request | 409 `lock.busy {resource: "fiscal_document"}` |
| an open request | 409 `fiscal_document.cancellation_open` |
| live related receipts or credit notes | 409 `fiscal_document.has_live_documents {documents: ["P12", "NC3"]}` |
| SAT: not cancellable | 409 `fiscal_document.not_cancellable`; issued |
| unknown reason | 422 `fiscal_document.unknown_cancellation_reason {reason}` |
| 01 without substitution | 422 `fiscal_document.substitution_required` |
| substitution not a stamped, live document of the same issuer and recipient | 422 `fiscal_document.substitution_invalid {substitution}` |
| PAC rejects | 422 `pac.rejected {pac_code, pac_message}`; issued |
| issuer has no certificate valid now | 422 `fiscal_document.no_valid_certificate`; the PAC is not called |
| PAC unavailable | 503 `pac.unavailable`; issued |
| PAC timeout | 504 `pac.timeout {state: "cancellation_pending"}`; cancellation_pending |

### `POST /refresh`

| State | Result |
|---|---|
| stamping | 200, issued (stamp found); sealed (not found, past the 5-minute grace); stamping (not found, within the grace) |
| cancellation_pending | 200, cancelled, issued, or still pending (a timed-out request SAT reports pending becomes `pending`) |
| any other | 409 `fiscal_document.nothing_to_refresh` |
| row locked by another request | 409 `lock.busy {resource: "fiscal_document"}` |
| PAC unavailable or timeout | 503 or 504; unchanged |

## Simulated PAC controls (dev and test only)

Every simulator route answers 404 unless `PAC_PROVIDER=simulated` (checked per request). Each needs Update on 23. The document must be sealed or later, otherwise 409 `fiscal_document.not_sealed`.

| Method | Route | Body | Effect |
|---|---|---|---|
| GET | `/pac-simulator/fiscal-documents/{id}` | – | The simulator's record: `uuid`, `sat_status`, `cancellation_status`, `next_stamp`, `next_cancellation` |
| PUT | `/pac-simulator/fiscal-documents/{id}` | `{stamp?, cancellation?, pac_code?, pac_message?}` | Sets the outcome of the **next** stamp or cancellation call only |
| POST | `/pac-simulator/fiscal-documents/{id}/receiver` | `{answer: "accept" \| "reject" \| "expire"}` | Answers a pending cancellation as the receiver. 409 `fiscal_document.nothing_to_refresh` if none is pending |

`stamp`: `stamped`, `rejected`, `unavailable`, `timeout_stamped`, `timeout_not_stamped`.

`cancellation`: `accepted`, `pending`, `not_cancellable`, `rejected`, `unavailable`, `timeout`.

`pac_code` and `pac_message` default to `CFDI40999` and "Rechazo simulado" for `rejected`.

With nothing chosen:
- **stamp:** `stamped`;
- **cancellation:** `accepted` when the stamp is under 24 hours old, otherwise `pending`.

### Driving each scenario (for the mbe-ui handoff)

| Scenario | Steps |
|---|---|
| Stamped | `stamp` |
| Rejected by the PAC | `PUT …{stamp:"rejected"}` → `stamp` → 422; still sealed |
| PAC unavailable | `PUT …{stamp:"unavailable"}` → `stamp` → 503; still sealed |
| Timeout, did stamp | `PUT …{stamp:"timeout_stamped"}` → `stamp` → 504, stamping → `refresh` → issued |
| Timeout, did not stamp | `PUT …{stamp:"timeout_not_stamped"}` → `stamp` → 504 → `refresh` after 5 min → sealed |
| Cancelled at once | `PUT …{cancellation:"accepted"}` → `cancel` → cancelled |
| Pending, then accepted | `PUT …{cancellation:"pending"}` → `cancel` → pending → `POST …/receiver {accept}` → `refresh` → cancelled |
| Pending, then rejected | same, with `{reject}` → `refresh` → issued; history shows `rejected` |
| Pending, then expired | same, with `{expire}` → `refresh` → cancelled; history shows `expired` |
| Not cancellable | `PUT …{cancellation:"not_cancellable"}` → `cancel` → 409; issued |
| Cancellation rejected by the PAC | `PUT …{cancellation:"rejected"}` → `cancel` → 422 `pac.rejected`; issued; history shows `rejected` |
| PAC unavailable on cancel | `PUT …{cancellation:"unavailable"}` → `cancel` → 503; issued; history shows `failed` |
| Timeout on cancel | `PUT …{cancellation:"timeout"}` → `cancel` → 504, cancellation_pending → `refresh` → pending (SAT has it) |
