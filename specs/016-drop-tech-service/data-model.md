# Data Model: Retire Technical Service and Vehicle Service Orders

This feature adds no entity and reshapes one. Everything else it does is removal, listed here so the
scope is checkable against the code rather than remembered.

## Removed — mapped tables

All seven live in `app/models/technical_service.py`, which is deleted whole. The first five belong
to technical service; the last two to vehicle service orders, and they are in scope because the same
module maps them and the same migration dropped them.

| Model class | Table | Internal FK |
|---|---|---|
| `TechServiceReceipt` | `tech_service_receipt` | — |
| `TechServiceReceiptComponent` | `tech_service_receipt_component` | → `tech_service_receipt` |
| `TechServiceReport` | `tech_service_report` | — |
| `TechServiceRequest` | `tech_service_request` | — |
| `TechServiceRequestComponent` | `tech_service_request_component` | → `tech_service_request` |
| `VehicleServiceOrder` | `vehicle_service_order` | — |
| `ServiceOrderDetail` | `service_order_detail` | → `vehicle_service_order` |

No table outside the set references any of them — verified through `information_schema` before the
monolith's drop, and the drop succeeded with no foreign key blocking it. So removal is
self-contained: no relationship elsewhere loses a target, and `Base.metadata` simply has seven fewer
tables for the integration schema to create.

## Reshaped — the permission matrix

`SystemObject` is the catalog of legacy menu entries and its **cardinality is the width of the
permission matrix**: an account carries one `access_privilege` row per member.

| | Before | After |
|---|---|---|
| Members | 107 | **103** |
| Rows written per account | 107 | **103** |
| Highest identifier | 113 | 113 (unchanged) |
| Absent identifiers | 31, 70, 76–78, 104, 105 | those **plus 58, 64, 65, 90** |

Removed: `TECHNICAL_SERVICE_REPORTS = 58`, `TECHNICAL_SERVICE_REQUESTS = 64`,
`TECHNICAL_SERVICE_RECEIPTS = 65`, `VEHICLE_SERVICE_ORDERS = 90`.

Untouched, and asserted so: `VEHICLE = 88`, `VEHICLE_OPERATORS = 89`, `FOR_DELIVER = 91` — the
neighbours on either side of 90, which are live.

### The two shapes this change flows through

The sparse/dense split established by spec 014 is what makes the removal safe without any new code:

- **A profile is sparse** — `user_profile_privilege` holds a row only for what it grants. A profile
  naming a removed object is refused at write time by `ProfilePrivilegeUpdate.validate_system_object`,
  which checks against the enum; one already stored would be ignored at apply time, since
  `_write_privileges_from` only writes for objects the enum defines. Measured: zero stored profiles
  grant any of the four, and there is one profile in total.
- **A user is dense** — one row per member, and `_write_privileges_from` removes any row whose
  object the enum does not define. That existing loop is the entire cleanup path (research R2).

### State transitions

None. No entity in this feature has a lifecycle; the matrix narrows once, at deploy, and every
subsequent account provision or profile apply simply uses the narrower catalog.

## Removed — documentation entities

- Seven `### <table>` sections in `docs/data-dictionary.md`, plus the section-11 note marking them
  as pending removal and the per-table markers under each.
- Seven `CREATE TABLE` definitions in `docs/mbe_schema.sql` (research R1).
- Six waivers in `tests/unit/test_data_dictionary.py` naming
  `tech_service_request_component` columns — after which that check carries **zero** column waivers.

The nine sectionless legacy tables waived in the same file (`abc_classification`, `temp_referencias`
and the rest) are **not** in scope and their waivers stay.
