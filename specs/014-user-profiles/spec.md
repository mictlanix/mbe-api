# Feature Specification: User Profiles as Permission Templates

**Feature Branch**: `014-user-profiles`

**Created**: 2026-08-11

**Status**: Draft

**Input**: User description: "implement user profiles, the current permissions scheme (user->privileges) remains the same, no logic change, the profiles will work as a template that when applied to the user the permissions will be copied"

## Background

Today a user's permissions are stored as one entry per system object, each carrying a
create/read/update/delete permission mask. There are 107 system objects, so provisioning
a single user means deciding 107 permission masks. Every cashier is provisioned the same way as
the last cashier, and every warehouse clerk the same way as the last warehouse clerk, but nothing
in the system records that fact. The knowledge lives in whoever set up the previous account.

The consequence is slow onboarding and silent inconsistency: two people doing the same job end up
with different permissions because someone missed an object, and nobody notices until one of them
cannot do their work — or until one of them can do something they should not.

A **user profile** is a named, reusable permission set — "Cashier", "Warehouse Clerk", "Branch
Manager" — that an administrator maintains once and applies to as many users as needed.

Profiles are a **template**, not a live grouping. Applying a profile copies its permissions onto
the user, and the copy is the account's complete permission set. From that moment those permissions
are the user's own: editing the profile does not reach back into users already provisioned from it.
The account does keep a note of which profile it came from, so an administrator can find every
account provisioned from a profile and re-apply a correction — but that note is provenance only.
The permission scheme the rest of the system reads is untouched, and nothing consults a profile
when deciding whether a request is allowed.

## Clarifications

### Session 2026-08-11

- Q: When a profile covers only some system objects, what happens to the ones it omits? → A: **Full replace.** The applied profile becomes the account's entire permission set; every system object the profile does not name is set to deny.
- Q: Does a user remember the profile it was provisioned from? → A: **Yes.** The account carries a reference to the last profile applied, exposed when the user is read, so administrators can find and re-provision every account on a profile.

### Session 2026-08-12

- Q: Can a user be provisioned from a profile at creation time, or only by a separate apply afterwards? → A: **Both.** Creating a user accepts an optional profile and applies it in the same action; the standalone apply remains, for re-provisioning accounts that already exist.
- Q: Does a profile store and return an entry for every system object, or only the ones it grants? → A: **Only the ones it grants**, on both write and read. Absence means denied. Users keep their existing full matrix of entries; profiles are sparse.
- Q: An apply replaces permissions in full, but the existing per-user edit is a partial upsert — should the two be aligned? → A: **No, keep the asymmetry** and state it explicitly. Converting the per-user edit to full replace would change behaviour existing callers depend on, which is outside this feature.
- Q: Does the user list show which profile each account came from, or only allow filtering by it? → A: **Every row carries it**, empty for accounts with no recorded origin, so the list is legible as well as filterable.
- Q: Is profile name uniqueness case-sensitive? → A: **Case-insensitive.** "cashier" conflicts with "Cashier"; the name is stored as typed and compared without case.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Provision a new user from a profile (Priority: P1)

An administrator hires a cashier for the Norte branch. Instead of walking through every system
object, they create the user account naming the existing "Cashier" profile, and the account exists
with exactly the permissions the profile describes, identical to every other cashier provisioned the
same way. The same profile can be applied again later to an account that already exists — when
someone changes role, or when a correction needs to reach them.

**Why this priority**: This is the reason the feature exists. It is the whole of the value on its
own — a catalog of profiles with nothing to apply them to is inert, and applying without a catalog
is what already exists.

**Independent Test**: Create a profile with a known permission set, create a user naming it, then
read that user back and confirm their permissions match the profile on every system object — granted
on the ones it names, denied on the ones it does not. Repeat by applying to an already-existing user.
Fully testable without any other story.

**Acceptance Scenarios**:

