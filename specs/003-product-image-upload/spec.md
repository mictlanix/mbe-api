# Feature Specification: Product Image Upload & Serving

**Feature Branch**: `003-product-image-upload`

**Created**: 2026-06-15

**Updated**: 2026-06-16

**Status**: Implemented

**Input**: "Add product image upload, serving, and URL exposure to the products endpoint."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Upload a Product Image (Priority: P1)

A user with Update Products privilege uploads an image for a product through the API. The system accepts the file, normalizes it to a standard format and size, stores it, and associates a public URL with the product.

**Why this priority**: Without this, the photo feature is unusable. All other stories depend on images being stored and accessible.

**Independent Test**: Upload a JPEG image for a product and confirm the product's `photo` field contains a publicly accessible URL ending in `.png`.

**Acceptance Scenarios**:

1. **Given** a valid product exists and an authenticated user with Update Products privilege has a JPG/PNG/GIF/WEBP image, **When** they upload the image, **Then** the system saves it as a PNG named by its content hash and the product's `photo` field is set to the full public URL of the image.
2. **Given** an image wider than 150 pixels, **When** it is uploaded, **Then** the stored image is resized to exactly 150 pixels wide with height scaled proportionally.
3. **Given** an image already 150 pixels wide or narrower, **When** it is uploaded, **Then** it is stored as-is (converted to PNG, not enlarged).
4. **Given** the same image is uploaded twice, **When** the second upload is processed, **Then** no duplicate file is created on disk — both products receive the same URL.
5. **Given** an unauthenticated request, **When** an image upload is attempted, **Then** the system returns 401 Unauthorized.

---

### User Story 2 - Remove a Product Image (Priority: P2)

A user with Update Products privilege clears the image from a product, removing the association so the product's `photo` field becomes null.

**Why this priority**: Provides the full lifecycle — operators must be able to correct mistakes or remove outdated images.

**Independent Test**: Upload an image, then clear it and confirm the `photo` field returns null.

**Acceptance Scenarios**:

1. **Given** a product has a photo URL, **When** a user with Update Products privilege sends an update with `photo` set to null, **Then** the product's `photo` field is cleared (the file is not deleted, as other products may reference it).
2. **Given** a product has no photo, **When** a user sends an update with `photo` set to null, **Then** the request succeeds with no error.

---

### User Story 3 - Serve a Product Image (Priority: P1)

Any client — authenticated or not — fetches a product image by its URL and receives the image file directly.

**Why this priority**: Without public serving, the URL stored in the `photo` field is unreachable. This is a hard prerequisite for any UI that renders product images.

**Independent Test**: Upload an image, take the URL from the product's `photo` field, make an unauthenticated HTTP GET to that URL, and confirm an image file is returned.

**Acceptance Scenarios**:

1. **Given** a product image has been uploaded and the product's `photo` contains a URL, **When** any client makes an unauthenticated GET request to that URL, **Then** the server returns the PNG image file with the correct content type.
2. **Given** a request for an image URL whose file does not exist on disk, **When** the request is made, **Then** the server returns 404 Not Found.
3. **Given** a product with no photo (`photo` is null), **When** a client fetches the product, **Then** the `photo` field is null (no broken URL is returned).

---

### Edge Cases

- What happens when a file larger than 2 MB is uploaded? → System rejects with a 422 error before processing.
- What happens when an unsupported format is uploaded (e.g., PDF)? → System rejects with a 422 Unprocessable Entity.
- What if the image storage directory does not exist at startup? → Startup succeeds without it (the static mount does not require the directory); the system creates the directory on demand at first upload, and the upload fails with an error if it cannot be created.
- What if a client requests an image URL for a file that was never stored or was manually deleted? → 404 Not Found.
- What if the `photo` column contains a bare filename (legacy data from before URL serving was added)? → The system constructs the full URL from the filename on read, so existing data remains valid.

## Requirements *(mandatory)*

### Functional Requirements

**Upload (US1)**

- **FR-001**: The products endpoint MUST accept image uploads in JPEG, PNG, GIF, and WEBP formats.
- **FR-002**: Uploaded images MUST be converted to PNG format before storage.
- **FR-003**: If an uploaded image's width exceeds 150 pixels, it MUST be resized to exactly 150 pixels wide with height scaled proportionally.
- **FR-004**: If an uploaded image's width is 150 pixels or less, it MUST NOT be enlarged.
- **FR-005**: Each stored file MUST be named using the SHA-256 hash of its final PNG content, with a `.png` extension.
- **FR-006**: The directory where images are stored MUST be configurable via application configuration.
- **FR-007**: After a successful upload, the product's `photo` field in API responses MUST contain the full public URL of the image (e.g. `https://api.example.com/images/abc123.png`).
- **FR-008**: If an image with the same content hash already exists on disk, the system MUST reuse the existing file.
- **FR-009**: Files exceeding 2 MB MUST be rejected with a validation error before any processing.
- **FR-010**: Files with unsupported or unrecognizable formats MUST be rejected with a validation error.
- **FR-011**: The upload endpoint MUST require authentication; unauthenticated requests MUST receive a 401.

**Remove (US2)**

- **FR-012**: A user with Update Products permission MUST be able to clear a product's photo by sending `photo: null` via the product update endpoint.

**Serve (US3)**

- **FR-013**: The API MUST expose a public endpoint that serves stored image files by filename.
- **FR-014**: The image-serving endpoint MUST be accessible without authentication.
- **FR-015**: Requests for a filename that does not exist on disk MUST return 404 Not Found.
- **FR-016**: The base URL used to construct image URLs in product responses MUST be configurable via application configuration (to support deployments behind reverse proxies or on custom domains).

### Key Entities

- **Product**: Existing catalog entity. The `photo` column stores the image filename on disk; the `photo` field in API responses exposes the full public URL (or null).
- **Image File**: A PNG file stored in the configured directory, named by SHA-256 content hash. Not tracked in the database.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An upload round-trip completes and the product's `photo` field contains a full URL ending in `.png`.
- **SC-002**: A GET request to the URL in the `photo` field (unauthenticated) returns the image with the correct content type 100% of the time when the file exists.
- **SC-003**: Uploading the same image twice results in exactly one file on disk and both products share the same URL.
- **SC-004**: An uploaded image wider than 150 px is stored at exactly 150 px wide.
- **SC-005**: Uploading an unsupported file type returns 422 every time.
- **SC-006**: Requesting a non-existent image URL returns 404 every time.

## Assumptions

- The `photo` column on the `product` table stores the bare filename; the full URL is constructed at read time from the configurable base URL.
- Image files are never deleted by this feature — clearing a product's photo only nulls the column.
- The image storage directory is local filesystem (no CDN integration in scope).
- Images are public by design — product thumbnails contain no sensitive information. No per-image access control is required.
- Animated GIFs are accepted but only the first frame is retained after PNG conversion.
- Existing products whose `photo` column already contains a bare filename (from before URL serving was introduced) will have their URL constructed correctly on read.
