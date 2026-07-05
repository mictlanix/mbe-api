# Feature Specification: Price Management Service

**Feature Branch**: `004-price-management-service`

**Created**: 2026-07-05

**Status**: Draft

**Input**: User description: "Build a price management service for per-product prices (ProductPrice), decoupled from the product domain. Today, product creation, retrieval, and update in app/services/product_service.py directly own ProductPrice CRUD: creating a product auto-provisions a zeroed price row for every existing price list, fetching/updating a product re-attaches all its price rows and their parent PriceList objects, and deleting or merging products directly deletes ProductPrice rows. This couples an unrelated concern (pricing) to the product lifecycle and was the root cause of a recent bug where re-attaching price-list relations twice in the same request crashed a product update. Extract this into a standalone internal module, following the same pattern already used for PriceList (app/api/v1/endpoints/price_lists.py + app/services/price_list_service.py): a new ProductPrice schema module, a new product_price_service.py, and a new top-level /product-prices router (mounted in app/api/v1/router.py alongside /price-lists), gated by SystemObject.PRICING (already defined in app/enums.py, currently unused elsewhere). The new endpoints must support listing prices filterable by product id and/or price_list id, showing the resolved price_list object; creating a price row for a given product + price_list pair; and fetching, updating, and deleting a single price row by its id. Product endpoints and product_service.py must no longer reference ProductPrice or PriceList at all: ProductResponse drops the prices field entirely, create_product no longer auto-provisions price rows, and delete_product/merge_products must delegate ProductPrice cleanup to the new product_price_service module. Out of scope: PriceList CRUD (/price-lists) is already separate and must not change. Product attributes tax_rate, tax_included, price_type, currency stay on the product."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Manage a product's price independently of the product record (Priority: P1)

As a pricing administrator, I need to create, view, update, and delete a product's price for a
specific price list without touching the product record itself, so pricing changes never require
editing unrelated product data, and product edits never risk corrupting pricing data.

**Why this priority**: This is the core value of the feature — pricing and product data become
independently manageable.

**Independent Test**: Can be fully tested by creating a product with no prices, creating a price
entry for it against a price list via the new capability, retrieving it, updating its values, and
deleting it — none of which requires editing the product record.

**Acceptance Scenarios**:

1. **Given** a product with no prices, **When** an administrator creates a price for it against a
   given price list, **Then** the price entry is retrievable and shows the price list's details
   (name and margins) along with the price, low profit, and high profit values.
2. **Given** an existing price entry, **When** an administrator updates its price, low profit, or
   high profit values, **Then** the updated values are returned and persisted.
3. **Given** an existing price entry, **When** an administrator deletes it, **Then** it no longer
   appears in any subsequent listing for that product.

---

### User Story 2 - Find prices by product or price list (Priority: P2)

As a pricing administrator, I need to list and filter prices by product and/or by price list, so
I can review every price tied to a product, or see how a price list is applied across products.

**Why this priority**: Important for day-to-day review and auditing, but the system already
delivers its core value via direct CRUD (User Story 1) without filtering.

**Independent Test**: Can be tested by creating several price entries across multiple products and
price lists, then querying by product alone, by price list alone, and by both together, verifying
only matching entries are returned each time.

**Acceptance Scenarios**:

1. **Given** multiple price entries across different products, **When** filtering by a specific
   product, **Then** only that product's price entries are returned.
2. **Given** multiple price entries across different price lists, **When** filtering by a specific
   price list, **Then** only entries belonging to that price list are returned.

---

### User Story 3 - Product operations stay free of pricing concerns (Priority: P1)

As a consumer of the product catalog, I need product create, read, update, and delete operations
to be entirely unaffected by pricing, so product data stays lightweight and pricing changes or
defects can never break product operations.

**Why this priority**: This is the other half of the core value — removing pricing from the
product lifecycle is as critical as giving pricing its own home.

**Independent Test**: Can be tested by creating, retrieving, updating, deleting, and merging
products, and confirming no pricing data ever appears in a product response and no price rows are
silently created.

