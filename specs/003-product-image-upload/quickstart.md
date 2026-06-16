# Quickstart: Product Image Upload

## Prerequisites

1. API running locally (`uv run uvicorn app.main:app --reload`)
2. `IMAGES_DIR` set to a writable directory (or default `images/` directory created at repo root)
3. `IMAGES_BASE_URL` set to `http://localhost:8000` for local dev (or left empty for relative URLs)
4. A valid JWT for a user with PRODUCTS UPDATE privilege
5. A test image file (any JPEG, PNG, GIF, or WEBP)

## Scenario 1: Upload an image and verify it is stored

```bash
# Upload a JPEG for product #1
curl -X POST http://localhost:8000/api/v1/products/1/image \
  -H "Authorization: Bearer <token>" \
  -F "file=@/path/to/image.jpg"

# Expected: 200 OK, body contains "photo": "<sha256>.png"
# Verify: ls images/ shows the new .png file
```

**Expected outcome**: `photo` field in the response contains a full URL, e.g.
`http://localhost:8000/images/<sha256>.png`. The file exists on disk at `images/<hash>.png`.

## Scenario 2: Upload the same image twice — no duplicate file

```bash
# First upload
curl -X POST http://localhost:8000/api/v1/products/1/image \
  -H "Authorization: Bearer <token>" \
  -F "file=@/path/to/image.jpg"

# Note the filename from the response, then:
COUNT_BEFORE=$(ls images/ | wc -l)

# Second upload (same image, different product)
curl -X POST http://localhost:8000/api/v1/products/2/image \
  -H "Authorization: Bearer <token>" \
  -F "file=@/path/to/image.jpg"

COUNT_AFTER=$(ls images/ | wc -l)
echo "Files before: $COUNT_BEFORE, after: $COUNT_AFTER"
# Expected: same count — no new file created
```

## Scenario 3: Uploaded image is resized to ≤150 px wide

```bash
# Upload an image wider than 150 px
curl -X POST http://localhost:8000/api/v1/products/1/image \
  -H "Authorization: Bearer <token>" \
  -F "file=@/path/to/wide-image.png"

# Inspect stored file dimensions (requires imagemagick or python)
python3 -c "
from PIL import Image
img = Image.open('images/<hash>.png')
print(img.size)  # (width, height) — width must be 150
"
```

## Scenario 4: Reject oversized file (>2 MB)

```bash
curl -X POST http://localhost:8000/api/v1/products/1/image \
  -H "Authorization: Bearer <token>" \
  -F "file=@/path/to/large-file.jpg"  # >2 MB

# Expected: 422 Unprocessable Entity
```

## Scenario 5: Reject unsupported format

```bash
curl -X POST http://localhost:8000/api/v1/products/1/image \
  -H "Authorization: Bearer <token>" \
  -F "file=@/path/to/document.pdf"

# Expected: 422 Unprocessable Entity
```

## Scenario 6: Clear a product's image

```bash
curl -X PUT http://localhost:8000/api/v1/products/1 \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"photo": null}'

# Expected: 200 OK, "photo": null in response
# File on disk is NOT deleted
```

## Scenario 7: Unauthenticated upload is rejected

```bash
curl -X POST http://localhost:8000/api/v1/products/1/image \
  -F "file=@/path/to/image.jpg"

# Expected: 401 Unauthorized
```

## Scenario 8: Fetch an image without authentication (US3)

```bash
# After uploading an image to product #1, take the URL from the photo field:
PHOTO_URL=$(curl -s -X POST http://localhost:8000/api/v1/products/1/image \
  -H "Authorization: Bearer <token>" \
  -F "file=@/path/to/image.jpg" | python3 -c "import sys,json; print(json.load(sys.stdin)['photo'])")

# Fetch the image without any auth header:
curl -I "$PHOTO_URL"
# Expected: HTTP/1.1 200 OK, Content-Type: image/png
```

**Expected outcome**: Image is returned as `image/png` with status 200. No `Authorization` header
is needed.

## Scenario 9: Request a non-existent image returns 404

```bash
curl -I http://localhost:8000/images/doesnotexist.png
# Expected: HTTP/1.1 404 Not Found
```

## Scenario 10: Product with no photo returns null (not a broken URL)

```bash
# After clearing a product's photo (see Scenario 6):
curl -s http://localhost:8000/api/v1/products/1 \
  -H "Authorization: Bearer <token>" | python3 -c "import sys,json; print(json.load(sys.stdin)['photo'])"
# Expected: null
```

## Running the Unit Tests

```bash
uv run pytest tests/api/test_product_image.py -v
```

See [contracts/upload-image.md](contracts/upload-image.md) and [contracts/serve-image.md](contracts/serve-image.md) for full request/response specs.
