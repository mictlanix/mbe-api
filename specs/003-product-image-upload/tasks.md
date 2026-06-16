# Tasks: Product Image Upload & Serving

**Input**: Design documents from `specs/003-product-image-upload/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Included — constitution requires tests for all new API endpoints.

**Organization**: Tasks grouped by user story for independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to

---

## Phase 1: Setup

**Purpose**: Add new dependency and configuration fields before any code is written.

- [X] T001 Add `pillow` to dependencies in `pyproject.toml` and run `uv sync`
- [X] T002 Add `images_dir: str = "images"` field to `Settings` in `app/core/config.py`

---

## Phase 2: Foundational

**Purpose**: Image processing service that US1 depends on. Must be complete before US1 starts.

**⚠️ CRITICAL**: US1 endpoint depends on this module.

- [X] T003 Create `app/services/image_service.py` with async `process_and_save_image(content: bytes, images_dir: str) -> str` — validates max 2 MB, opens with Pillow (rejects non-image via `UnidentifiedImageError`), takes first frame for animated formats, resizes to 150 px wide if wider (LANCZOS, preserving ratio), converts to PNG bytes, computes SHA-256 hex digest of output bytes, writes file as `{digest}.png` only if it does not already exist (dedup), returns the filename; all Pillow and file I/O wrapped in `asyncio.to_thread`

**Checkpoint**: `image_service.py` complete — US1 implementation can now begin.

---

## Phase 3: User Story 1 — Upload a Product Image (Priority: P1) 🎯 MVP

**Goal**: `POST /api/v1/products/{product_id}/image` accepts a multipart image, processes it, and updates the product's `photo` field.

**Independent Test**: Upload a JPEG for a known product and verify the `photo` field in the response contains a `.png` URL; confirm the file exists on disk.

### Tests for User Story 1

> **Write these tests FIRST — verify they FAIL before implementing T005**

- [X] T004 [US1] Write unit tests for the upload endpoint in `tests/api/test_product_image.py` covering: upload returns 200 with photo URL set (happy path), product not found returns 404, unauthenticated returns 401, file exceeds 2 MB returns 422, non-image file returns 422, uploading same image twice does not create a duplicate file

### Implementation for User Story 1

- [X] T005 [US1] Add `POST /{product_id}/image` route to `app/api/v1/endpoints/products.py` — accept `file: UploadFile = File(...)`, read bytes, call `image_service.process_and_save_image`, call `product_service.update_product` to set `photo`, return `ProductResponse`; gate with `require_privilege(SystemObject.PRODUCTS, AccessRight.UPDATE)`

**Checkpoint**: US1 fully functional — upload, dedup, resize, and photo field update all verified by tests.

---

## Phase 4: User Story 2 — Remove a Product Image (Priority: P2)

**Goal**: Sending `{"photo": null}` via the existing `PUT /api/v1/products/{id}` endpoint clears the product's photo field.

**Independent Test**: Upload an image, then PUT with `{"photo": null}` and confirm the product's `photo` field is `null` in the response.

**Note**: The existing `update_product` service uses `if data.photo is not None` which silently skips null — this prevents clearing the photo. US2 requires fixing this to use Pydantic's `model_fields_set`.

### Tests for User Story 2

> **Write these tests FIRST — verify they FAIL before implementing T007**

- [X] T006 [US2] Add tests for clearing photo in `tests/api/test_product_image.py`: PUT with `{"photo": null}` returns 200 with `photo` as null; PUT without `"photo"` key does not change the existing photo value

### Implementation for User Story 2

- [X] T007 [US2] Fix `update_product` in `app/services/product_service.py` — change `if data.photo is not None: product.photo = data.photo` to `if "photo" in data.model_fields_set: product.photo = data.photo` so that an explicit null clears the field while omitting the key leaves it unchanged

**Checkpoint**: US1 and US2 both pass — upload and clear tested independently.

---

## Phase 5: User Story 3 — Serve a Product Image (Priority: P1)

**Goal**: `GET /images/{filename}` serves stored PNG files without authentication. All `ProductResponse.photo` fields return the full public URL (e.g. `http://host/images/{hash}.png`) instead of the bare filename.

