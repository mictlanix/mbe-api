# Quickstart: Document Printing

## Prerequisites

- System text libraries (research R1):
  - macOS: `brew install pango`
  - Debian/Ubuntu: `sudo apt install libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libharfbuzz-subset0 libfontconfig1`
- `uv sync`, which pulls `weasyprint==70.0`, `jinja2`, `python-barcode`, and `pypdf` (dev only).
- A running API against `mbe_dev` and a token for a user with sales-order and POS read privileges.

Check that WeasyPrint finds its libraries:

```bash
uv run python -c "import weasyprint; print(weasyprint.__version__)"   # 70.0
```

## Automated checks

```bash
uv run pytest tests/unit/test_rendering.py tests/unit/test_amount_in_words.py \
              tests/unit/test_cash_cut.py tests/unit/test_print_contexts.py \
              tests/api/test_print_endpoints.py \
              tests/integration/test_print_render.py
uv run ruff check app/ migrations/ tests/
```

| Check | Covers |
|---|---|
| Ticket: exactly one page, MediaBox width 204.09 pt, height within 1 mm of the content (1-line and 200-line tickets). Letter: every page 612 × 792 pt | FR-002, SC-002 |
| Render under a fixture that fails on any `socket.connect` / `getaddrinfo`; bytes equal an unpatched render | FR-003, FR-006, SC-003 |
| `app.openapi()`: every PDF route's 200 is `application/pdf` + `{type: string, format: binary}`, with no JSON entry | FR-041, SC-006 |
| `/health` p95 during 10 concurrent renders stays within +100 ms of idle | FR-005, SC-005 |
| Extracted PDF text contains the folio, every line and every total | FR-010/011/020/030 |
| 401 / 403 / 404 / 409 on each route | FR-040, FR-042, FR-033 |

## Manual checks against mbe_dev

Pick one record of each kind that legacy can also print:

```bash
TOKEN=...; API=http://localhost:8000/api/v1
curl -sf -H "Authorization: Bearer $TOKEN" $API/sales-orders/<completed id>/ticket   -o ticket.pdf
curl -sf -H "Authorization: Bearer $TOKEN" $API/sales-orders/<draft id>/ticket       -o preticket.pdf
curl -sf -H "Authorization: Bearer $TOKEN" $API/sales-orders/<id>/document           -o pedido.pdf
curl -sf -H "Authorization: Bearer $TOKEN" $API/cash-sessions/<closed id>/ticket     -o corte.pdf
```

1. **Fidelity (SC-001)**: open each PDF next to legacy's output for the same id. Legacy's paths are `/POS/Pdf/{id}`, `/Payments/Print/{id}`, `/SalesOrders/Pdf/{id}` and `/Payments/PrintCashCount/{id}`. Page width (letter: page size), folio, lines and every total must match. Ticket height is fitted to the content, where legacy's is a fixed 297 mm. Expected differences: fonts render slightly differently, the amount in words follows the corrected wording (research R10), "Ventas en Efectivo" replaces "Efectivo Contado", the credit-note balance follows the API (research R11), and the cut classifies refunds by type and sign, so cash credit notes appear only under Devoluciones (data-model.md › CashCutFigures).
2. **Open session**: `curl -i …/cash-sessions/<open id>/ticket` → `409`.
3. **Thermal printer (SC-007)**: open `ticket.pdf` in Chrome, print at 100% scale with margins set to "None" on the 72 mm printer, and check that nothing is clipped and the barcode scans back to the 8-digit id.
4. **Timing (SC-004)**: `curl -w '%{time_total}\n' -o /dev/null …/ticket` stays under 1 s.

## Results (2026-09-25)

| Check | Result |
|---|---|
| Automated suite | 2,718 passed, ruff clean. Includes geometry, zero-network with byte-identical output, bundled fonts only, OpenAPI binary schema, 401/403/404/409, and the route sweep with no 500s. |
| Event loop (SC-005) | `/health` p95 rose by 0.05–0.14 ms with 10 renders in flight, against a 100 ms budget. |
| Real data, read-only | 280 renders from `mbe_dev` through the real context builders, 0 failures, every page at the right size and only bundled fonts. The sample covered: recent completed, draft, cancelled and unpaid credit orders; 2013-era orders; orders with refunds and credit notes; the longest orders (up to 106 lines, 4 letter pages); and closed cash sessions from 2024, 2025 (negative credit notes) and the latest. It surfaced two fallback-font causes, soft hyphens and mojibake control characters, now fixed (research R5). |
| Timing (SC-004) | Real-data render time p50 88–123 ms, p95 196–414 ms. The max, about 2 s, was the 106-line, 4-page letter document, above SC-004's 50-line scope. |
| Timing over HTTP (SC-004), 2026-09-29 | **Passed with the API next to the database.** The API ran on xolotl at `127.0.0.1:8765` (`/var/www/mbe-api-dev`, tmux session `mbe-api-dev`), against the local MariaDB socket, with 10 requests per route from the same host. Sale ticket 337607: p50 0.092 s, max 0.198 s. Ticket 337619: p50 0.089 s, max 0.095 s. Pedido 337607: p50 0.087 s, max 0.195 s. Cash cut 10035: p50 0.092 s, max 0.204 s. At SC-004's scope, a 48-line order (247681): ticket p50 0.353 s, max 0.464 s; pedido p50 0.309 s, max 0.423 s. The JSON `GET /sales-orders/337607` took 0.011 s. An earlier run from a workstation through the SSH tunnel took 1.0–1.65 s per request, because the tunnel adds about 65 ms to each of 11–15 queries. That latency comes from the setup, not the renderer. The open session 409 was also confirmed there (`/cash-sessions/10428/ticket`). |
| Fidelity against legacy (SC-001), 2026-09-29 | **Passed on content**, after `mbe_dev` was recreated from a fresh copy and fully migrated. Checked: sale ticket 337607, ticket 337619 (credit, discounted), pedido 337607 and cash cut 10035. Legacy's PDFs are Phantom rasters with no text layer, so the comparison was visual. Every folio, date, seller, customer, term, due date, line (quantity, price, discount %, code, description, amount), total, payment, amount in words and cut figure matches, with these exceptions: (1) Fixed: payment references were zero-padded (`Nota de Crédito - 00294674`). Legacy prints the raw id (`Payments/Print.cshtml:118,137`), and so does ours now (research R9). (2) Expected: 337619 prints Subtotal $15,724.13 / IVA $2,427.59, against legacy's $15,724.14 / $2,427.58, with the same $17,600.00 total. Legacy's IVA is a per-line remainder (`SalesOrderDetail.cs:141`); ours is the API's `tax_total` (research R11). The user chose to keep the API's IVA. (3) Expected: the pedido labels the date "Fecha", where legacy says "Fecha y Hora" but also prints only the date. The user chose "Fecha". Layout, text scale (legacy's is about 1.36× larger) and the logo are out of scope: the look will be redesigned. Legacy defects found here are filed as mictlanix/mbe#60 (cash-cut classification), #61 (amount in words) and #62 (labels). |
| Open session 409 | Passed against a running server, 2026-09-29: `GET /cash-sessions/10011/ticket` → `409 {"detail":"Cash session is not closed"}`. |
| Thermal printer (SC-007) | **Pending**: needs the physical 80 mm printer, including that Chrome prints the fitted page at actual size. |

