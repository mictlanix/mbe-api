# Feature Specification: Price List Retirement

**Feature Branch**: `015-price-list-retirement`

**Created**: 2026-08-29

**Status**: Draft

**Input**: User description: "Retiring a price list must be something a client can actually do.
Corresponds to GitHub issue #181. A price list's own product prices must not block its deletion;
customers assigned to the list must be reassigned to a replacement the operator names, not merely
reported; and the operator sees the scale before committing. Coverage must come from the mapped
foreign-key metadata rather than a hand-written list of tables, the way the product merge and its
preview already work."

## Overview

Records what retiring a price list means and what it guarantees. Filed because a price list that
was ever used cannot be retired from a client at all: everything pointing at the list refuses the
deletion, and the two things that point at it need opposite treatment. Its product prices are the
list's own contents — they exist only because the list does — yet they were being reported as
records the client must go and clear, one request per priced product. Its customers are assignments
to a commercial tier, and every one of them has to land somewhere; the API cannot choose for them,
but it also cannot leave the operator resolving them one customer at a time.

The shape follows the product merge (`010-product-merge-integrity`): the same split between what a
record *is configured with* and what *refers to it*, the same derivation of both from the modelled
relationships rather than a hand-written list, and the same read-only report of scale before an
irreversible action.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Retiring a list that was actually priced (Priority: P1)

An operator retires a price list that carries prices for thousands of products. The retirement
succeeds and the list's prices go with it.

**Why this priority**: A list nobody ever priced is a list nobody needs to retire. The lists that
need retiring are exactly the ones that were used, and those are the ones that cannot be retired at
all today. Without this, the feature has no subject.

**Independent Test**: Price several products in a list, retire the list, and confirm it is gone,
that its prices are gone with it, and that the same products' prices in every other list are
untouched.

**Acceptance Scenarios**:

1. **Given** a price list carrying prices for many products and assigned to no customer, **When** the operator retires it, **Then** the retirement succeeds, the list is gone, and its prices are gone with it.
2. **Given** products priced in both the retired list and other lists, **When** the list is retired, **Then** only the retired list's prices are removed; every other list's prices for those products are unchanged.
3. **Given** a price list nothing refers to at all, **When** the operator retires it, **Then** it is retired exactly as before this feature.
4. **Given** a price list referred to through a relationship added to the data model after this feature shipped, **When** the operator retires it, **Then** that relationship is accounted for with no change to the retirement logic — either carried by the same rule as the prices or reported as a blocker.

---

### User Story 2 - Moving the list's customers to another tier (Priority: P1)

An operator retiring a list that customers are assigned to names the tier those customers move to.
The move and the retirement happen together.

**Why this priority**: Same priority as User Story 1 and the other half of the same operation. Every
customer must be on some price list, so retiring a list that customers sit on is meaningless without
saying where they go. Left out, a list in real use is still unretirable — the blocker just moves
from the prices to the customers.

**Independent Test**: Assign customers to a list, retire it naming another list as the replacement,
and confirm the list is gone and every one of those customers now reads as being on the named list,
with no customer of any other list touched.

**Acceptance Scenarios**:

1. **Given** a price list customers are assigned to, **When** the operator retires it naming another list as the replacement, **Then** the retirement succeeds and every one of those customers is now on the named list.
2. **Given** a price list customers are assigned to, **When** the operator retires it without naming a replacement, **Then** the retirement is refused, and the refusal names the customer assignments and how many there are.
3. **Given** any retirement, **When** the operator names a replacement that does not exist, **Then** the retirement is refused and nothing is changed.
4. **Given** any retirement, **When** the operator names the list being retired as its own replacement, **Then** the retirement is refused and nothing is changed.
5. **Given** a price list no customer is assigned to, **When** the operator retires it naming a replacement anyway, **Then** the retirement succeeds, no customer is moved, and the named list is unchanged.
6. **Given** a retirement that cannot complete for any reason, **When** it is attempted, **Then** nothing at all is changed — no customer is left moved onto a list that still exists, and no list is left without its prices.

---

### User Story 3 - Seeing the scale before committing (Priority: P2)

Before retiring a list, an operator is shown what rides on it: how many prices will be deleted and
how many customers must be reassigned.

**Why this priority**: It changes no data and prevents no failure; it informs a decision — the same
standing the merge preview has. It ranks below the two correctness stories, but the retirement is
irreversible and can delete thousands of prices, and the confirmation step that would let an
operator review it otherwise has nothing to show.

**Independent Test**: Request the report for a list that is both priced and assigned to customers,
confirm the counts match what is held, confirm nothing changed by asking, then retire the list and
confirm the retirement acted on exactly the kinds of record the report listed.

**Acceptance Scenarios**:

1. **Given** a list that is priced and assigned to customers, **When** the operator asks what rides on it, **Then** each kind of record is listed with its count, largest first, together with a total.
2. **Given** a list nothing refers to, **When** the operator asks, **Then** the breakdown is empty and the total is zero.
3. **Given** any list, **When** the operator asks, **Then** nothing in the data is changed by asking.
4. **Given** a list that does not exist, **When** the operator asks, **Then** it is refused the same way retiring it would be, so a report that answers is a report about a list that could be acted on.
5. **Given** a report and the retirement that follows it, **When** both run, **Then** the kinds of record counted by the one are exactly the kinds acted on by the other.

### Edge Cases

- A list that is neither priced nor assigned to any customer reports an empty breakdown and retires as a plain deletion.
- Naming a replacement for a list no customer sits on is accepted and moves nothing, rather than being refused: the operator cannot be expected to know the assignment count before asking, and refusing would make the safe habit of always naming one fail unpredictably.
- A list named as the replacement for its own retirement is refused: every customer moved onto it would be moved onto a list about to stop existing.
- A list referred to from outside the modelled data set cannot be accounted for; the deletion fails and the whole retirement is undone, leaving everything as it was.
- The report counts what the retirement *touches*, which is both the prices it deletes and the customers it moves. A client that labels the total as records that will be deleted overstates it.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Retiring a price list MUST delete the prices that list holds, rather than reporting them as records the client must clear first.
- **FR-002**: Retiring a price list MUST NOT affect any other list's prices, including prices for the same products in other lists.
- **FR-003**: Retiring a price list MUST accept an optional replacement list, and MUST move every customer assigned to the retired list onto it.
- **FR-004**: A retirement MUST be refused when customers are assigned to the list and no replacement is named; the refusal MUST name the customer assignments and their count, as it does today.
- **FR-005**: A named replacement MUST be rejected when it does not exist, and when it is the list being retired.
- **FR-006**: A retirement that cannot complete MUST leave everything unchanged; no partially retired state may be observable — no customers moved without the list going, and no prices deleted without it.
- **FR-007**: System MUST report, before a retirement and on request, how many records of each kind ride on the list, ordered largest first, with a total, changing nothing by asking.
- **FR-008**: The report MUST cover exactly the kinds of record the retirement acts on — neither fewer nor more — so the two cannot describe different operations.
- **FR-009**: The report MUST be refused for a list that does not exist, in the same way and for the same reason a retirement would be.
- **FR-010**: Coverage of what refers to a price list MUST be derived from the modelled relationships, with no hand-maintained list of them, so a relationship added later is accounted for without changing the retirement or the report.
- **FR-011**: Every relationship to a price list other than the prices it holds MUST continue to block a retirement, so a relationship added later fails loudly rather than being silently deleted.
- **FR-012**: Both the retirement and the report MUST be available only to an authenticated caller, on the same terms as the other price list operations.

### Key Entities *(include if data involved)*

- **Price list**: A commercial tier. Holds a price per product and is what customers are assigned to. The record being retired.
- **Product price**: The price of one product *in one list* — the pair is unique. It is the list's own contents: once the list is gone the row means nothing and can be reached by nothing.
- **Customer**: Assigned to exactly one price list at all times. The assignment is a business decision, so a retirement moves it only to a tier the operator names.
- **Replacement price list**: The tier the retired list's customers are moved to. Named per retirement, never inferred.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An operator retires a price list holding prices for any number of products in a single request, with no preparatory requests of any kind.
- **SC-002**: Retiring a list that customers sit on takes one request regardless of how many customers are assigned, replacing the one-request-per-customer reassignment the client performs today.
- **SC-003**: After a retirement, nothing anywhere still refers to the retired list, and every customer that was on it is on the named replacement.
- **SC-004**: After a failed retirement, the list, its prices and its customers' assignments are byte-for-byte what they were before the attempt.
- **SC-005**: The counts an operator is shown before a retirement match, kind for kind, the records the retirement then acts on.
- **SC-006**: A relationship to price lists added to the data model later is reflected in both the report and the retirement's refusals with no edit to either.

## Assumptions

- The replacement list is named on the retirement request itself rather than through a separate reassignment step, so that the move and the deletion succeed or fail as one.
- Naming a replacement is optional, and omitting it preserves today's behaviour exactly: a list with customers on it is refused, with the blocker named. This keeps existing clients working and makes the refusal the safe default.
- A replacement may be named even when no customer is assigned; it simply moves nobody. The alternative — refusing it — would punish a client that names one defensively.
- The report is a read-only request about a single list; it takes no replacement, because naming one changes no count.
- Both the retirement and the report require an authenticated caller and no further privilege, matching the price list endpoints as they stand rather than introducing a privilege the system's object catalogue does not define.
- Prices are the only thing a retirement deletes. Anything else that refers to a price list, now or later, keeps blocking — the choice of what may be swept away is stated once and deliberately, not inferred.