1. **Given** a profile "Cashier" granting read on products and create+read on sales orders, **When** an administrator creates a user naming that profile, **Then** the new account holds read on products and create+read on sales orders, is denied on every other system object, and records "Cashier" as its origin.
2. **Given** the same profile, **When** an administrator applies it to a user who already exists and has no permissions at all, **Then** that user holds read on products and create+read on sales orders, and is denied on every other system object.
3. **Given** an administrator creating a user, **When** they name no profile, **Then** the account is created with every permission denied and no recorded origin, exactly as it is today.
4. **Given** a profile that does not exist, or one that is inactive, **When** an administrator creates a user naming it, **Then** the request is refused and no user is created.
5. **Given** a user who already holds full permissions on products, **When** an administrator applies a profile that grants read-only on products, **Then** the user's products permission becomes read-only.
6. **Given** a user who holds permissions on warehouses, **When** an administrator applies a profile that does not name warehouses at all, **Then** the user is denied on warehouses.
7. **Given** a user with an active session, **When** an administrator applies a profile to that user, **Then** the user's existing sessions stop being accepted and they must sign in again.
8. **Given** a user provisioned from a profile, **When** an administrator reads that user, **Then** they can see which profile the account was last provisioned from.
9. **Given** a profile identifier that does not exist, **When** an administrator applies it to a valid user, **Then** the request is refused as not found and the user's permissions are unchanged.
10. **Given** a valid profile, **When** an administrator applies it to a user identifier that does not exist, **Then** the request is refused as not found.
11. **Given** a non-administrator caller, **When** they attempt to apply a profile to any user, **Then** the request is refused as forbidden.

---

### User Story 2 - Maintain the profile catalog (Priority: P2)

An administrator opens the profile catalog to see which permission templates exist, inspects what
"Warehouse Clerk" actually grants, corrects a permission that was set too broadly, renames a
profile whose job title changed, and retires a profile for a role the company no longer staffs.

**Why this priority**: Without it, profiles can only be as good as their first draft, and a wrong
profile is worse than no profile because it spreads the mistake. But provisioning (P1) delivers
value against a catalog seeded by any means, so this comes second.

**Independent Test**: Create several profiles, list them, retrieve one and confirm its permission
set reads back, edit its name and permissions and confirm both changes persist, then delete an
unapplied one and confirm it no longer appears in the catalog.

**Acceptance Scenarios**:

1. **Given** several profiles exist, **When** an administrator lists them, **Then** they see each profile's name and status, and can page through the results.
2. **Given** a profile exists, **When** an administrator retrieves it, **Then** they see its name, description, status, and its permission mask for every system object it covers.
3. **Given** a profile exists, **When** an administrator changes its name and one of its permission masks, **Then** both changes are recorded and read back on the next retrieval.
4. **Given** a profile named "Cashier" exists, **When** an administrator creates or renames another profile to "Cashier" or to "cashier", **Then** the request is refused as a conflict in both cases.
5. **Given** a profile that has never been applied to anyone, **When** an administrator deletes it, **Then** it disappears from the catalog.
6. **Given** a profile that users were provisioned from, **When** an administrator deletes it, **Then** the request is refused as a conflict naming how many users still reference it, and no user's permissions change.
7. **Given** a profile for a role the company no longer staffs, **When** an administrator marks it inactive, **Then** it can no longer be applied but remains readable, and the users provisioned from it are unaffected.
8. **Given** a non-administrator caller, **When** they attempt to read or modify the profile catalog, **Then** the request is refused as forbidden.

---

### User Story 3 - Correct a profile and bring existing users up to date (Priority: P3)

An administrator discovers the "Cashier" profile has been granting delete on sales orders, which
cashiers should not have. They correct the profile, find every account provisioned from it, and
re-apply it to each so the correction reaches the people already provisioned.

**Why this priority**: This is the consequence of copy semantics, and it is the part administrators
are most likely to get wrong by assuming the edit propagates. It needs to be stated and tested,
but it is composed entirely of P1 and P2 behaviour — no new capability.

**Independent Test**: Apply a profile to two users, edit the profile, confirm both users are
unchanged, filter the user list by that profile to find them, re-apply to each, then confirm both
reflect the edit.

**Acceptance Scenarios**:

