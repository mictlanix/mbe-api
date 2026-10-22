# Feature Specification: Master Data Access Privileges

**Feature Branch**: `022-master-data-privileges`
**Created**: 2026-10-22
**Status**: Draft
**Input**: Implements mictlanix/mbe-roadmap#22, the pending [API] work of the Users & permissions epic (mictlanix/mbe-roadmap#2) that can ship while the current system is still running. The scope documents are `docs/specs/12-users.md` §2 (Access Privileges) and §5 "Authorization". Eleven master data modules check only that the user is logged in: customers, employees, exchange rates, expenses, labels, payment method options, price lists, suppliers, taxpayer recipients, vehicle operators and vehicles. This feature makes each of their operations check the user's access privilege for the module, without breaking the pilot. Every branch runs the new MBE alongside the current system, and non-admin users sell with it.

## Findings that shape this spec

### How the current system gates the same data

The current system (`mictlanix/mbe`, ASP.NET MVC) **never checks a module privilege on the server** for any of the 11 modules. Every one of their controllers requires only a login (`[Authorize]`, or the `[SessionVersion]` attribute inherited from `CustomController`, which compares the session version and never reads privileges). Privileges only decide what the user sees:

- **Menu:** a link appears only if the user holds Read on its module (`Views/Shared/_Menu.cshtml:151`). Typing the URL still works.
- **Buttons:** views show or hide edit controls by right (e.g. `Views/SalesOrders/_EditorView.cshtml:147`).
- **A few actions** check a privilege explicitly. None of them is in these 11 modules.

Inside sales orders, the POS and quotes, the lookups run through the sales controllers or through login-only actions, never through a privilege on the looked-up module:

| Lookup | Served by | Gate |
|---|---|---|
| Customer search | `CustomersController.GetSuggestions` | login only |
| Customer contacts, addresses, invoice recipients | `SalesOrdersController.Contacts`, `.Addresses`, `.Recipients` (and the Quotations equivalents) | login only |
| Salesperson picker | `EmployeesController.SalesPeople` | login only |
| Currencies | `ExchangeRatesController.Currencies` | login only |
| Price list and customer discounts | applied on the server when a line is added | none (no lookup) |
| Payment options at the till | read on the server from the cash drawer's store | none (no lookup) |

The only privilege between a seller and these lookups is Read on the sales screen's own module (SalesOrders 7, POS 44, SalesQuotes 30), and that is checked through menu visibility, not by the server.

### What mbe-ui calls, and from which screens

mbe-ui reads these modules from other modules' screens. It does so through the same routes the module's own catalog uses. The calls below go to one of the 11 routers, from a screen whose route gate is a **different** privilege:

