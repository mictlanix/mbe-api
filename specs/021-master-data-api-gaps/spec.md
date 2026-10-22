# Feature Specification: Master Data API Gaps

**Feature Branch**: `021-master-data-api-gaps`
**Created**: 2026-10-21
**Status**: Draft
**Input**: Closes all of the pending [API] work in the Master data epic (mictlanix/mbe-roadmap#1): mictlanix/mbe-roadmap#13, mictlanix/mbe-roadmap#15, mictlanix/mbe-roadmap#17, mictlanix/mbe-roadmap#18 and mictlanix/mbe-roadmap#20. The scope documents are `docs/specs/01-master-data.md` §1, §3, §6 and §16, and `docs/specs/12-users.md` §1. The request named "§12.1", but that heading does not exist; "User Management" is §1. The work covers five items: supplier sub-panels, customer discounts, incidence logging for products and users, barcode search in the product list, and a driver license photo for vehicle operators.

## Clarifications

### Session 2026-10-21

- Q: What shape do the supplier sub-panels take? → A: The customer pattern. Addresses, contacts and bank accounts are id lists on the supplier's create and update, and a new standalone `/bank-accounts` resource holds the bank account records. Agreements are nested under the supplier at `/suppliers/{id}/agreements` (FR-002, FR-004, FR-005).
- Q: Where are driver license photos stored, and how are they served? → A: The proof-of-delivery pattern. They go to private storage, are returned only to authenticated users, and are kept up to 1024 px wide. Replacing or deleting a photo deletes its file. Validation stays the same as for product images (FR-023 to FR-026).
- Q: How is a user-account incidence keyed? → A: The instance is the affected user's employee id. The content starts with the user id, followed by the action (FR-015).
- Q: No pricing code reads customer discounts today. Should this feature apply them? → A: No. In this feature they can only be maintained. Applying them to sales order and quote lines is follow-up work (FR-013).
- Q: Scope 12 §1 says that "all edit and delete operations" write an incidence. Does that include applying a profile and the administrator's recovery token? → A: Yes. Applying a profile rewrites a user's privileges, and issuing a recovery token lets an administrator set that user's password, so both write a UserSettings incidence (FR-015). Product merge does not write a product incidence (Out of Scope).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Manage a supplier's addresses, contacts, bank accounts and agreements (Priority: P1)

A purchasing user opens a supplier and sees its addresses, contacts, bank accounts and commercial agreements in one response. The user can link or unlink addresses, contacts and bank accounts. The user can also add, edit and remove agreements, each of which runs from a start date to an end date.

**Why this priority**: The supplier record cannot be maintained without these sub-panels. mbe_dev already holds 119 address links, 154 contact links, 2 bank account links and 18 agreements that no client can see today (scope 01 §6, mictlanix/mbe-roadmap#13).

**Independent Test**: Fetch a supplier that already has links in mbe_dev; all four lists appear. Replace its contacts with a different set, add an agreement, then fetch again and check the result.

**Acceptance Scenarios**:

1. **Given** a supplier with linked addresses, contacts, bank accounts and agreements, **When** the supplier detail is fetched, **Then** the response includes all four lists.
2. **Given** existing address and contact records, **When** a supplier is created or updated with lists of their ids, **Then** the supplier is linked to exactly those records. This is the same replace-all behaviour customers use: an omitted list leaves the links alone, and an empty list removes them all.
3. **Given** a new bank account (bank name, account number, and optionally a reference, routing number and comment), **When** it is created and its id is sent in the supplier's bank account list, **Then** the supplier detail shows that account.
4. **Given** a supplier, **When** an agreement is added with a start date, an end date and an optional comment, **Then** it appears in the supplier's agreements. It can then be edited and removed.
5. **Given** an agreement whose end date is before its start date, **When** it is submitted, **Then** it is rejected with a validation error.
6. **Given** an id in a link list that does not exist, **When** the supplier is saved, **Then** the request fails and no links change.

---

### User Story 2 - Keep product-specific discounts for a customer (Priority: P1)

A sales user opens a customer and maintains its discount overrides. Each override is a discount rate for one product. The user can list the overrides, add one, change its rate and remove it. A customer can have at most one override per product.

**Why this priority**: Customer discounts are a sub-panel named in scope 01 §3 that no client can maintain today (mictlanix/mbe-roadmap#15).

**Independent Test**: Add a discount for a product to a customer, list the customer's discounts, change the rate, try to add a second override for the same product (rejected), then remove the override.

**Acceptance Scenarios**:

1. **Given** a customer, **When** a discount is added for a product with a rate between 0 and 1, **Then** it is stored and appears in the customer's discount list together with the product's code and name.
2. **Given** a customer that already has a discount for a product, **When** a second discount is added for the same product, **Then** it is rejected as a conflict and the existing override is unchanged.
3. **Given** an existing override, **When** its rate is updated, **Then** the new rate is stored. The product on an override cannot be changed; the user removes the override and adds a new one instead.
4. **Given** an existing override, **When** it is removed, **Then** it no longer appears in the list.
5. **Given** a rate below 0 or above 1, or a product that does not exist, **When** it is submitted, **Then** it is rejected with a validation error.
6. **Given** an override id that belongs to a different customer, **When** it is updated or removed through this customer, **Then** the request fails as not found.

---

### User Story 3 - Audit trail for product and user changes (Priority: P2)

An administrator reviewing the incidence log can see who created, edited or deleted a product, and who edited, deleted, applied a profile to, or issued a recovery token for a user account, and when.

**Why this priority**: Scope 01 §1 and scope 12 §1 both require these records, and two TODOs in the user service mark the gap (mictlanix/mbe-roadmap#17). It does not block daily work, so it ranks below the sub-panels.

**Independent Test**: Create, edit and delete a product. Edit and delete a user, apply a profile to one, and issue a recovery token for one. Check that each action left exactly one incidence row with the right source, instance, author and time.

**Acceptance Scenarios**:

1. **Given** a product is created, edited or deleted, **When** the change is saved, **Then** one incidence with source Product is written. Its instance is the product's id, its author is the acting employee, and it records which action was taken.
2. **Given** a product is deleted, **When** the incidence is read later, **Then** it still identifies the product by code and name, even though the product row no longer exists.
3. **Given** a user account is edited, deleted, has a profile applied, or has a recovery token issued by an administrator, **When** the action completes, **Then** one incidence with source UserSettings is written. It names the affected user and records the acting employee.
4. **Given** a change that fails or is rolled back, **When** the request ends, **Then** no incidence is left behind. The incidence is saved in the same transaction as the change.

---

### User Story 4 - Find a product by scanning or typing its barcode (Priority: P2)

A user searching the product list types or scans a 13-digit barcode and finds the product that carries it.

**Why this priority**: This removes a documented limitation in the product catalog list (mictlanix/mbe-roadmap#18). Its reach is small for now: only 43 products have a barcode, and filling the rest belongs to the master data migration (mictlanix/mbe-roadmap#123).

**Independent Test**: Search the product list for the barcode of one of the 43 products that have one. That product appears. A partial barcode does not match on barcode.

**Acceptance Scenarios**:

1. **Given** a product whose barcode is `7501234567890`, **When** the product list is searched for `7501234567890`, **Then** that product is in the results.
2. **Given** the same product, **When** the list is searched for `750123`, **Then** it is not matched by its barcode. It may still match on code, name, model, SKU or brand as before.
3. **Given** any search term, **When** the list is searched, **Then** every product that matched before this change still matches. The barcode match only adds results.

---

### User Story 5 - Keep a photo of each operator's driver license (Priority: P3)

A logistics user uploads a photo of a vehicle operator's driver license, replaces it when the license is renewed, views it, and deletes it.

**Why this priority**: This supports document control for drivers (mictlanix/mbe-roadmap#20). It has the smallest reach of the five items, since mbe_dev has 14 operators.

**Independent Test**: Upload an image for an operator, fetch it back, replace it with a different image, then delete it. Check that fetching after the delete returns not found.

**Acceptance Scenarios**:

1. **Given** an operator without a license photo, **When** an image is uploaded, **Then** it is stored and the operator detail shows that the operator has a license photo.
2. **Given** an operator with a license photo, **When** a new image is uploaded, **Then** the new image replaces the old one and the old image is removed from storage.
3. **Given** an operator with a license photo, **When** the photo is requested by an authenticated user, **Then** the image is returned. **When** it is requested without authentication, **Then** it is refused.
4. **Given** an operator with a license photo, **When** the photo is deleted, **Then** the operator no longer has one, the image is removed from storage, and fetching it returns not found.
5. **Given** a file that is not a recognisable image, or one larger than 2 MB, **When** it is uploaded, **Then** it is rejected with a validation error and any existing photo is kept.

---

### Edge Cases

- A supplier that has links or agreements cannot be deleted. Deleting it returns a conflict, which is the same behaviour customers have with linked addresses. Unlinking first, by sending empty lists and removing the agreements, makes the delete possible.
- An address, contact or bank account linked to a supplier cannot be deleted while the link exists. Deleting it returns a conflict.
- An agreement may start and end on the same day. Agreements of one supplier may overlap, because no rule forbids it in legacy or in the scope document.
- Products are hard-deleted. The product's delete incidence is written before the row is removed, in the same transaction.
- A user account is keyed by a text id, while an incidence instance is a number. User incidences use the user's employee id as the instance and name the user id in the content (Assumptions).
- An operator is deleted while it has a license photo: the photo is removed from storage along with the operator.
- A stored license photo has gone missing from storage: fetching it returns not found and does not fail with a server error.
- A product is merged away while it has customer discounts: the existing merge behaviour discards those discounts (spec 010) and does not change.
- An empty or all-whitespace search term behaves as it does today. A barcode match only applies to a non-empty term.

## Requirements *(mandatory)*

### Functional Requirements

**Supplier sub-panels** (scope 01 §6)

- **FR-001**: The supplier detail MUST include the supplier's addresses, contacts, bank accounts and agreements, each with its full fields. The supplier list MUST stay as it is, without these lists, in the same way customers have them on the detail only.
- **FR-002**: Supplier create and update MUST accept optional lists of address ids, contact ids and bank account ids. Each list replaces that link set, an omitted list leaves it unchanged, and an empty list clears it. This mirrors the customer address and contact behaviour.
- **FR-003**: Any unknown id in a link list MUST fail the whole request, and no links change.
- **FR-004**: Users MUST be able to create, read, update, list and delete bank account records (bank name, account number, reference, routing number, comment), in the same way address and contact records are managed today. A bank account that is still linked to a supplier MUST NOT be deletable.
- **FR-005**: Users MUST be able to list, add, update and remove a supplier's agreements. Each agreement has a start date, an end date and an optional comment of up to 500 characters.
- **FR-006**: An agreement's end date MUST be on or after its start date.
- **FR-007**: An agreement addressed through a supplier it does not belong to MUST be treated as not found.

**Customer discounts** (scope 01 §3)

- **FR-008**: Users MUST be able to list, add, update and remove a customer's product discount overrides. Each listed override MUST show the product's id, code and name.
- **FR-009**: A customer MUST have at most one override per product. A duplicate MUST be rejected as a conflict.
- **FR-010**: A discount rate MUST be at least 0 and at most 1. This is the fraction convention used everywhere else, so 0.15 means 15%.
- **FR-011**: Only the rate of an existing override is editable. The product is fixed once the override is created.
- **FR-012**: An override addressed through a customer it does not belong to MUST be treated as not found.
- **FR-013**: How prices are computed MUST NOT change. Customer discounts are stored and maintained only. Nothing applies them to sales order or quote lines today, and this feature does not start applying them.

**Incidence logging** (scope 01 §1, scope 12 §1)

- **FR-014**: Creating, editing and deleting a product MUST each write one incidence with source Product. The incidence records the product id, the acting employee and a description of the action that identifies the product by code.
- **FR-015**: Editing a user account, deleting one, applying a profile to one, and an administrator issuing a recovery token for one MUST each write one incidence with source UserSettings. The incidence's instance is the affected user's employee id, its author is the acting employee, and its content starts with the affected user id, followed by the action. This resolves both TODOs in the user service.
- **FR-016**: Each incidence MUST be saved in the same transaction as the change it describes, so a failed change leaves no incidence.
- **FR-017**: Incidences MUST be written through the existing incidence service, as customer payments and facilities already do.

**Product barcode search** (scope 01 §1)

- **FR-018**: The product list search MUST also return products whose barcode equals the search term exactly. A partial barcode MUST NOT match on barcode.
- **FR-019**: All existing matches (code, name, model, SKU, brand) MUST be unchanged. Any other listing that shares the same search, such as the label and missing-price views, gains the barcode match too.
- **FR-020**: The POS product lookup MUST NOT change.
- **FR-021**: Scope 01 §1 MUST be updated so that it lists barcode as an exact-match search field. The text "not barcode" is removed.

**Vehicle operator license photo** (scope 01 §16)

- **FR-022**: Users MUST be able to upload a license photo for a vehicle operator. An upload when a photo already exists replaces it.
- **FR-023**: Uploads MUST be validated in the same way product images are. The file must be a recognisable image no larger than 2 MB, and invalid uploads are rejected with a validation error and leave any existing photo untouched.
- **FR-024**: The license photo MUST be kept in private storage and served only to authenticated users through the API, in the same way proof-of-delivery images are. It MUST NOT be reachable through the public image path that product photos use, because a driver license is personal data.
- **FR-025**: The photo MUST keep enough resolution for the license text to be read. Product images are reduced to 150 px wide, which is too small for this; the photo is bounded the way proof-of-delivery photos are, at 1024 px wide.
- **FR-026**: Users MUST be able to delete the photo. Deleting or replacing the photo, or deleting the operator, MUST remove the previous file from storage.
- **FR-027**: The operator detail and list MUST indicate whether the operator has a license photo.
- **FR-028**: The expired-license check on delivery itineraries MUST stay advisory (FR-035 of the itinerary spec) and unchanged.
- **FR-029**: Scope 01 §16 MUST be updated to describe the license photo.
- **FR-030**: The new storage reference on the operator MUST be added through a numbered SQL migration with a rollback script, under `migrations/` (spec 009).

**Cross-cutting**

- **FR-031**: Every new endpoint MUST require an authenticated user. Each one MUST use the same access rule as the router it belongs to. Customers, suppliers and vehicle operators are not privilege-gated today, and gating them belongs to the Users & permissions epic. The new bank account endpoints follow the address and contact endpoints.

### Key Entities

- **Supplier**: An existing entity. Its detail gains four lists: addresses, contacts, bank accounts and agreements.
- **Bank Account**: An existing shared record with a bank name, account number, reference, routing number and comment. It is linked to suppliers in the same way addresses and contacts are.
- **Supplier Agreement**: An existing entity. It is a date-ranged commercial agreement (start, end, comment) owned by one supplier.
- **Customer Discount**: An existing entity. It holds one customer's discount rate (0–1) for one product, and the pair is unique; mbe_dev already has a unique index on it. mbe_dev has no rows yet.
- **Incidence**: An existing audit record with a source, instance, author, time, content and comment. This feature adds new writers for sources Product and UserSettings.
- **Vehicle Operator**: An existing entity. It gains an optional reference to a privately stored license photo.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For every one of the 265 suppliers in mbe_dev, the supplier detail shows exactly the addresses, contacts, bank accounts and agreements stored for it: 119, 154, 2 and 18 rows in total.
- **SC-002**: A user can add, change and remove a customer's product discount, and a second override for the same customer and product is refused 100% of the time.
- **SC-003**: Every product create, edit and delete, and every user edit, delete, profile application and recovery token issued by an administrator, produces exactly one incidence. A failed change produces none.
- **SC-004**: Searching the product list for the barcode of any of the 43 products that have one returns that product. Every search term that worked before the change returns at least the same products.
- **SC-005**: A license photo can be uploaded, viewed, replaced and deleted. An unauthenticated request for it is refused 100% of the time.
- **SC-006**: All existing tests pass, and each new behaviour, including each rejection path above, has tests.

## Assumptions

- **Pattern for sub-panels.** Customers do not expose addresses and contacts as nested routes. They take id lists on create and update, and the linked records are managed through the standalone address and contact resources. Suppliers follow that pattern for addresses, contacts and bank accounts. Bank accounts have no standalone resource today, so one is added. Agreements belong to a single supplier and cannot exist on their own, so they are managed under the supplier, in the same way customer discounts are managed under the customer.
- **Discounts are not applied to prices anywhere today.** The request says product_service reads customer discounts. It does not: product_service only discards them when products are merged (spec 010), and no pricing or sales code reads them. This feature makes them maintainable. Applying them to prices stays out of scope, as requested.
- **Incidence wording.** Legacy writes "Product created." for products, with instance set to the product id. For users it writes the user id as content and instance 0, because user ids are text. New product incidences describe the action in the same style. New user incidences use the affected user's employee id as the instance, which is never null since migration 012, and keep the user id in the content so that searches by content still find the legacy rows.
- **The incidence service requires a reason.** It is non-blank and is stored as the comment. For these automatic records, the reason is the action description.
- **License photo storage.** The request asked for the product image pattern. That pattern is followed for validation (size limit and image decoding) and for the upload, replace, fetch and delete flow. Storage follows the private proof-of-delivery pattern instead, because product images are public and are reduced to 150 px. This is a privacy decision (FR-024, FR-025). Files are named uniquely rather than by content hash, so deleting one operator's photo can never remove another's.
- **Migrations path.** The request named `migrations/sql`, but SQL migrations live directly in `migrations/`. The latest is `021_user_must_change_password.sql`, so this feature's migration is 022.

## Out of Scope

- UI work: mictlanix/mbe-roadmap#14, mictlanix/mbe-roadmap#16, mictlanix/mbe-roadmap#19 and mictlanix/mbe-roadmap#21.
- Adding privilege checks to routers that lack them. This belongs to the Users & permissions epic.
- Product prices, and applying customer discounts to prices.
- Barcode image generation (Code128 or QR), and filling in barcodes in the data (mictlanix/mbe-roadmap#123).
- Changes to the POS product lookup or to the expired-license advisory check.
- An incidence when products are merged (spec 010). A merge deletes the duplicate product without writing a product incidence.
- Customer taxpayers, addresses and contacts, which already exist.