1. **Given** a user provisioned from a profile, **When** an administrator edits that profile, **Then** the user's permissions are unchanged.
2. **Given** several users provisioned from the same profile, **When** an administrator filters the user list by that profile, **Then** they see exactly those users, each row showing the profile it came from.
3. **Given** a mix of provisioned and hand-built accounts, **When** an administrator lists users without filtering, **Then** each row shows its origin profile where it has one and nothing where it does not.
4. **Given** a user provisioned from a profile that has since been edited, **When** an administrator re-applies the profile to that user, **Then** the user's permissions reflect the edited profile.
5. **Given** a user provisioned from a profile, **When** an administrator hand-edits that user's permissions through the existing per-user permission editing, **Then** the change takes effect, the profile is unaffected, and the account still shows the profile it was provisioned from.
6. **Given** a user whose permissions were hand-edited after being provisioned from a profile, **When** an administrator re-applies that profile, **Then** the hand-edits are discarded and the user matches the profile again.

---

### Edge Cases

- **Profile applied to an administrator.** Administrators already bypass every per-object permission check, so a profile applied to one is recorded but has no effect on what they can do. The apply is still permitted rather than refused — the permissions become meaningful the moment the `administrator` flag is cleared, and refusing would make an account's permission set depend on a flag the profile does not own. An administrator who applies a restrictive profile to their own account therefore does not lock themselves out, and their sessions are invalidated like anyone else's.
- **Profile covers fewer system objects than exist.** A profile need not name every system object; the objects it omits are denied on the user it is applied to. A thin profile is therefore a restrictive one, not a partial one.
- **Profile grants nothing.** A profile with no permission entries, or whose every mask is zero, is valid; applying it denies the user everything. This is a legitimate way to express a suspended role.
- **New system object is added after profiles exist.** Stored profiles do not name it, so applying any of them denies it. Granting it means editing the profiles that should have it — the same work as granting it to a user today, done once per profile.
- **Profile referenced by users is deleted.** Refused as a conflict, naming how many users still reference it, consistent with every other delete in the system. Retiring a role is done by marking the profile inactive, which is what that status is for.
- **Retired profile is applied.** A profile that has been marked inactive cannot be applied; the request is refused. It remains readable so the accounts still pointing at it stay explicable.
- **Two administrators apply different profiles to the same user at once.** The last apply to commit wins in full — both the permission set and the recorded origin. Permission masks from the two profiles are never interleaved, and the recorded origin never disagrees with the permissions that were written.
- **Partial edit after an apply.** Editing one system object on a provisioned account changes only that object — the partial-upsert semantics of the existing per-user edit are unaffected by the account having come from a profile. The account keeps its recorded origin and now differs from it. This asymmetry between the two write paths is deliberate; see FR-026.
- **Recorded origin goes stale.** Hand-editing a user's permissions after an apply leaves the origin pointing at a profile the account no longer matches. The origin records where the account was last provisioned from, not what it currently holds; detecting drift is out of scope.
- **Profile names an unknown system object.** A permission entry for a value outside the system object catalog is refused at profile creation and edit time, so an unapplyable profile cannot be stored.
- **Applying a profile to an inactive or suspended user.** Permitted. Permissions are set now and take effect if the account is reactivated; account status governs sign-in, not what the account is permitted to do.

## Requirements *(mandatory)*

### Functional Requirements

**Profile catalog**

- **FR-001**: Administrators MUST be able to create a user profile with a name and an optional description.
- **FR-002**: A profile MUST hold permission masks drawn from the same create/read/update/delete vocabulary already used for per-user permissions, with at most one entry per system object.
- **FR-003**: A profile MUST store an entry only for the system objects it grants something on. Reading a profile MUST return only those entries; the objects absent from the response are denied. An entry whose mask grants nothing is equivalent to no entry at all, and a profile with no entries is valid.
- **FR-004**: Profile names MUST be unique without regard to case — "cashier" conflicts with "Cashier". The name MUST be stored as the administrator typed it, and compared for uniqueness without case. Creating or renaming a profile to a name already in use MUST be refused as a conflict.
- **FR-005**: Administrators MUST be able to list profiles, with search by name and pagination, matching the conventions the user catalog already uses.
- **FR-006**: Administrators MUST be able to retrieve a single profile including every permission entry it holds.
- **FR-007**: Administrators MUST be able to update a profile's name, description, status, and permission set.
- **FR-008**: Administrators MUST be able to delete a profile that no user references. Deleting a profile that users were provisioned from MUST be refused as a conflict, naming how many users still reference it, consistent with how every other referenced delete in the system is refused.
- **FR-009**: A profile MUST carry an active/inactive status, using the same status vocabulary the rest of the system's catalogs use. An inactive profile MUST NOT be applyable, and MUST remain readable.
- **FR-010**: A profile permission entry naming a system object outside the known catalog MUST be refused as a validation error.

