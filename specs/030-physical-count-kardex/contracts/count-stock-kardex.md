# Contract: Counts, Stock and Kardex

Base path: `/api/v1`. Every route needs a bearer token, and administrators bypass the privilege checks. Errors use the usual `{"detail": "..."}` shape.

## Counts: `/inventory-counts` (object 74, PhysicalCountAdjustment)

| Method | Path | Right | Body / query | Success |
|---|---|---|---|---|
| GET | `` | Read | `status` (`draft`, `completed`, `cancelled`), `warehouse`, `skip`, `limit` | 200 `ListResponse[InventoryCountSummary]` |
| POST | `` | Create | `{warehouse, comment?}` | 201 `InventoryCountResponse` |
| GET | `/{id}` | Read | — | 200 |
| PUT | `/{id}` | Update | `{warehouse?, comment?}` | 200 |
| PUT | `/{id}/lines` | Update | `{lines: [{product?, product_code?, quantity ≥ 0}]}`, 1–2,000 items; each item names exactly one of `product` or `product_code` | 200 |
| PUT | `/{id}/lines/{line_id}` | Update | `{quantity ≥ 0}` | 200 |
| DELETE | `/{id}/lines/{line_id}` | Update | — | 200 |
| POST | `/{id}/confirm` | Update | `{include_uncounted: bool = false}` | 200 `InventoryCountConfirmResponse` |
| POST | `/{id}/cancel` | Delete | — | 200; 409 if confirmed |

There are 9 routes; `GET ''` and `GET /{id}` share the read right.

- **`InventoryCountResponse`:**
  - `inventory_count_id`, `facility`, `warehouse`, `serial`, `status`, `comment`, `creator`, `updater`, `creation_time`, `modification_time`;
  - `lines[]`: `{count_detail_id, product, product_code, product_name, quantity, quantity_on_hand, difference}`. The last two are null while a draft (blind).
- **`InventoryCountConfirmResponse`:** the count, plus:
  - `negative_available[]`: `{product, product_code, counted, reserved, available}`;
  - `corrected_by_other_counts[]`: `{product, product_code, inventory_count_id, serial}`.
- **Batch refusal (422):** `{"detail": "...", "errors": [{"index", "product" | "product_code", "reason"}]}`.

## Stock: `/stock` (object 67, WarehouseStockReport, Read)

| Method | Path | Query | Success |
|---|---|---|---|
| GET | `` | `warehouse` (required), `q`, `include_zero` (false), `skip`, `limit` (≤ 500) | 200 `ListResponse[StockRow]` |
| GET | `/products/{product}` | — | 200 `ProductStock` |

## Kardex: `/kardex` (object 32, Kardex, Read)

| Method | Path | Query | Success |
|---|---|---|---|
| GET | `` | `product`, `warehouse`, `from` (date), `to` (date, inclusive) | 200 `Kardex` |

## Status codes

| Code | When |
|---|---|
| 401 | Missing or invalid token |
| 403 | The right is missing, or a non-administrator confirms or cancels in another facility |
| 404 | Unknown count, line, warehouse or product |
| 409 | Not a draft; a confirmed count cancelled; no lines; busy (retry) |
| 422 | Validation: quantities, batch errors, an in-transit warehouse, the kardex range |
