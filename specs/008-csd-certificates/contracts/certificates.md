# Contract: Digital seal certificates

Base path `/api/v1/taxpayer-certificates`. Every route requires the `TAXPAYERS` (24)
permission. Administrators bypass the check.

## List

```http
GET /api/v1/taxpayer-certificates?taxpayer=AAA010101AAA&status=0&skip=0&limit=20
```

Both filters optional. Standard `{ "items": [...], "total": N }` envelope.

## Item shape — metadata only

```json
{
  "taxpayer_certificate_id": "00001000000500003416",
  "taxpayer": "AAA010101AAA",
  "valid_from": "2024-01-01T00:00:00",
  "valid_to": "2028-01-01T00:00:00",
  "status": 0
}
```

These five fields are the entire contract. The certificate file, the private key and the
password are **never** present, on any route, under any parameter. There is no download
endpoint and no expansion that reveals them.

Validity timestamps are **Mexico City local time**, matching records written before this
interface existed. They carry no offset suffix and must not be interpreted as UTC.

## Get

```http
GET /api/v1/taxpayer-certificates/{certificate_id}   → 200 | 404
```

## Register

```http
POST /api/v1/taxpayer-certificates
Content-Type: multipart/form-data

taxpayer=AAA010101AAA
certificate=@csd.cer        (DER)
key=@csd.key                (DER, password-protected)
key_password=...
```

Returns `201` with the metadata shape. The certificate number and validity window in the
response are read from the certificate, not from the request.

| Rejection | Status | Message |
|-----------|--------|---------|
| Unreadable certificate | `422` | `Unreadable certificate — expected a DER or PEM encoded .cer` |
| Wrong password, or unreadable key | `422` | `Wrong password, or unreadable private key — expected a DER or PEM .key` |
| Key is not password protected | `422` | `Private key is not password protected — not a valid CSD key` |
| Key does not match the certificate | `422` | `Private key does not match the certificate` |
| Certificate belongs to another entity | `422` | `Certificate belongs to X, not to Y` |
| Taxpayer entity does not exist | `422` | `Taxpayer issuer not found` |
| Certificate already registered | `409` | `Certificate {number} is already registered` |
| Tax identifier not 12–13 characters | `422` | validation error |

Each reason is distinct, so an administrator can tell a typo in the password from a mismatched
pair from the wrong entity.

## Not offered

- **No amend.** A certificate is externally issued; there is nothing to correct in place.
- **No download.** No route returns stored certificate or key material.
- **No password rotation.** The password belongs to the key as issued.
