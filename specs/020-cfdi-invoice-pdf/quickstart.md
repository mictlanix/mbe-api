# Quickstart: CFDI Invoice PDF

## Prerequisites

- Spec 019's system libraries (pango), plus `uv sync`, which now also pulls `segno`.
- **Logo, an operations step**: every batch points at `casamaestra.png`. Copy legacy's `mbe/Web/Content/images/casamaestra.png` into `IMAGES_DIR`, or invoices print without a logo (research R5).

## Automated checks

```bash
uv run pytest tests/unit/test_cfdi.py tests/unit/test_rendering.py \
              tests/unit/test_fiscal_document_service.py tests/unit/test_print_contexts.py \
              tests/api/test_fiscal_documents.py tests/api/test_print_endpoints.py \
              tests/integration/test_fiscal_document_queries.py tests/integration/test_print_render.py
uv run ruff check app/ migrations/ tests/
```

| Check | Covers |
|---|---|
| TFD original string rebuilt from fixture XMLs, with and without `Leyenda` | FR-008 |
| QR payload for an invoice, a payment receipt (`tt=0`), an RFC with `&` (`%26`) and one with `Ñ` (unchanged); the SVG equals `segno`'s output for that payload | FR-007 |
| Batch settings: single-quoted template, malformed text, missing row, wrong types | FR-014 |
| Extracted PDF text holds every section's values for an invoice, a credit note, an applied advance and a payment receipt | FR-005, FR-009 to FR-013 |
| Cancelled document: `CANCELADO` and the date on every page of a two-page render | FR-022 |
| Footer and `Página N de M` on every page | FR-015 |
| 401 / 403 / 404 / 409 (each detail) on every route; XML bytes equal to the stored text | FR-004, FR-016 to FR-018 |
| OpenAPI: PDF and XML 200s are binary, with no JSON entry | FR-020 |
| Zero network and byte-identical output across two renders | FR-019 |

## Manual checks against mbe_dev

1. **Fidelity (SC-001)**: for one stamped 4.0 document of each type (0, 100, 101, 200), compare every printed value with legacy's `/FiscalDocuments/Pdf/{id}`. The comparison is on content, not layout. Expected differences:
   - the corrected amount in words;
   - the cancellation mark;
   - no logo until the operations step above is done.
2. **Sample (SC-002, SC-003, SC-005, SC-007)**: for at least 200 stamped 4.0 documents:
   - the QR payload is built from the XML;
   - the printed total, UUID, seals and certificates equal the XML's;
   - the XML download is byte-identical to the stored text;
   - rendering the same documents with outbound network blocked gives byte-identical PDFs.
3. **Phone scan (SC-006)**: scan the QR code of:
   - one normal invoice;
   - document 57904, the one whose recipient RFC contains `&` (`MA&990222V79`).

   No stamped 4.0 document has `Ñ` in an RFC, so that case is covered only by `tests/unit/test_cfdi.py`.

   Each must open SAT's page and report the CFDI as valid (vigente), or as cancelled for a cancelled one.
4. **Timing (SC-004)**: on xolotl, over localhost, a document of up to 50 lines returns in under 1 s.

## Results (2026-09-30)

| Check | Result |
|---|---|
| Automated suite | 2,831 passed (113 new), `ruff check app/ migrations/ tests/` clean. Includes the parse, original-string, QR-payload and batch-settings unit tests; the context tests against a row that deliberately disagrees with the XML; the list, detail, XML and PDF route tests, with every 409 in contract order; the renders of all four types, retentions, an 80-concept document with its footer and counter on every page, the cancellation mark on every page; zero network and byte-identical output with the CFDI in the URL list; and the route sweep with no 500s. |
| Sample (SC-002, SC-003, SC-007), xolotl | **Passed, 200 of 200.** 150 random stamped 4.0 invoices plus 50 other 4.0 documents (28 payment receipts, 19 applied advances, 3 credit notes); 196 rendered on one page, 4 on two. SC-002: every QR payload equals the one built from the XML, with the XML's UUID, `Total` and last 8 characters of `Sello`. SC-003: every PDF's text contains the XML's UUID, both seals, both certificate numbers and the printed total. SC-007: every `GET /fiscal-documents/{id}/xml` body is byte-identical to the stored text. Render time p50 159 ms, p95 234 ms, max 332 ms. |
| Zero network, byte-identical (SC-005), xolotl | **Passed, 200 of 200**, re-run 2026-09-30 after `libharfbuzz-subset0` was installed on xolotl. Every document re-rendered with outbound network blocked gives the same bytes, and four renders of 47815 1.1 s apart are identical. The first run had failed because the library was missing: WeasyPrint fell back to fontTools, which timestamps font subsets. That warning is gone. Render time p50 136 ms, p95 206 ms, max 273 ms. |
| Logo (operations step), xolotl | Done 2026-09-30: `casamaestra.png` is in `IMAGES_DIR` (`/var/www/mbe-api-dev/images`). Invoice 63868 embeds it, and the 9 samples still pass every check. |
| Timing over HTTP (SC-004), xolotl | **Passed.** 10 requests each over localhost: invoice 63868 p50 0.174 s, max 0.274 s; payment receipt 63165 p50 0.166 s, max 0.274 s; the largest 4.0 document within SC-004's scope, 54763 with 50 lines, p50 0.485 s, max 0.596 s. XML download p50 0.007 s; list search `RH11405` p50 0.095 s; a 100-row list page p50 0.026 s. |
| Fidelity against legacy (SC-001), 2026-09-30 | **Passed on content, with one fix and one expected difference.** Compared 9 documents against legacy's `/FiscalDocuments/Pdf/{id}` from `mbe.casa-maestra.com.mx`: invoices 63868 (discount), 46932 (retention), 57904 (`&` RFC), 54763 (50 lines), 57676 (109 lines) and 63748 (cancelled); credit note 60648; applied advance 63821; payment receipt 63165. Legacy's body is an image with no text layer, so it was OCR'd (tesseract) and checked against the 987 values the XMLs call for. Values the OCR missed were matched allowing 1–2 character errors, and those still missing were checked by eye. Our PDFs contain all 987. Checked by eye: 63868 in full; 63748, 63821 and 63165 header, parties and lines; 60648 related section; 46932 retention totals; 54763 page 2 lines with comments; 57676 last page and stamp block. Everything matched except: (1) Fixed: a payment receipt's settled document printed `GR 4702`, where legacy prints `GR 004702`, padded like every folio. (2) Expected: 63165's Uso CFDI. Legacy prints `P01 Por definir` from the database, but the stamped XML says `CP01`. Ours prints the stamped value (research R1). Not content: legacy runs to more pages (54763: 5 against 3; 57676: 8 against 4; 63748: 2 against 1), as its text is larger. Product text with double-encoded characters (`45Âº`) is printed as stamped: the stored XML holds those bytes. |
| Phone scan (SC-006), 2026-09-30 | **Passed**, as reported by the user: the QR codes on the samples open SAT's verification. The samples included invoice 63868 and document 57904, the one recipient RFC with `&`. |