**Applying a profile**

- **FR-011**: Administrators MUST be able to apply a profile to a user in a single action, which copies the profile's permission masks onto that user's permissions.
- **FR-012**: Creating a user MUST accept an optional profile, applying it as part of the same action so the account exists with its permissions already set. Creation MUST remain valid with no profile named, in which case the account is created with every permission denied, exactly as it is today. A creation naming a profile that does not exist or is inactive MUST be refused, and MUST NOT leave a user behind.
- **FR-013**: An apply MUST replace the user's permissions in full. Every system object the profile names takes the profile's mask; every system object the profile does not name MUST be denied. The applied profile becomes the account's complete permission set, and no permission the account held beforehand survives an apply.
- **FR-014**: An apply MUST be a one-time copy. The system MUST NOT consult a profile when the user's permissions are later read or enforced, and MUST NOT propagate later profile edits to users already provisioned.
- **FR-015**: Applying a profile MUST invalidate the target user's existing sessions, consistent with every other privilege mutation.
- **FR-016**: Applying a non-existent profile, or applying to a non-existent user, MUST be refused as not found, leaving the target unchanged.
- **FR-017**: Applying an inactive profile MUST be refused.
- **FR-018**: An apply MUST be all-or-nothing. A failure part-way MUST leave the user's permissions exactly as they were.
- **FR-019**: An apply MUST record on the user which profile it was applied from, replacing any previously recorded origin. A user who has never had a profile applied MUST have no recorded origin.
- **FR-020**: Reading a user MUST expose the profile it was last provisioned from, if any. Listing users MUST expose it on every row, so the list is legible as well as filterable, and MUST leave it empty for accounts with no recorded origin.
- **FR-021**: Administrators MUST be able to filter the user list by profile, to find every account provisioned from a given profile.
- **FR-022**: The recorded origin MUST be provenance only. It MUST NOT be consulted when the user's permissions are read or enforced, and hand-editing a user's permissions MUST NOT clear it.

**Preserving existing behaviour**

- **FR-023**: The existing per-user permission scheme MUST remain unchanged in shape and in meaning. Permissions continue to be stored per user, per system object.
- **FR-024**: Permission enforcement MUST be unchanged. No authorization decision may depend on a profile, whether or not the user was provisioned from one.
- **FR-025**: The existing per-user permission editing MUST continue to work as it does today, including on users provisioned from a profile. A user's permissions remain individually editable after an apply, and an account having a recorded origin MUST NOT restrict what can be edited on it.
- **FR-026**: The two ways of writing a user's permissions MUST keep their distinct semantics, deliberately and not by oversight: the existing per-user edit is a partial upsert that touches only the system objects its payload names, while an apply replaces the account's permissions in full. Changing the per-user edit to replace in full is explicitly out of scope, as it would alter behaviour existing callers depend on.
- **FR-027**: Users who have never had a profile applied MUST behave exactly as they do today, with no change to how their permissions are read or written.

**Access control**

- **FR-028**: Every profile capability — reading the catalog, modifying it, and applying a profile — MUST be restricted to administrators, matching the existing user-management endpoints.
- **FR-029**: Unauthenticated requests to any profile capability MUST be refused as unauthenticated.

### Key Entities

