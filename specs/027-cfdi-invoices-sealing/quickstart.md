# Quickstart: CFDI Invoices up to a Sealed Document

## 1. Local checks

```bash
uv sync                                   # installs lxml
uv run ruff check app/ migrations/ tests/
uv run pytest tests/unit/test_cfdi_invoice.py tests/unit/test_invoice_service.py \
              tests/api/test_taxpayer_batches.py tests/api/test_invoices.py -q
uv run pytest -q                          # the whole suite stays green
```

Expected:
- **Builder and sealing units:**
  - the rebuilt `tests/fixtures/cfdi/invoice.xml` gives the fixture's original string;
  - sealed XML validates against `tests/fixtures/sat/cfdv40.xsd`;
  - the seal verifies with the test certificate;
  - 0 %, exempt, 100 % discount, PPD, XAXX and 07 relation cases each validate.
- **Remaining-quantity units:**
  - the spec's two examples give 0;
  - a refund after a partial invoice frees nothing;
  - a refund before invoicing reduces the remaining quantity.
- **API:** every route has its happy path, 401, 403, 404, 409 and 422 cases (contract table).

## 2. Deploy to the test instance (after confirmation)

Following the xolotl memory notes: as `mictlanix@`, check out the branch in `/var/www/mbe-api-test`, run `uv sync`, then, as root, `systemctl restart mbe-api-test`. No migration.

## 3. SC-002: parity with stamped invoices (read only)

Run `scripts`-style from the scratchpad on xolotl, with `PYTHONPATH=.`:
1. Select stamped CFDI 4.0 invoices covering:
   - order-based;
   - standalone;
   - XAXX;
   - a 0 % line;
   - the exempt line;
   - a discount;
   - PPD;
   - a type 07 relation.
2. For each, rebuild the XML from its rows with `cfdi_invoice.build(...)`. Take `Fecha`, `NoCertificado` and `Certificado` from the stored XML.
3. Compare `original_string(rebuilt)` with `original_string(stored)`. Expect them equal for every sample, and record any difference with its cause.

## 4. SC-003: concurrent confirms on MariaDB (writes to mbe_dev, confirm first)

1. Create a throwaway batch `ZZ027` (type 0) for an issuer with a valid certificate, through the API.
2. Create 20 drafts in that series, each with one manual line.
3. Fire the 20 `POST /confirm` calls concurrently (`httpx.AsyncClient`, `asyncio.gather`).
4. Expect 20 × 200 and serials 1..20 with no duplicates or gaps. Run once more with one draft made invalid (no lines): expect 19 sealed, consecutive, and the invalid one still a draft with no serial.
5. Clean up after confirmation: delete the 20 documents' XML, lines and rows, and then the batch. Each step is listed and approved before it runs.

## 5. SC-004: the clerk's flow on the test instance (writes fiscal tables only)

1. Snapshot `MAX(modification_time)` and row counts of `sales_order`, `customer_refund` and `product`.
2. Then:
   - create an invoice from two real completed orders;
   - change a quantity;
   - add a 07 relation;
   - confirm, and check `status = sealed`, the serial, and that the XML validates;
   - reopen;
   - change the date;
   - confirm again, keeping the same serial.
3. Snapshot again. Only `fiscal_document*` tables changed.