**Acceptance Scenarios**:

1. **Given** a new product is created, **When** its record is retrieved, **Then** the response
   contains no pricing information whatsoever, and no price entries have been auto-created for it.
2. **Given** a product with existing price entries, **When** the product is deleted, **Then** its
   associated price entries are removed automatically and no orphaned pricing data remains.
3. **Given** two products are merged into one, **When** the merge completes, **Then** the
   duplicate product's price entries are removed while the canonical product's own price entries
   are unaffected.

---

### Edge Cases

- What happens when someone tries to create a second price entry for the same product and price
  list combination? The system must prevent the duplicate rather than silently creating two
  conflicting prices.
- What happens when a price entry is requested, updated, or deleted using an identifier that
  doesn't exist? The system must return a clear not-found outcome.
- What happens when a price is created referencing a product or price list that doesn't exist?
  The system must reject the request.
- What happens when invalid values are submitted (e.g., a negative price)? The system must reject
  the request with a validation error.
- What happens to a product's price entries when the product is deleted or merged away? They must
  be cleaned up automatically so no orphaned pricing data is left behind.

## Requirements *(mandatory)*

### Functional Requirements

**Price Management (US1)**

- **FR-001**: System MUST allow creating a price entry for a given product and price list
  combination, capturing a price, a low profit margin, and a high profit margin.
- **FR-002**: System MUST allow retrieving a single price entry by its identifier, including the
  associated price list's details (name and margins).
- **FR-003**: System MUST allow updating the price, low profit, and high profit values of an
  existing price entry.
- **FR-004**: System MUST allow deleting a single price entry.
- **FR-005**: System MUST prevent creating more than one price entry for the same product and
  price list combination.

**Filtering (US2)**

- **FR-006**: System MUST allow listing price entries filtered by product.
- **FR-007**: System MUST allow listing price entries filtered by price list.
- **FR-008**: System MUST allow listing price entries filtered by product and price list at the
  same time.

**Decoupling From Products (US3)**

- **FR-009**: Product create, read, update, and delete operations MUST NOT return, accept, or
  otherwise expose pricing data.
- **FR-010**: Creating a new product MUST NOT automatically generate any price entries; price
  entries are only created explicitly through the price management capability.
- **FR-011**: Deleting a product MUST automatically remove all of that product's associated price
  entries.
- **FR-012**: Merging two products into one MUST remove the duplicate product's price entries
  while preserving the canonical product's own price entries.
- **FR-013**: Access to the price management capability MUST be governed by its own permission,
  independent of product permissions.

### Key Entities

- **Product Price**: The price of a single product under a specific price list. Key attributes:
  price, low profit margin, high profit margin. Belongs to exactly one product and one price list.
- **Price List** *(existing, unchanged)*: A named pricing tier with its own profit margin
  configuration, already managed independently of this feature. A Product Price references one.
- **Product** *(existing, unchanged)*: The catalog item being priced. No longer holds or manages
  pricing data directly.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Administrators can create, update, and delete a product's price without navigating
  to or modifying the product record.
- **SC-002**: 100% of product catalog responses (create, read, update) contain zero pricing
  fields.
- **SC-003**: Creating a new product results in zero automatically generated price entries.
- **SC-004**: Deleting or merging a product leaves zero orphaned price entries behind.
- **SC-005**: Administrators can retrieve every price for a given product, or every product priced
  under a given price list, in a single request.

## Assumptions

- Only one price entry is allowed per product/price-list combination — there is no historical or
  versioned pricing.
- Price management is an internal, staff-facing capability, consistent with the rest of this
  system's administrative endpoints — not a customer-facing feature.
- Price List management itself (creating/editing price lists) is unchanged and out of scope.
- Access to price management requires the same style of permission-gating already used by other
  administrative capabilities in this system.
- Product attributes that relate to pricing context (tax rate, tax-inclusion flag, price type,
  currency) remain on the product record and are unaffected by this feature.
