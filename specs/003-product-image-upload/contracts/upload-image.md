# Contract: Upload Product Image

## Endpoint

`POST /api/v1/products/{product_id}/image`

## Authentication

Bearer JWT required. Caller must have `PRODUCTS` / `UPDATE` privilege.
Returns `401` if unauthenticated, `403` if privilege is missing.

## Request

**Content-Type**: `multipart/form-data`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `file` | file | yes | Image file in JPEG, PNG, GIF, or WEBP format. Max 2 MB. |

## Responses

### 200 OK — Upload successful

Returns the updated `ProductResponse` with `photo` set to the full public URL of the stored image.

```json
{
  "product_id": 42,
  "code": "P001",
  "name": "Widget Alpha",
  "photo": "https://api.example.com/images/a3f9c2d1e4b5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1.png",
  ...
}
```

The URL is directly fetchable without authentication (see [serve-image.md](serve-image.md)).

### 404 Not Found

Product with given `product_id` does not exist.

```json
{ "detail": "Product not found" }
```

### 422 Unprocessable Entity — Validation failure

Returned when:
- File exceeds 2 MB
- File format is not a recognized image (JPEG/PNG/GIF/WEBP)

```json
{ "detail": "..." }
```

### 401 Unauthorized

No valid JWT provided.

### 403 Forbidden

Authenticated but lacks PRODUCTS UPDATE privilege.

---

# Contract: Clear Product Image

Image clearing is handled by the existing update endpoint — no new contract.

`PUT /api/v1/products/{product_id}`

Send `"photo": null` in the JSON body to clear the product's photo field.

The image file is **not** deleted from disk.
