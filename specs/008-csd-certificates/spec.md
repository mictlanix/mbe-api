# Feature Specification: Digital Seal Certificates

**Feature Branch**: `010-open-issues-fixes`, `012-csd-validity-local-time`

**Created**: 2026-07-21

**Status**: Implemented (PR #103, corrected by PR #106)

**Input**: Surfaced while closing #100 — the certificate table had a model but no endpoint, the same gap that issue described for taxpayer entities. Unlike that one it holds private signing keys, so it needed its own decisions about what may be read and what may be written.

## Overview

Digital seal certificates are the credentials a taxpayer entity uses to sign fiscal documents:
a certificate, a private key, and the password that unlocks it. They were stored but
unreachable — no way to see which certificates existed, when they expire, or to register a new
one without direct database access.

The governing constraint is that the stored material is a **private signing credential**. Every
decision here follows from that: what may leave the system, what must be proven before storage
is accepted, and what is deliberately not offered.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Seeing which certificates exist and when they expire (Priority: P1)

An administrator needs to know which taxpayer entities have a usable certificate and when each
stops being valid, so an expiry can be anticipated rather than discovered when signing starts
failing.

**Why this priority**: A certificate that expires unnoticed halts fiscal document issuance
entirely. Visibility is the whole point; registering new ones is secondary to knowing the
state of the current ones.

**Independent Test**: List certificates, filter to one taxpayer entity, and confirm each entry
shows its identifying number, owner and validity window — and nothing more.

**Acceptance Scenarios**:

1. **Given** registered certificates, **When** the list is requested, **Then** each entry shows its number, owning taxpayer entity, validity window and lifecycle state.
2. **Given** certificates for several taxpayer entities, **When** the list is filtered by one of them, **Then** only that entity's certificates are returned.
3. **Given** any certificate, **When** it is read by any means the interface offers, **Then** the certificate file, the private key and the password are absent from the response.
4. **Given** an administrator without rights over taxpayer data, **When** they attempt to read certificates, **Then** they are refused.

---

### User Story 2 - Registering a new certificate (Priority: P2)

An administrator receives a new certificate and key from the tax authority, along with the
password that unlocks the key. They register it against the taxpayer entity that owns it, and
want to know immediately if anything is wrong — not when the first document fails to sign.

**Why this priority**: Registration happens rarely, roughly once every four years per entity,
but a silently wrong registration is discovered at the worst possible moment.

**Independent Test**: Register a genuine certificate and key with the correct password
(accepted); repeat with a wrong password, a mismatched key, and an unreadable file (each
rejected with a distinguishable reason).

**Acceptance Scenarios**:

1. **Given** a matching certificate, key and correct password, **When** registered against the owning taxpayer entity, **Then** it is stored and its number and validity window are reported back.
2. **Given** the wrong password, **When** registration is attempted, **Then** it is rejected as unusable rather than stored.
3. **Given** a key that belongs to a different certificate, **When** registration is attempted with its correct password, **Then** it is rejected.
4. **Given** a certificate belonging to a different taxpayer entity, **When** registration is attempted against this one, **Then** it is rejected, naming the entity the certificate actually belongs to.
5. **Given** a certificate already registered, **When** registration is attempted again, **Then** it is rejected as a duplicate.
6. **Given** a valid registration, **When** the identifying number and validity window are recorded, **Then** they are taken from the certificate itself, not from what the administrator typed.

---

### User Story 3 - Validity dates that agree with the existing records (Priority: P1)

An administrator comparing a newly registered certificate against ones registered before this
interface existed sees consistent dates. A certificate's recorded expiry means the same thing
regardless of how it was registered.

**Why this priority**: Elevated to P1 after discovery. A certificate whose recorded expiry is
wrong by hours defeats the purpose of User Story 1 — the visibility it provides would be
subtly false, and false in the direction of appearing valid for longer than it is.

**Independent Test**: Register a certificate and compare its recorded validity window against
records created by the previous system for the same kind of certificate.

**Acceptance Scenarios**:

1. **Given** a certificate registered through this interface, **When** its validity window is compared against certificates registered by the previous system, **Then** both express the same convention.
2. **Given** any certificate, **When** its recorded expiry is read, **Then** it does not overstate how long the certificate remains valid.

### Edge Cases

- A certificate whose owner cannot be determined from its contents is accepted against the taxpayer entity the administrator names, since an unreadable owner is not evidence of a wrong one.
- A key that opens without any password is rejected: an unprotected signing key is not a valid credential.
- A certificate using an unexpected cryptographic scheme is rejected rather than stored and discovered unusable later.
- Registering against a taxpayer entity that does not exist is rejected, so certificates cannot be orphaned at creation.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Users MUST be able to list certificates and filter them by owning taxpayer entity and lifecycle state.
- **FR-002**: Users MUST be able to retrieve a single certificate by its identifying number.
- **FR-003**: System MUST expose only a certificate's number, owner, validity window and lifecycle state.
- **FR-004**: System MUST NOT return the certificate file, the private key, or the password through any read path.
- **FR-005**: System MUST NOT retrieve the certificate file, key or password from storage when serving a read, so they cannot be exposed by a later change to what is returned.
- **FR-006**: System MUST restrict all certificate access to users holding the taxpayer permission.
- **FR-007**: Users MUST be able to register a certificate by supplying the certificate, the key, the password, and the owning taxpayer entity.
- **FR-008**: System MUST verify the supplied password unlocks the supplied key before storing anything.
- **FR-009**: System MUST verify the key belongs to the certificate before storing anything.
- **FR-010**: System MUST verify the certificate's own stated owner matches the taxpayer entity it is being registered against, where the certificate states one.
- **FR-011**: System MUST reject registration against a taxpayer entity that does not exist.
- **FR-012**: System MUST reject registration of a certificate number already registered.
- **FR-013**: System MUST take the identifying number and validity window from the certificate itself, never from the request.
- **FR-014**: System MUST distinguish rejection reasons — unreadable file, wrong password, mismatched key, wrong owner, duplicate — so an administrator can act on the message.
- **FR-015**: System MUST record validity windows in the same convention as existing records, so dates are comparable across registrations old and new.
- **FR-016**: System MUST NOT report a certificate as valid for longer than it actually is.

### Key Entities

- **Certificate**: A signing credential belonging to a taxpayer entity. Identified by a number issued by the tax authority. Has a validity window and a lifecycle state. Holds three pieces of secret material that never leave the system.
- **Taxpayer entity**: The owner. A certificate cannot exist without one, and the certificate itself states which one it belongs to.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An administrator can determine, without database access, which taxpayer entities hold a valid certificate and when each expires.
- **SC-002**: No secret material is obtainable through the interface, by any request.
- **SC-003**: A certificate that cannot actually sign — wrong password, mismatched key, wrong owner — is rejected at registration rather than discovered when signing fails.
- **SC-004**: An administrator receiving a rejection can tell which of the possible faults occurred.
- **SC-005**: Validity windows recorded through this interface agree with those recorded by the previous system for equivalent certificates.
- **SC-006**: No certificate's recorded expiry is later than its true expiry.

## Assumptions

- The password is stored **unencrypted**, unchanged from the previous system, and the product owner confirmed it stays that way. Validation does not alter this. The mitigation is that the password is never read back on any request path (FR-005), so exposure is bounded by direct database access, which it already was.
- Certificates are issued by a single tax authority in a single jurisdiction, so one date convention applies to all of them and no per-certificate timezone is stored.
- Registration is rare and administrative. Verification cost at registration is therefore irrelevant next to the cost of discovering an unusable certificate during document issuance.
- Amending a stored certificate is not offered. A certificate is an externally issued artifact — there is nothing to correct in place. A wrong one is removed and replaced.
- The identifying number and owner are extracted using conventions specific to the issuing authority. Both fall back gracefully rather than rejecting a valid certificate: an unreadable owner defers to the administrator's stated entity, and an unreadable number falls back to a raw form.
- Historical records registered before this interface existed are left untouched, including a small number whose expiry follows an older, internally inconsistent convention. Rewriting them would alter fiscal records to correct an inconsistency that cannot recur.
