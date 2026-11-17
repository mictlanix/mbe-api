# Contract: PAC provider

This is the interface that mbe-api owns and that the real PAC client (mictlanix/mbe-roadmap#160) implements. In step 3 (#46, #51) the API swaps the simulated provider for that client, and nothing outside `app/services/pac/` changes.

Module: `app/services/pac/provider.py`. Everything is async. The API never parses the provider's error text.

## Types

```python
class Stamp:                      # TFD 1.1
    uuid: str                     # upper case, 36 chars
    stamped_at: datetime          # FechaTimbrado, Mexico City local time, naive
    sat_seal: str                 # SelloSAT
    sat_certificate: str          # NoCertificadoSAT (20 digits)
    provider_rfc: str             # RfcProvCertif
    xml: str                      # the full stamped CFDI, TFD inside cfdi:Complemento

class Csd:                        # the issuer's certificate, read from taxpayer_certificate
    certificate: bytes            # DER
    key: bytes                    # encrypted PKCS#8 as stored
    password: str

class CancellationRequest:
    issuer_rfc: str
    recipient_rfc: str
    uuid: str
    total: Decimal                # the document's total, as SAT's services expect
    reason: Literal['01', '02', '03', '04']
    substitution: str | None      # required for '01', None otherwise
    csd: Csd

class CancellationAnswer(StrEnum):
    ACCEPTED = 'accepted'               # cancelled at once
    PENDING = 'pending'                 # SAT waits for the receiver
    NOT_CANCELLABLE = 'not_cancellable'

class SatStatus:
    state: Literal['valid', 'cancelled', 'not_found']   # not_found: SAT doesn't know the UUID; the API leaves the document unchanged
    cancellable: Literal['without_acceptance', 'with_acceptance', 'not_cancellable']
    cancellation: Literal['none', 'requested', 'pending', 'accepted', 'rejected', 'expired']
```

## Errors

Every operation raises only these errors, or returns a result:

| Exception | Meaning | Caller's action |
|---|---|---|
| `PacRejected(code: str, message: str)` | The PAC or SAT refused the request. Nothing was done | Surface `pac.rejected` with `{pac_code, pac_message}` |
| `PacUnavailable` | The request did not reach the PAC (connection refused, 5xx before acceptance). Nothing was done | Surface `pac.unavailable`; retry is safe |
| `PacTimeout` | The request may or may not have been processed | Keep the in-flight state; resolve by `find_stamp` or `query_status` |

An implementation MUST NOT put credentials, CSD material or raw PAC payloads in exception messages or logs.

## Operations

```python
class PacProvider(Protocol):
    async def stamp(self, sealed_xml: str) -> Stamp: ...
    async def find_stamp(self, sealed_xml: str) -> Stamp | None: ...
    async def request_cancellation(self, request: CancellationRequest) -> CancellationAnswer: ...
    async def query_status(
        self, issuer_rfc: str, recipient_rfc: str, uuid: str, total: Decimal
    ) -> SatStatus: ...
```

### `stamp(sealed_xml)`
- **Input:** a CFDI 4.0 that is sealed but not stamped.
- **Returns:** the `Stamp`.
- **Duplicates:** if the PAC reports the same document as already stamped (SAT 307), the implementation SHOULD return that existing stamp rather than raise.

### `find_stamp(sealed_xml)`
- **Purpose:** resolves a `stamp` that timed out.
- **Returns:** the stamp the PAC issued for this exact sealed document (same issuer, series, folio and `Sello`), or `None` when the PAC never stamped it.
- **Never stamps.**

### `request_cancellation(request)`
- **Purpose:** asks SAT, through the PAC, to cancel the UUID.
- **Returns:**
  - `ACCEPTED` when SAT cancelled it at once;
  - `PENDING` when the receiver must accept;
  - `NOT_CANCELLABLE` when SAT refuses because of the document's state, for example live related documents at SAT.
- **Errors:** any other refusal is `PacRejected`.

### `query_status(issuer_rfc, recipient_rfc, uuid, total)`
- **Returns:** SAT's current answer for the UUID.
- **Who decides acceptance:** this answer, and `request_cancellation`'s, are the only source for whether a cancellation needs the receiver's acceptance. The API computes no rule of its own (spec FR-019).

## Implementations

| Implementation | Where | Used when |
|---|---|---|
| `SimulatedPac` | `app/services/pac/simulated.py` | `PAC_PROVIDER=simulated`, never with `APP_ENV=production` |
| `ScriptedPac` | `tests/fakes/pac.py` | Tests; proves a second implementation runs the same flows (SC-006) |
| Real client | #160, connected in #46 and #51 | Step 3 |

`get_pac_provider()` returns the configured implementation, or `None` for `PAC_PROVIDER=none`.
