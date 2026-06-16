# Contract: Serve Product Image

## Endpoint

`GET /images/{filename}`

## Authentication

None required. This endpoint is intentionally public — product thumbnails contain no sensitive
information (see spec Assumptions).

## Request

**Path parameter**:

| Parameter | Type | Description |
|-----------|------|-------------|
| `filename` | string | The PNG filename (e.g. `a3f9...b7c2.png`) as returned in the `photo` field of a `ProductResponse` |

No query parameters. No request body.

## Responses

### 200 OK — Image served

Returns the raw PNG image bytes.

**Response headers**:

| Header | Value |
|--------|-------|
| `Content-Type` | `image/png` |
| `Content-Length` | size of the file in bytes |
| `ETag` | file fingerprint (starlette-generated) |
| `Last-Modified` | file modification timestamp |

**Response body**: raw PNG binary data.

### 404 Not Found

The requested filename does not exist on disk. This occurs when:
- The filename was never stored (invalid or manually crafted URL).
- The file was deleted from the filesystem after being stored.

No JSON body — the response is a plain 404 from starlette `StaticFiles`.

---

## Notes

- This endpoint is served by starlette `StaticFiles` mounted at `/images`. It is **not** an
  API router endpoint and therefore does not appear under `/api/v1/`.
- The `photo` field in `ProductResponse` always contains the full URL to this endpoint when a
  photo exists, making the URL directly fetchable without any transformation.
- Range requests (`Range` header) are supported by `StaticFiles` for large files.