| Host screen (route gate) | Calls |
|---|---|
| POS capture and sales-order editor (`pos` / `salesOrders` Read) | `GET /customers?search=`, `GET /customers/{id}` (address and contact pickers read the customer's lists from this response), `PUT /customers/{id}` with only `addresses` or only `contacts` (links a new address or contact), `GET /employees?sales_person=true` (salesperson), `GET /taxpayer-recipients` (invoice recipient), `GET /payment-method-options?facility=` (checkout tender grid) |
| POS "new customer" dialog (button shown with `customers` Create) | `POST /customers`, `GET /price-lists`, `GET /taxpayer-recipients` |
| Cash sessions list (`pos` Read) | `GET /employees`, `GET /employees/{id}` (cashier filter) |
| Product form and products list (`products` Read) | `GET /labels` (loaded automatically), `GET /suppliers`, `GET /suppliers/{id}` |
| Pricing grid and per-product pricing (`pricing` Read) | `GET /price-lists` (loaded automatically), `GET /labels` (loaded automatically), `GET /suppliers`, `GET /suppliers/{id}` |
| Customer form and customers list (`customers` Read) | `GET /price-lists`, `GET /price-lists/{id}`, `GET /employees`, `GET /employees/{id}`, `GET /taxpayer-recipients` |
| Vehicle operator form and list (`vehicleOperators` Read) | `GET /employees`, `GET /employees/{id}` |
| User administration (`users` Read; the router requires an administrator) | `GET /employees`, `GET /employees/{id}` |

mbe-ui has no sales-quote screens yet, and no screen calls `/customers/{id}/discounts`, `/exchange-rates`, `/expenses`, `/vehicles` or `/vehicle-operators` from outside their own catalog.

### Measured in mbe_dev (2026-10-22)

mbe_dev holds 36 active users: 3 administrators and 33 non-administrators. 4 of the 33 are QA accounts with no employee (`employee = -1`). Of the 33, **21 can sell**, meaning they hold Create on SalesOrders (7) or SalesQuotes (30). The counts below include the QA accounts.

**Active non-admin users who lack Read on each module.** The first column counts all 33; the second counts only the 21 who can sell.

| Module | Lacking Read (of 33) | Sellers lacking Read (of 21) |
|---|---|---|
| Customers (2) | 8 | **0** |
| Addresses (11), already guarded | 14 | 3 |
| Contacts (12), already guarded | 16 | 5 |
| Price lists (5) | 16 | 6 |
| Taxpayer recipients (54) | 14 | 4 |
| Payment method options (84) | 24 | 12 |
| Employees (6) | 31 | 19 |
| Labels (1) | 27 | 17 |
| Exchange rates (43) | 28 | 17 |
| Suppliers (3) | 29 | 17 |
| Expenses (81) | 20 | 9 |
| Vehicle (88) | 29 | 17 |
| Vehicle operators (89) | 29 | 17 |

**Users who would lose a lookup they use today if each route checked only its own module.** Each count is users who hold the host screen's privilege but lack Read on the looked-up module.

| Lookup that would break | Users affected |
|---|---|
| Customer search or detail from a sales screen (Read on 7, 30 or 44, but not on Customers) | 1 |
| Salesperson picker in the sales-order editor (SalesOrders Read, no Employees Read) | 23 |
| Cashier filter in cash sessions (POS Read, no Employees Read) | 18 |
| Invoice-recipient picker in the sales-order editor (SalesOrders Read, no Taxpayer recipients Read) | 6 |
| Checkout tender grid (POS Read, no Payment method options Read) | 11 |
| Checkout tender grid (Customer payments Create, no Payment method options Read) | 12 |
| Price-list picker in the new-customer dialog (Customers Create, no Price lists Read) | 7 |
| Labels on the product form and list (Products Read, no Labels Read; 26 users hold Products Read) | 20 |
| Suppliers on the product form and list (Products Read, no Suppliers Read) | 22 |
| Price lists on the pricing grid (Pricing Read, no Price lists Read) | 3 |
| Labels on the pricing grid (Pricing Read, no Labels Read) | 13 |
| Salesperson on the customer form and list (Customers Read, no Employees Read) | 23 |
| Driver picker on vehicle operators (Vehicle operators Read, no Employees Read) | 2 |

**Linking a new address or contact to a customer from the sales-order editor.** The UI does this with `PUT /customers/{id}`. 4 users hold SalesOrders Update but not Customers Update. 3 of them also hold Addresses or Contacts Create, so today they can create the address or contact and link it.

Conclusion: checking only each module's own privilege would break the pilot. Sales-order editing, checkout, the product form and the pricing grid would all fail for most of the users who use them today. Every lookup must also accept the privileges of the screens that perform it.

## Clarifications

### Session 2026-10-22

- Q: Who may update a customer's address or contact list without Customers Update? → A: A user with SalesOrders Update or SalesQuotes Update, and only when the request changes nothing but those lists (FR-006, User Story 3).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Master data is closed to users without the privilege (Priority: P1)

An administrator controls, per user and per module, who may view, create, edit and delete customers, employees, exchange rates, expenses, labels, payment method options, price lists, suppliers, taxpayer recipients, vehicle operators and vehicles. A user without the right privilege is refused by the API, not only hidden from the menu. Today any logged-in user can, for example, delete a price list or edit an employee by calling the API directly.

**Why this priority**: This is the security gap #22 closes. Scope 12 §5 requires deny-by-default authorization with an administrator bypass, and these 11 modules are the last master data modules without it.

**Independent Test**: Log in as a non-admin user who holds Read but not Delete on Labels. Listing labels succeeds, and deleting one is refused with "insufficient privileges". Remove the Labels row entirely: listing is refused. Log in as an administrator with no privilege rows: every operation succeeds.

**Acceptance Scenarios**:

1. **Given** a non-admin user with Read but not Create on a module, **When** they create a record in it, **Then** the request is refused as forbidden and nothing is written.
2. **Given** a non-admin user with no privilege row for a module, **When** they call any operation of that module, **Then** it is refused as forbidden, except the lookups covered by User Story 2.
3. **Given** an administrator, **When** they call any operation of the 11 modules, **Then** it is allowed regardless of privilege rows.
4. **Given** a user with Update on Customers, **When** they add, edit or remove a customer discount, **Then** the change is allowed. A user without Update is refused.
5. **Given** a user with Update on Suppliers, **When** they add, edit or remove a supplier agreement, **Then** the change is allowed. A user without Update is refused.
6. **Given** a user without a session, **When** they call any of these routes, **Then** they are refused as unauthenticated, as today.

---

### User Story 2 - Sellers and back-office users keep every lookup they use today (Priority: P1)

A seller at the POS searches for a customer, opens the address and contact pickers, chooses a salesperson and an invoice recipient, and pays at checkout, all without holding Read on Customers, Employees, Taxpayer recipients or Payment method options. A product manager opens the product form, which loads labels and suppliers, without holding Read on Labels or Suppliers. These keep working after the change, because each lookup also accepts the privilege of the screen it is made from.

**Why this priority**: The pilot runs in every branch. Breaking sales-order editing or checkout stops sales; the measurements show 11 to 23 users would lose a lookup in each case.

**Independent Test**: Log in as a user who holds only SalesOrders Read+Update and POS Read (no rows for Customers, Employees, Taxpayer recipients or Payment method options). Search customers, fetch one, list salespeople, list taxpayer recipients and list the facility's payment method options: all succeed. Creating, editing or deleting in any of those modules is refused.

**Acceptance Scenarios**:

1. **Given** a user with Read on SalesOrders, POS or SalesQuotes and no Customers row, **When** they search customers or fetch one, **Then** the response is the same as for a user with Customers Read.
2. **Given** a user with Read on SalesOrders or SalesQuotes and no Employees row, **When** they list employees or fetch one, **Then** it succeeds.
3. **Given** a user with POS Read and no Payment method options row, **When** they list payment method options for a facility, **Then** it succeeds.
4. **Given** a user with Products Read and no Labels or Suppliers row, **When** the product form loads labels and searches suppliers, **Then** both succeed.
5. **Given** a user with only a host privilege (e.g. POS Read), **When** they create, update or delete in a looked-up module (e.g. delete a payment method option), **Then** it is refused.
6. **Given** a user with only a host privilege, **When** they call a lookup module that host does not use (e.g. POS Read only, listing suppliers), **Then** it is refused.

---

### User Story 3 - Sellers can still attach a new ship-to address or contact to the customer (Priority: P2)

From the sales-order editor, a seller creates a new address or contact for the customer and links it, so the order can ship there. mbe-ui links it by updating the customer with the full list of address or contact ids.

**Why this priority**: 3 sellers can do this today but lack Customers Update. A strict Customers Update check would take it away. They could still pick existing addresses and contacts, so it is less critical than the lookups.

**Independent Test**: As a user with SalesOrders Update and Addresses Create but no Customers row, create an address and update the customer with only its address list extended. The link succeeds. Sending any other customer field in the same request is refused.

**Acceptance Scenarios**:

1. **Given** a user who lacks Customers Update but holds SalesOrders Update or SalesQuotes Update, **When** they update a customer with only its address list or only its contact list, **Then** the update is applied.
2. **Given** the same user, **When** the update changes any other customer field, **Then** it is refused as forbidden and nothing is written.

---

### Edge Cases

- A privilege row with value 0 counts as a missing row: no access.
- A user who holds Create but not Read (e.g. Customers = 1) can create but not list. The rule is per right, as in the bitmask.
- `GET /price-lists/{id}/delete/preview` only exists to support a delete, but it reads: it requires Price lists Read, not Delete, and accepts no host privilege.
- Vehicle operator license photos: uploading and removing a photo edit the operator, so they require Vehicle operators Update, the way a product image upload requires Products Update. Fetching the photo requires Vehicle operators Read and accepts no host privilege.
- A host privilege opens only the list and get-by-id routes of a looked-up module, never its sub-resources (customer discounts, supplier agreements, license photos, delete preview).
- A link-only body with an empty list (`{"addresses": []}`) clears that list, and it is accepted under the same rule as any other link-only body. mbe-ui always resends the full list, so the API cannot tell an append from a replacement.
- The users and user-profiles routers stay administrator-only, so their employee lookups need nothing new.
- A token issued before a privilege change keeps its session. Privileges are read on every request, so a revoked privilege takes effect on the next call, as today with products.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Every operation on the 11 modules MUST require a privilege on that module's SystemObject: Customers 2, Employees 6, Exchange rates 43, Expenses 81, Labels 1, Payment method options 84, Price lists 5, Suppliers 3, Taxpayer recipients 54, Vehicle operators 89, Vehicle 88.
- **FR-002**: The right MUST follow the operation: Read to list or fetch, Create to create, Update to edit, Delete to delete. The exceptions are the license-photo upload and removal (Update, see Edge Cases) and the customer link exception in FR-006.
- **FR-003**: Nested routes MUST use their parent's SystemObject: customer discounts use Customers, supplier agreements use Suppliers, license photos use Vehicle operators. Adding, editing or removing a discount or an agreement is an Update of the parent; listing them is a Read of the parent.
- **FR-004**: Administrators MUST bypass every check. A missing privilege row, or a row without the required bit, MUST be refused as forbidden ("Insufficient privileges"), the same response products give today.
- **FR-005**: The list route and the get-by-id route of these modules MUST also accept Read on the host modules whose screens use them, and only those:

  | Module (own Read) | Also accepted: Read on |
  |---|---|
  | Customers | SalesOrders, SalesQuotes, POS |
  | Employees | SalesOrders, SalesQuotes, POS, Customers, Vehicle operators |
  | Taxpayer recipients | SalesOrders, Customers |
  | Price lists | Customers, Pricing |
  | Payment method options | SalesOrders, POS, Customer payments |
  | Labels | Products, Pricing |
  | Suppliers | Products, Pricing |
  | Exchange rates, Expenses, Vehicle, Vehicle operators | none |

- **FR-006**: `PUT /customers/{id}` MUST require Customers Update, except that a request that changes only the customer's address list and/or contact list MUST also be accepted from a user holding SalesOrders Update or SalesQuotes Update. The request can add or remove links, because mbe-ui resends the full list.
- **FR-007**: A host privilege MUST NOT open any create, update or delete operation, any sub-resource (FR-003), or any module not listed for it in FR-005.
- **FR-008**: The responses of the guarded routes MUST NOT change for a user who is allowed. Only the refusals are new.
- **FR-009**: Every route of the 11 modules MUST be covered by automated tests that show (a) the route requires exactly the privileges in the contract table, and (b) a non-admin user with no privilege rows is refused. Each kind of rule MUST also be tested end to end with a user who is allowed: own privilege, an alternative read privilege, nested route, and the link exception. Each FR-005 alternative MUST be tested to allow the read and to refuse a write.

### Key Entities

- **Access privilege**: one row per user and module (SystemObject), holding a Create/Read/Update/Delete bitmask (scope 12 §2). No change to its shape or data.
- **Host module**: a module whose screen looks up another module's records (e.g. SalesOrders looks up Customers). It exists only as the FR-005 table; it is not stored.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 0 routes among the 11 modules are reachable with a login alone. Every route requires an administrator or a matching privilege.
- **SC-002**: Re-running the mbe_dev measurement after the change shows 0 active users losing a lookup listed in "Measured in mbe_dev" (today's counts: 1 to 23 per lookup under a strict check).
- **SC-003**: Every mbe-ui flow listed in "What mbe-ui calls" completes for a user holding only the host screen's privileges. This is checked once on the test deployment with a seller account and a product manager account. The check records the mbe-ui commit deployed at https://test.mbe.mictlanix.com.
- **SC-004**: An administrator's responses on all 11 modules are identical before and after the change.

## Assumptions

- mbe_dev is the deployment database that the counts were measured on. It is a copy of the live data, refreshed through 2026-10-21. The counts will drift as administrators edit privileges.
- "Users who can sell" means active non-admin users with Create on SalesOrders or SalesQuotes. Sellers using the new MBE could not be identified in mbe_dev: only 16 orders carry an origin, all created by `admin`. The spec therefore protects everyone the privileges allow to sell, not only the pilot users.
- Host privileges are taken from mbe-ui's route gates (`app_router.dart`), which match the menu gates the current system uses.
- SalesQuotes is listed as a host even though mbe-ui has no quote screens yet. The current system's quote editor uses the same customer and salesperson lookups, so the planned quote screens will need them.
- Payment method options at checkout are listed by facility. Accepting SalesOrders, POS and Customer payments Read covers both the register and back-office payment flows.
- The lookups are served by the existing routes accepting more privileges (FR-005), not by new lookup routes. UI changes are out of scope, and mbe-ui already calls the existing routes.
- No data changes: no privilege rows are added or migrated.

## Out of Scope

- Rehashing passwords to bcrypt (mictlanix/mbe-roadmap#23). It waits until the current system is shut down, because the current system validates SHA-1 on the same user table.
- `user_settings_mode` (#24, closed: Casa Maestra runs Managed).
- The users and user-profiles routers, which already require an administrator.
- UI changes, including mbe-ui hiding buttons by privilege.
- Routers already guarded (products, addresses, contacts, sales, payments and the rest) and `sat_catalogs`, which is shared reference data.
- New lookup routes. FR-005 serves the lookups from the existing routes.
