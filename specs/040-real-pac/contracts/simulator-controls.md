# Contract: Simulated PAC controls (changes from spec 039)

> **Removed 2026-11-24.** These routes no longer exist: `PAC_PROVIDER=simulated` goes through the same `/stamp`, `/cancel` and `/refresh` routes as the real PAC, and the API exposes no controls for the library's simulator. This contract records what spec 040 built.

Spec 039's section "Simulated PAC controls" in `contracts/endpoints.md` still holds for:
- the routes;
- the Update privilege on 23;
- the 404 `request.route_not_found` unless `PAC_PROVIDER=simulated` (per request, before authentication);
- the 409 `fiscal_document.not_sealed` for a draft;
- the request body.

This spec changes what the routes return and when an outcome can be set. The library's simulated PAC now answers behind them (research R12). The routes get it through the FastAPI dependency `get_pac_simulator()` (contracts/pac-provider.md §5), which tests override.

| Method | Route | Body | Effect |
|---|---|---|---|
| GET | `/pac-simulator/fiscal-documents/{id}` | – | The simulator's view of the document: `uuid`, `sat_status`, `cancellation_status` |
| PUT | `/pac-simulator/fiscal-documents/{id}` | `{stamp?, cancellation?, pac_code?, pac_message?}` | Sets the outcome of the **next** stamp of this sealed document, or of the next cancellation of its UUID. Returns the view, as GET. |
| POST | `/pac-simulator/fiscal-documents/{id}/receiver` | `{answer: "accept" \| "reject" \| "expire"}` | Answers a pending cancellation as the receiver and returns the view, as GET |

## Response (`PacSimulationResponse`)

| Field | Value |
|---|---|
| `uuid` | the document's stamp UUID, or null |
| `sat_status` | `valid`, `cancelled` or `not_found`. `not_found` when the document has no UUID yet. |
| `cancellation_status` | `none`, `pending`, `accepted`, `rejected` or `expired` |

**Removed:** `next_stamp` and `next_cancellation`. The library cannot read back a queued outcome (spec Clarifications, 2026-11-18).

## Rules

- **`stamp`** (`stamped`, `rejected`, `unavailable`, `timeout_stamped`, `timeout_not_stamped`):
  - **Keyed by the sealed document.** The outcome belongs to the document as sealed now; reopening and sealing again discards it.
  - **On a document the simulator already stamped,** the outcome is accepted but has no effect: the next stamp returns the existing stamp.
  - **On a document the old simulator stamped,** the library answers 307, and the route answers 422 `pac.rejected` with `pac_code` `307`.
  - **On a document that is `stamping`,** the route answers 409 `fiscal_document.stamping` and writes nothing. Another process may be storing that document's stamp in the simulator, and the simulator's instances don't lock each other.
- **`cancellation`** (`accepted`, `pending`, `not_cancellable`, `rejected`, `unavailable`, `timeout`):
  - **Keyed by the UUID.** On a document not yet stamped, the route answers 409 `fiscal_document.not_stamped`.
  - **`not_cancellable` is permanent** for that UUID.
- **`pac_code` and `pac_message`** go to the outcome that is `rejected`. When both outcomes are `rejected`, both use them. With none given, they default to `CFDI40999` and "Rechazo simulado".
- **`receiver`:** with no cancellation pending, or no UUID, the route answers 409 `fiscal_document.nothing_to_refresh`.
- **With nothing chosen:** a stamp succeeds. A cancellation is accepted at once when the stamp is under 24 hours old, and pending after. The same holds for a UUID the simulator never stamped, which is treated as older than 24 hours.
- **Other failures:** any other library refusal answers `pac.rejected`, a store failure `pac.unavailable`.
