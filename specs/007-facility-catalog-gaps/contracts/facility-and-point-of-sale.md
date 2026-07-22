# Contract: Facility address and the point-of-sale pairing rule

## Facility address — breaking change

`GET /api/v1/facilities` and `GET /api/v1/facilities/{id}`:

```diff
- "address": 1
+ "address": {
+   "address_id": 1,
+   "street": "Reforma", "exterior_number": "100", "interior_number": null,
+   "postal_code": "06000", "neighborhood": "Centro", "borough": "Cuauhtemoc",
+   "state": "CDMX", "city": null, "country": "MEX",
+   "nickname": null, "type": 0, "locality": null,
+   "url_address": null, "comment": null, "status": 0
+ }
```

Unchanged everywhere else. A facility embedded in a warehouse, point of sale or cash drawer
keeps `"address": 1`, and keeps `"location"` as a bare postal code:

```json
{ "warehouse_id": 1, "facility": { "facility_id": 1, "address": 1, "location": "06000", "...": "..." } }
```

No client rendering a facility list needs a second request for addresses.

## Point of sale — facility and warehouse must agree

```http
POST /api/v1/points-of-sale     { "facility": 2, "code": "POS1", "name": "Till", "warehouse": 7 }
PUT  /api/v1/points-of-sale/1   { "facility": 3 }
```

| Condition | Status | Body |
|-----------|--------|------|
| Warehouse belongs to another facility | `422` | `Warehouse 7 does not belong to facility 2` |
| Warehouse does not exist | `404` | `Warehouse not found` |
| Amendment touches neither field | — | not validated; unrelated edits never fail |
| Warehouse belongs to the facility | `200` / `201` | the point of sale |

On `PUT`, validation is against the **resulting** pair: changing only `facility` is checked
against the warehouse already stored, so a point of sale cannot be stranded by a partial
edit.

Response shape is unchanged. `facility` and `warehouse` continue to arrive as embedded
summary objects.
