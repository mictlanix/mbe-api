# Quickstart: validating Master Data Access Privileges

The required privilege for every route is listed in [contracts/route-privileges.md](contracts/route-privileges.md). The design decisions are in [research.md](research.md).

## Automated gate

```bash
uv run ruff check app/ migrations/ tests/
uv run pytest
```

Expected: zero ruff violations and a green suite.

| Requirement | Covered by |
|---|---|
| FR-001–FR-003, FR-005, FR-007, SC-001 | `tests/api/test_master_data_privileges.py`, route-table test: every route on the 11 routers carries exactly the grants in the contract, and no route is missing or extra (research R5) |
| FR-004 | `tests/unit/test_deps_privileges.py`: administrator bypass, missing row, mask 0, wrong bit, the right bit on any one grant |
| FR-005, FR-007 (behavior) | `tests/api/test_master_data_privileges.py`, end to end with a non-admin user: a lookup allowed through a host, a host refused on a write, a host refused on a module it doesn't host |
| FR-006, User Story 3 | `tests/api/test_master_data_privileges.py`: link-only body with SalesOrders Update is applied; the same user with a `name` in the body is refused; Customers Update allows any body; a `null` field does not count as a change |
| FR-008, SC-004 | the existing per-router suites, which run as administrator and pass unchanged |

## Measurement re-run on mbe_dev (SC-002)

Read only. Use the connection from the `mbe-dev` CLI memory (`MYSQL_PWD`, `--skip-ssl`, `SET SESSION TRANSACTION READ ONLY`). For each FR-005 lookup, count the active non-admin users who hold a calling screen's privilege but none of the grants the lookup accepts. For example, the salesperson picker:

```sql
SELECT COUNT(*) FROM user u
WHERE u.status = 0 AND u.administrator = 0
  AND EXISTS (SELECT 1 FROM access_privilege a
              WHERE a.user = u.user_id AND a.object = 7 AND a.privileges & 2)
  AND NOT EXISTS (SELECT 1 FROM access_privilege a
                  WHERE a.user = u.user_id AND a.object IN (6, 7, 30, 44, 2, 89)
                    AND a.privileges & 2);
```

Expected: 0 for every row of the spec's "Users who would lose a lookup" table. The salesperson picker counted 23 under a strict check.

## Manual checks on the test deployment (SC-003)

Deploy the branch to `/var/www/mbe-api-test` (https://test.api.mbe.mictlanix.com). The mbe-ui test build is at https://test.mbe.mictlanix.com. No migration is needed.

1. **Seller.** Sign in as an mbe_dev user with SalesOrders and POS but no Employees, Payment method options or Taxpayer recipients Read (e.g. `franciscosalazar`; check the rows first).
   - In the POS, search for and select a customer.
   - Open the address and contact pickers.
   - In the order header, pick a salesperson and an invoice recipient.
   - Reach the checkout tender grid.

   Every step loads. Then call `DELETE /payment-method-options/{id}` with that user's token: 403.
2. **Link exception.** As a user with SalesOrders Update but no Customers Update (`cristian`, `margaritagodinez` or `carmengodinez`), add a new address from the order header's address picker. The address appears on the customer. `PUT /customers/{id}` with `{"name": "x"}` from the same token: 403. Remove the test address afterwards.
3. **Product manager.** As a user with Products Read but no Labels or Suppliers Read, open a product. The labels and the supplier picker load.
4. **Strict module.** As a user with no Vehicle row, call `GET /vehicles`: 403. As an administrator: 200 with the same body as before the deploy.
