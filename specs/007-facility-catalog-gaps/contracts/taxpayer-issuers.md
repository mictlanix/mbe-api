# Contract: Taxpayer issuers

Base path `/api/v1/taxpayer-issuers`. Every route requires the `TAXPAYERS` (24) permission —
`READ` on the read routes, `CREATE` / `UPDATE` / `DELETE` on the others. Administrators bypass
the check.

## List

```http
GET /api/v1/taxpayer-issuers?search=acme&skip=0&limit=20
```

- `search` matches the tax identifier or the name, case-insensitive substring.
- Returns the standard `{ "items": [...], "total": N }` envelope.

## Item shape

```json
{
  "taxpayer_issuer_id": "AAA010101AAA",
  "name": "Acme SA de CV",
  "regime": { "id": "601", "description": "General de Ley Personas Morales" },
  "postal_code": { "id": "06000", "description": null },
  "provider": 0,
  "comment": null
}
```

- `regime` and `postal_code` are expanded catalog objects, or `null` when not on file.
- `provider`: `0` none, `1` Diverza, `2` FiscoClic, `3` Servisim, `4` ProFact.

## Create / update / delete

```http
POST   /api/v1/taxpayer-issuers          → 201
GET    /api/v1/taxpayer-issuers/{rfc}    → 200 | 404
PUT    /api/v1/taxpayer-issuers/{rfc}    → 200 | 404
DELETE /api/v1/taxpayer-issuers/{rfc}    → 204 | 404 | 409
```

- `taxpayer_issuer_id` is 12–13 characters; shorter is rejected as invalid.
- `provider` outside the known set is rejected as invalid.
- On create, `provider` defaults to none when omitted.
- `PUT` is partial: omitted fields are left unchanged.
- `DELETE` returns `409` while facilities, certificates, fiscal batches or fiscal documents
  still reference the entity, naming what blocks it. This feature shipped a hand-written check
  over those four tables; it was later replaced by the shared referential guard, so the exact
  response shape is now defined by
  [feature 006's contract](../../006-constraint-violation-handling/contracts/conflict-responses.md).

## Deployment note

`TAXPAYERS` (24) governed no endpoint before this feature, so no permission row grants it.
Until granted, only administrators can reach any of these routes.