- **User Profile**: A named, reusable permission template. Carries a name (unique), an optional description, an active/inactive status, and a set of profile permissions. Independent of any user — a profile exists whether or not it has ever been applied.
- **Profile Permission**: One entry within a profile: a system object plus the permission mask granted on it. The same object/mask pairing already used for per-user permissions, held against a profile instead of a user. Entries exist only for the objects a profile grants something on — unlike a user, which carries an entry for every system object — so a profile is a statement of what a role grants, not a full matrix.
- **User** *(existing, extended)*: Gains an optional reference to the profile it was last provisioned from. The reference is provenance — it records history, not authority, and no permission decision reads it. Absent on every user that predates the feature and on every user never provisioned from a profile. Nothing else about the user changes.
- **Access Privilege** *(existing, unchanged)*: A user's permission on one system object. Remains the sole source of truth for what a user may do. An apply writes into these entries; nothing else about them changes.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An administrator can give a new user a complete permission set in one action, replacing the current path of deciding a permission mask for each of the 107 system objects individually.
- **SC-002**: Provisioning permissions for a new user takes under one minute, down from the multi-minute per-object walk it requires today.
- **SC-003**: Two users provisioned from the same profile, with no subsequent hand-editing, hold identical permissions on every system object — verifiable by reading both accounts back and comparing.
- **SC-004**: After an apply, the user's permissions match the applied profile on every system object in the catalog — granted where the profile names them, denied everywhere else — with zero discrepancies.
- **SC-005**: No authorization behaviour changes for users who have never had a profile applied: the full existing permission test suite passes unmodified.
- **SC-006**: Editing a profile changes the permissions of zero already-provisioned users.
- **SC-007**: An administrator can see what a profile grants without applying it, and can tell before applying which permissions a user will end up with.
- **SC-008**: Given a profile, an administrator can retrieve every account provisioned from it in one query, so correcting a profile does not depend on a list kept outside the system.

## Assumptions

- **Profiles carry permissions only.** The `administrator` flag, the user's facility/point-of-sale/cash-drawer settings, the linked employee, and account status are per-user facts, not role facts. Applying a profile does not touch them. The feature description said "the permissions will be copied", and nothing wider.
- **Administrator-only, matching the existing user endpoints.** User management is already administrator-gated rather than governed by a system object permission. Profiles follow it rather than introducing a new system object, which would mean touching the permission vocabulary the feature is explicitly meant to leave alone.
- **Session invalidation on apply is not optional.** An apply is a privilege mutation, and privilege mutations already invalidate sessions. Treating an apply differently would let a user keep exercising permissions that were just revoked.
- **Applying to one user at a time.** Bulk apply — one profile to many users in a single action — is out of scope. The description says "applied to the user", singular. Re-applying to a set of users is done one apply at a time.
- **Profiles are authored directly, not captured from a user.** "Save this user's permissions as a new profile" is a plausible convenience and is out of scope; a profile's permissions are set the same way a user's are.
- **Existing users are not migrated.** No attempt is made to infer which profile existing accounts resemble. Every account keeps the permissions it has and has no recorded origin; profiles begin empty and are authored by administrators.
- **Profile deletion is refused while referenced, rather than clearing the reference.** Since the origin is a real reference on the user, the system's existing refuse-while-referenced behaviour applies to it as it does to every other referenced row, and it keeps a delete from silently rewriting user records. Retiring a role uses the inactive status, which FR-009 provides for exactly this.
- **Drift detection is out of scope.** The recorded origin says where an account was provisioned from, not whether it still matches. Comparing a user's live permissions against their origin profile — and defining what "matches" means when the profile has since been edited — is a separate feature.
- **Reuse of the existing permission vocabulary.** Profiles reuse the established system object catalog and the create/read/update/delete mask, not a parallel representation. A profile permission means exactly what the same mask means on a user today.
- **No system object catalog endpoint.** Because a profile returns only what it grants, rendering a full permission matrix needs the list of system objects. Clients already obtain it the way they do today — a user's permissions come back as a complete matrix — so exposing the catalog as its own resource is out of scope.
- **Status vocabulary is the existing one.** Profile status reuses the system-wide entity status rather than introducing a profile-specific lifecycle.
- **Profiles are global, not facility-scoped.** A single catalog serves every facility; "Cashier" means the same thing at every branch. Per-facility variants of a role are expressed as separate profiles if they are ever needed, not as a scoping dimension on the profile.
- **No audit trail beyond what exists.** Recording an apply in the incidence log is not specified here; the existing user-mutation audit behaviour is neither extended nor reduced.