**Independent Test**: Upload an image, take the URL from the product's `photo` field, make an unauthenticated GET to that URL, confirm `200 image/png`; request a non-existent filename, confirm `404`.

### Tests for User Story 3

> **Write these tests FIRST — verify they FAIL before implementing T012–T014**

- [X] T011 [US3] Add serving tests to `tests/api/test_product_image.py` covering: GET /images/{filename} returns 200 with correct content type when file exists (create a real PNG in a temp dir and mount it), GET /images/nonexistent.png returns 404, GET /images/{filename} without Authorization header returns 200 (public access); also update the US1 upload test to assert `photo` is a full URL ending in `.png` (not a bare filename)

### Implementation for User Story 3

- [X] T012 [US3] Add `images_base_url: str = ""` field to `Settings` in `app/core/config.py`
- [X] T013 [US3] Mount `StaticFiles` at `/images` in `app/main.py` — `app.mount("/images", StaticFiles(directory=settings.images_dir, check_dir=False), name="images")`; add `from starlette.staticfiles import StaticFiles` import
- [X] T014 [US3] Add `_photo_url(filename: str | None) -> str | None` helper to `app/api/v1/endpoints/products.py` that returns `f"{settings.images_base_url}/images/{filename}"` when `images_base_url` is set else `f"/images/{filename}"`; update every endpoint that returns `ProductResponse` to post-set `response.photo = _photo_url(product.photo)` after `model_validate`

**Checkpoint**: US3 fully functional — image served publicly, photo field returns full URL in all product responses.

---

## Phase 6: Polish

**Purpose**: Linting gate, changelog, final test run.

- [X] T015 Run `uv run ruff check app/ migrations/ tests/` and fix any violations introduced by the serving changes
- [X] T016 Update `CHANGELOG.md` — add `GET /images/{filename}` public serving endpoint under `[Unreleased] > Added` and `images_base_url` config field; note `ProductResponse.photo` now returns full URL
- [X] T017 Run `uv run pytest tests/ -v` and confirm all tests pass

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately (T001–T002 done)
- **Foundational (Phase 2)**: Depends on T001, T002 — done
- **US1 (Phase 3)**: Depends on T003 — done
- **US2 (Phase 4)**: Independent of US1 — done
- **US3 (Phase 5)**: Depends on US1 complete (photo field must exist); T012 [P] T013 [P] are independent of each other; T014 depends on T012
- **Polish (Phase 6)**: Depends on US3 complete

### Within US3

- T011 (tests) MUST be written and FAIL before T012–T014 (TDD)
- T012 (config) and T013 (mount) are independent — run in parallel
- T014 (URL helper) depends on T012 (needs `images_base_url` from config)

### Parallel Opportunities

- T012 and T013 can run in parallel (different files)

---

## Parallel Example: US3

```bash
# After T011 tests written and confirmed FAILING:
Task T012: "Add images_base_url to Settings in app/core/config.py"
Task T013: "Mount StaticFiles at /images in app/main.py"
# Then sequentially:
Task T014: "Add _photo_url() helper and update all ProductResponse returns in app/api/v1/endpoints/products.py"
```

---

## Implementation Strategy

### US3 Only (remaining work)

1. T011: Write serving tests (TDD — verify FAIL)
2. T012 + T013: Config field + StaticFiles mount (parallel)
3. T014: `_photo_url()` helper + update all `ProductResponse` returns
4. **STOP & VALIDATE**: `uv run pytest tests/api/test_product_image.py -v`
5. T015–T017: Polish, changelog, full test suite

---

## Notes

- `[P]` tasks touch different files and have no blockers — safe to run in parallel
- `StaticFiles(check_dir=False)` is required — prevents startup failure when `images_dir` does not yet exist
- `_photo_url()` helper must handle `None` input (photo not set) and return `None`
- Every endpoint in `products.py` that returns `ProductResponse` must call `_photo_url()` — check: list products, get product, create product, update product, upload image
- Existing tests that assert `photo == "some.png"` (bare filename) must be updated to assert a URL pattern
- Constitution VII: `/images/{filename}` is intentionally public — explicitly marked in spec FR-014
