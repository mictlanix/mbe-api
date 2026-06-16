# Research: Product Image Upload

## Image Processing Library

**Decision**: Use `Pillow` (`pillow` PyPI package).

**Rationale**: Pillow is the de-facto standard Python image library. It decodes JPEG, PNG, GIF,
WEBP (and many others), supports resize with LANCZOS resampling for high quality, and can encode
to PNG. No other library in the project covers image processing.

**Alternatives considered**:
- `wand` (ImageMagick bindings) — heavier system dependency, unnecessary for this scope.
- `opencv-python` — ML-focused, much larger footprint than needed.
- stdlib `imghdr` — detection only, no processing capability.

---

## Async Strategy for Synchronous I/O

**Decision**: Wrap all Pillow operations and disk writes in `asyncio.to_thread()`.

**Rationale**: Pillow is fully synchronous. Calling it directly inside an `async def` handler
would block the event loop under concurrent requests, violating Constitution Principle VI
(Async-First). `asyncio.to_thread` runs the callable in a thread-pool executor and returns an
awaitable — zero new dependencies, stdlib only.

**Alternatives considered**:
- `aiofiles` — handles async file I/O but does not help with Pillow's CPU/IO work. Would add a
  dep without fully solving the problem.
- `run_in_executor` directly — equivalent to `asyncio.to_thread` but more verbose; `to_thread` is
  the idiomatic Python 3.9+ form.

---

## Upload Endpoint Design

**Decision**: Add `POST /api/v1/products/{product_id}/image` as a separate route in `products.py`.

**Rationale**: Image upload uses `multipart/form-data` (`UploadFile`), which cannot be mixed with
a JSON body in the same request. A dedicated route keeps the existing PUT endpoint clean (JSON
only) and makes the upload intent explicit in the URL.

**Alternatives considered**:
- Extend `PUT /products/{id}` to accept optional multipart — complicates the schema and breaks
  clients that send `Content-Type: application/json`.
- `PATCH /products/{id}/photo` — semantically valid but less conventional in this codebase where
  all updates use `PUT`.

---

## Content Hash Strategy

**Decision**: SHA-256 of the post-processed PNG bytes (after resize and conversion).

**Rationale**: Hashing the final bytes (not the original upload) ensures two different source
files that produce identical output share a single stored file. SHA-256 is collision-resistant
for practical purposes; no deduplication mitigation is needed. The hex digest gives a 64-char
filename with `.png` extension — well within the `String(255)` column limit.

**Alternatives considered**:
- MD5 — faster but cryptographically broken; not worth the risk even for non-security use.
- UUID — no deduplication benefit; rejected per FR-008.
- Hash of original upload — two different formats of the same visual would produce different hashes
  and duplicate files; hashing the output avoids this.

---

## File Size Enforcement

**Decision**: Reject uploads exceeding 2 MB (per updated spec edge cases) before processing.

**Rationale**: FastAPI's `UploadFile` does not enforce size limits natively. Read the file into
memory, check `len(content) > 2 * 1024 * 1024`, and raise HTTP 422 before passing bytes to
Pillow. This prevents OOM from maliciously large files.

**Alternatives considered**:
- Streaming with byte-count limit — more complex, negligible benefit at 2 MB cap.
- Nginx/proxy-level limit — correct in production but not enforced at the app layer; both are
  complementary. App-layer check is required for correct 422 response.

---

## Privilege Gating

**Decision**: `require_privilege(SystemObject.PRODUCTS, AccessRight.UPDATE)`.

**Rationale**: Image upload modifies the product record (sets `photo`). It is naturally gated by
the same UPDATE right on PRODUCTS that would govern any other product mutation. No new
`SystemObject` enum value is needed.

---

## Configuration

**Decision**: Add `images_dir: str = "images"` and `images_base_url: str = ""` to `Settings` in `app/core/config.py`.

**Rationale**: Follows the existing pattern (e.g., `default_photo_file`, `database_url`) of
declaring all environment-overridable values in `Settings`. The default `"images"` is a relative
path that works out-of-the-box in development; production deployments set `IMAGES_DIR` to an
absolute path. `IMAGES_BASE_URL` is empty by default (produces relative-style bare filename when
unset) and set to the public base URL in production (e.g. `https://api.example.com`).

**Startup validation**: The lifespan event (or a startup check) should verify the directory exists
and is writable, failing fast rather than at upload time.

---

## Image Serving

**Decision**: Mount `StaticFiles` at `/images` in `app/main.py` using FastAPI's bundled starlette
`StaticFiles` — no new package required.

**Rationale**: `StaticFiles` is already available via starlette (installed as a FastAPI dependency).
It handles range requests, `ETag`, `Last-Modified`, `Content-Type` detection, and 404 for missing
files — all for free. A custom endpoint would replicate this functionality with no benefit.

**Options considered**:
- Custom `GET /images/{filename}` FastAPI route — more code, reinvents `StaticFiles` behaviour.
- CDN / object storage — out of scope per spec Assumptions; local filesystem only.
- Serve via nginx/reverse proxy in production — complementary in production but not part of the
  app layer. App must also serve (for dev, tests, and deployments without a separate proxy).

**Mount detail**: `StaticFiles(directory=settings.images_dir, check_dir=False)` — `check_dir=False`
prevents startup failure when the images directory does not yet exist (it is created on first
upload).

---

## Photo URL Construction Strategy

**Decision**: Store bare filename in DB; construct full URL at serialization time in the endpoint
layer using a `_photo_url(filename)` helper that prepends `settings.images_base_url`.

**Rationale**: Separating storage format (filename) from response format (URL) means:
- DB migration is not required when the base URL changes.
- Legacy rows (bare filenames written before URL serving was added) are automatically upgraded at
  read time.
- URL construction is a single, testable helper function.

**URL construction**: `f"{settings.images_base_url}/images/{filename}"` when filename is not None
and `images_base_url` is non-empty; otherwise `f"/images/{filename}"` as a relative fallback.

**Alternatives considered**:
- Store full URL in DB — couples DB to deployment topology; migration required on domain change.
- Pydantic `field_validator` / `model_validator` — forces URL config into the schema layer, which
  is harder to test and mixes concerns. Post-validate assignment in the endpoint is simpler.
