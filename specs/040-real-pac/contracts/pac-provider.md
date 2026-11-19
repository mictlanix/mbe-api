# Contract: PAC provider (changes from spec 039)

The contract is the one in `specs/039-cfdi-stamping-cancellation/contracts/pac-provider.md`, in `app/services/pac/provider.py`. This spec changes the three things below and nothing else.

## 1. `resolves_by_resubmitting`

```python
class PacProvider(Protocol):
    #: True when the PAC cannot look a stamp up but answers a resubmission of the same sealed
    #: document with the stamp it already issued. Refresh then resolves a document left
    #: `stamping` by calling `stamp` again with the stored sealed XML, never `find_stamp`.
    resolves_by_resubmitting: bool
```

## 2. `SIMULATED_PAC_RFC`

```python
from mictlanix.cfdi.pac import SIMULATED_PAC_RFC   # 'SIM010101SI0'; was mbe-api's 'SIM010101SIM'
```

`provider.py` re-exports the library's constant, so the simulated mark always matches what the library's simulator stamps.

## 3. The one implementation: `CfdiPacProvider`

Module: `app/services/pac/adapter.py`. It replaces `app/services/pac/simulated.py`, which is deleted.

```python
class CfdiPacProvider:
    def __init__(self, pac: mictlanix.cfdi.pac.PacClient,
                 sat: mictlanix.cfdi.sat.SatStatusClient, *,
                 name: str, resolves_by_resubmitting: bool) -> None: ...

    resolves_by_resubmitting: bool

    async def stamp(self, sealed_xml: str) -> Stamp: ...
    async def find_stamp(self, sealed_xml: str) -> Stamp | None: ...
    async def request_cancellation(self, request: CancellationRequest) -> CancellationAnswer: ...
    async def query_status(self, issuer_rfc: str, recipient_rfc: str, uuid: str,
                           total: Decimal) -> SatStatus: ...
```

**What each method calls**
- **`stamp`, `find_stamp` and `request_cancellation`** call the library's `PacClient`. Multifacturas' `find_stamp` always answers None. The API never calls it there, because `resolves_by_resubmitting` is True.
- **`query_status`** calls the library's `SatStatusClient`.
- **Translation:** every value and error is translated as in [research.md R3](../research.md#r3-translation-table).

**What it logs:** `stamp` writes `pac {name}: stamp call {issuer} {series}{folio}` at WARNING, one line per call, and never the XML.

**What may leave the adapter**
- **Errors:** only the contract's `PacRejected`, `PacUnavailable` and `PacTimeout`, each raised `from None`.
- **`repr`:** `repr(provider)` shows the library clients' own `repr`, which hides the password.

### How `get_pac_provider()` builds it

```python
if settings.pac_provider == 'simulated':
    pac = SimulatedPac(DatabaseSimulatorStore())
    return CfdiPacProvider(pac, SimulatedSatStatusClient(pac),
                           name='simulator', resolves_by_resubmitting=False)
if settings.pac_provider == 'multifacturas':
    return CfdiPacProvider(
        MultifacturasPac(MultifacturasSettings(
            user=settings.multifacturas_user,
            password=settings.multifacturas_password.get_secret_value(),
            production=settings.multifacturas_production,
            base_url=settings.multifacturas_url,
            connect_timeout=settings.multifacturas_connect_timeout,
            read_timeout=settings.multifacturas_read_timeout,
        )),
        SatConsultaClient(),
        name='multifacturas', resolves_by_resubmitting=True,
    )
return None
```

## 4. The simulator's store: `DatabaseSimulatorStore`

Module: `app/services/pac/simulator_store.py`. It implements the library's `SimulatorStore`.

```python
class DatabaseSimulatorStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession] = AsyncSessionLocal): ...
    async def get(self, key: str) -> str | None: ...       # its own session
    async def set(self, key: str, value: str) -> None: ... # its own session; merge, commit
```

The table is `pac_simulator_store` (data-model.md). The store keeps the values without reading them.

Endpoints (spec 039's `contracts/endpoints.md`) and error codes (spec 039's `contracts/error-codes.md`) do not change, except the simulator routes in [simulator-controls.md](simulator-controls.md).
