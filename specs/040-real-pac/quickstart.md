# Quickstart: Connect stamping and cancellation to the real PAC

How to prove spec 040 works. The contracts are in [contracts/](contracts/), and the decisions are in [research.md](research.md).

## 1. Offline suite

```bash
DATABASE_URL='sqlite+aiosqlite:///:memory:' JWT_SECRET_KEY=test-secret-test-secret-test-secret-0123 \
BUSINESS_TIMEZONE=America/Mexico_City uv run pytest -q
uv run ruff check app/ migrations/ tests/
```

**Expected:** all tests pass, and the live tests are skipped. The new tests cover:
- the translation table and the error mapping (R3);
- the resubmission in each outcome (R4, SC-003);
- the 72-hour wait (R5);
- each startup refusal (SC-006);
- the simulator's new RFC.

## 2. Live tests (opt-in)

```bash
# Multifacturas test mode: stamp twice with the same UUID; cancellation answers SAT 205
MULTIFACTURAS_USER=… MULTIFACTURAS_PASSWORD=… uv run pytest tests/live -q
# SAT's status service: a valid and a cancelled UUID of the current system (issuer|recipient|uuid|total)
SAT_LIVE_VALID='…' SAT_LIVE_CANCELLED='…' uv run pytest tests/live -q
```

The values are given on the command line and never committed.

## 3. Deploy (each step confirmed with the user)

1. **Deploy key:** add xolotl's `mictlanix` public key as a read-only deploy key on mictlanix/cfdi. Check it with `ssh -T git@github.com` on xolotl.
2. **Migration 035** on mbe_dev, then restart dev and test on the new code at once (they share mbe_dev).
3. **Test's `.env`:** set `PAC_PROVIDER=multifacturas`, `MULTIFACTURAS_USER`, `MULTIFACTURAS_PASSWORD` and `MULTIFACTURAS_PRODUCTION=false`, keep `APP_ENV=staging`, then restart.
4. **Check:**
   - test starts;
   - a start with `MULTIFACTURAS_PRODUCTION=true` is refused;
   - the journal shows no credential.

## 4. Walk on test (SC-001, SC-002)

| Document | Stamp: UUID / provider RFC / PDF mark | Cancel 02: answer | Cancel 01 + substitute: answer |
|---|---|---|---|
| Invoice | _pending_ | _pending_ | _pending_ |
| Payment receipt | _pending_ | _pending_ | _pending_ |
| Credit note | _pending_ | _pending_ | _pending_ |

**Expected**
- **Stamping:** each document is issued with a UUID from the PAC, provider RFC `LSO1306189R5` or the one test mode answers, and no "DOCUMENTO SIMULADO" in its PDF.
- **Cancelling** in test mode answers `pac.rejected` with `pac_code` 205. The request closes as rejected, and the document stays issued.
- **Refresh** of a timed-out stamp is exercised offline only; test mode cannot be made to time out on demand.

**Fallback:** if test mode refuses a real issuer's CSD, record its answer here and see research R9.

## 5. Confirm at the cutover (#57)

These can't be verified in test mode:
- SAT's answer to a non-cancellable document;
- `acuseSAT` on SAT's 307 in production;
- a real 201 → pending or cancelled → the receiver's answer;
- the answer to wrong credentials;
- assumption A3: the duplicate check comes before validation.
