# Contract: PAC provider (changes from spec 039)

The contract is the one in `specs/039-cfdi-stamping-cancellation/contracts/pac-provider.md`, in `app/services/pac/provider.py`. This spec changes the three things below and nothing else.

## 1. `resolves_by_resubmitting`

```python
class PacProvider(Protocol):
    #: True when the PAC cannot look a stamp up but answers a resubmission of the same sealed
    #: document with the stamp it already issued. Refresh then resolves a document left
    #: `stamping` by calling `stamp` again with the stored sealed XML, never `find_stamp`.
    resolves_by_resubmitting: ClassVar[bool]
```

| Provider | Value | `find_stamp` |
|---|---|---|
| `SimulatedPac` | `False` | looks the stamp up in `pac_simulation` (spec 039) |
| `MultifacturasProvider` | `True` | not called by the API. Implemented as `return None`, so the class satisfies the protocol. |

## 2. `SIMULATED_PAC_RFC`

```python
SIMULATED_PAC_RFC = 'SIM010101SI0'   # was 'SIM010101SIM'; RfcProvCertif must end in a digit or 'A'
```

## 3. The Multifacturas provider

Module: `app/services/pac/multifacturas.py`.

```python
class MultifacturasProvider:
    resolves_by_resubmitting = True

    def __init__(self, pac: mictlanix.cfdi.pac.PacClient,
                 sat: mictlanix.cfdi.sat.SatStatusClient) -> None: ...

    async def stamp(self, sealed_xml: str) -> Stamp: ...
    async def find_stamp(self, sealed_xml: str) -> Stamp | None: ...      # always None
    async def request_cancellation(self, request: CancellationRequest) -> CancellationAnswer: ...
    async def query_status(self, issuer_rfc: str, recipient_rfc: str, uuid: str,
                           total: Decimal) -> SatStatus: ...
```

**What each method calls**
- **`stamp` and `request_cancellation`** call the library's `PacClient`.
- **`query_status`** calls the library's `SatStatusClient`.
- **Translation:** every value and error is translated as in [research.md R3](../research.md#r3-translation-table).

**What may leave the provider**
- **Errors:** only the contract's `PacRejected`, `PacUnavailable` and `PacTimeout`, each raised `from None`.
- **Answers:** `request_cancellation` returns only `ACCEPTED` (SAT 202) or `PENDING` (SAT 201).

**`repr`:** `repr(provider)` shows the library clients' own `repr`, which hides the password.

### How `get_pac_provider()` builds it

```python
MultifacturasProvider(
    MultifacturasPac(MultifacturasSettings(
        user=settings.multifacturas_user,
        password=settings.multifacturas_password.get_secret_value(),
        production=settings.multifacturas_production,
        base_url=settings.multifacturas_url,
        connect_timeout=settings.multifacturas_connect_timeout,
        read_timeout=settings.multifacturas_read_timeout,
    )),
    SatConsultaClient(),
)
```

Endpoints (`contracts/endpoints.md` of spec 039) and error codes (`contracts/error-codes.md` of spec 039) do not change.
