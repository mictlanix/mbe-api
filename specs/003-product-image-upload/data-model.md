# Data Model: Product Image Upload

## Existing Entities (unchanged)

### Product (`app/models/product.py`)

No schema changes required.

| Column | Type | Notes |
|--------|------|-------|
| `product_id` | `int` (PK) | Existing |
| `photo` | `str \| None` | Existing — stores the bare PNG filename (e.g. `a3f9...b7c2.png`) or `null` |

The `photo` column is `String(255)`. A SHA-256 hex digest (64 chars) + `.png` (4 chars) = 68 chars, well within the limit.

## New Artifacts

### Image File (filesystem, not DB-tracked)

| Attribute | Value |
|-----------|-------|
| Location | `{settings.images_dir}/{hash}.png` |
| Naming | SHA-256 hex digest of final PNG bytes + `.png` extension |
| Format | PNG (always, regardless of upload format) |
| Dimensions | Width ≤ 150 px; height scaled proportionally |
| Max size | 2 MB (input) |

Files are never deleted by this feature. If a product's `photo` is cleared, the file remains on
disk (other products may reference the same hash).

## API Response Shape

### `ProductResponse.photo`

The `photo` field in API responses returns the **full public URL** of the image, not the bare
filename stored in the DB.

| Context | Value |
|---------|-------|
| `photo` column in DB | `a3f9...b7c2.png` (bare filename, or `null`) |
| `photo` in API response | `https://api.example.com/images/a3f9...b7c2.png` (or `null`) |

URL construction happens at serialization time in the endpoint layer:
```
{images_base_url}/images/{filename}
```

When `images_base_url` is empty (default dev config), the path is a relative URL: `/images/{filename}`.

## Configuration Change

### `Settings` (`app/core/config.py`)

| Field | Type | Default | Env Var | Notes |
|-------|------|---------|---------|-------|
| `images_dir` | `str` | `"images"` | `IMAGES_DIR` | Directory for stored product images |
| `images_base_url` | `str` | `""` | `IMAGES_BASE_URL` | Base URL for constructing image URLs in responses |

## Validation Rules

| Rule | Enforcement |
|------|-------------|
| File size ≤ 2 MB | Checked in endpoint before processing; HTTP 422 if exceeded |
| Format is JPEG / PNG / GIF / WEBP | Verified by Pillow open attempt; HTTP 422 on failure |
| Product must exist | HTTP 404 if `product_id` not found |
| Caller must be authenticated | HTTP 401 if no valid JWT |
| Caller must have PRODUCTS UPDATE privilege | HTTP 403 if privilege missing |
