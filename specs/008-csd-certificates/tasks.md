---

description: "Task list for Digital Seal Certificates"
---

# Tasks: Digital Seal Certificates

**Input**: Design documents from `/specs/008-csd-certificates/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Included — new endpoints, and the parser encodes assumptions that must be checked
against real material.

**Organization**: Grouped by user story. US1 shipped first, deliberately without US2, while an
open question about credential storage was answered.

**Status**: All tasks complete — US1 and US2 in PR #103 (`f124eba`, `44aa275`), US3 in PR #106
(`ed0be40`).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: US1 metadata reads, US2 registration, US3 validity convention

## Path Conventions

Single project at repository root: `app/`, `tests/`.

---

## Phase 1: Setup

- [x] T001 Confirm nothing in `app/` or `scripts/` currently reads the stored certificate, key or password, so the upload is genuinely absent rather than elsewhere (research R3)
- [x] T002 Confirm with the product owner how the key password is stored, before designing anything that writes it (answer: unencrypted, unchanged)

---

## Phase 2: Foundational

- [x] T003 Add `TaxpayerCertificateResponse` to `app/schemas/fiscal.py`, carrying the five metadata fields and nothing else

---

## Phase 3: User Story 1 — Seeing which certificates exist (P1) 🎯 MVP

**Goal**: An administrator can see holdings and expiry dates without database access.

**Independent test**: List certificates, filter by taxpayer entity, confirm no secret material
appears.

### Tests for User Story 1

- [x] T004 [P] [US1] Cover list, filter pass-through, get and 404 in `tests/api/test_taxpayer_certificates.py`
- [x] T005 [P] [US1] Assert the response field set exactly matches the metadata contract, and that a fake password string appears nowhere in the body, in `tests/api/test_taxpayer_certificates.py`

### Implementation for User Story 1

- [x] T006 [US1] Create `app/services/taxpayer_certificate_service.py` with list and get, excluding the three secret columns from the query itself via `load_only` rather than filtering them from the response
- [x] T007 [US1] Create `app/api/v1/endpoints/taxpayer_certificates.py` with list and get, gated on `SystemObject.TAXPAYERS`
- [x] T008 [US1] Register the router at `/taxpayer-certificates` in `app/api/v1/router.py`
- [x] T009 [US1] Verify the compiled query names only the five metadata columns

**Checkpoint**: Certificates are visible; nothing secret is reachable. Shipped alone,
deliberately, pending T002's answer.

---

## Phase 4: User Story 2 — Registering a new certificate (P2)

**Goal**: A certificate that cannot sign is rejected at registration, not at first use.

**Independent test**: Register a genuine pair (accepted); repeat with a wrong password, a
foreign key, and a mismatched owner (each rejected distinguishably).

### Tests for User Story 2

- [x] T010 [P] [US2] Build genuine DER certificate/key pairs in `tests/unit/test_csd_service.py` rather than mocking the crypto library
- [x] T011 [P] [US2] Cover wrong password, key from another pair, unencrypted key and unreadable certificate in `tests/unit/test_csd_service.py`
- [x] T012 [P] [US2] Cover owner mismatch, unknown taxpayer entity and duplicate number in `tests/unit/test_taxpayer_certificate_service.py`
- [x] T013 [P] [US2] Cover the upload route, including that the password reaches storage as supplied, in `tests/api/test_taxpayer_certificates.py`

### Implementation for User Story 2

- [x] T014 [US2] Create `app/services/csd_service.py` parsing the certificate and key, dispatching the CPU-bound work off the event loop
- [x] T015 [US2] Verify the password opens the key, distinguishing an unprotected key from a wrong password
- [x] T016 [US2] Verify the key's public numbers match the certificate's, catching a correct password on the wrong pair
- [x] T017 [US2] Extract the certificate number and owner using the issuing authority's conventions, each falling back rather than rejecting a valid certificate
- [x] T018 [US2] Add the guarded create to `app/services/taxpayer_certificate_service.py`: owner match, taxpayer entity exists, number not already registered
- [x] T019 [US2] Add the multipart upload route to `app/api/v1/endpoints/taxpayer_certificates.py`, translating parse failures into distinguishable rejections
- [x] T020 [US2] Promote `cryptography` to a direct dependency in `pyproject.toml`

**Checkpoint**: Registration verifies what it stores.

---

## Phase 5: User Story 3 — Validity dates that agree (P1, discovered) ⚠️

**Goal**: Recorded validity windows mean the same thing as those written by the previous
system.

**Independent test**: Reproduce the stored columns of existing certificates from their
binaries alone.

**Why this phase exists**: it was not planned. US2 shipped believing the validity window was
correct; verifying against real certificates showed it wrong on every one.

### Tests for User Story 3

- [x] T021 [US3] Verify the parser against the real certificates in the database, comparing number, owner, validity window and the key/password check against the stored columns
- [x] T022 [US3] Assert the local-time conversion in `tests/unit/test_csd_service.py`, including that the result differs from the certificate's own UTC

### Implementation for User Story 3

- [x] T023 [US3] Convert both validity timestamps to the issuing jurisdiction's local time in `app/services/csd_service.py`
- [x] T024 [US3] Record in the code why four historical records still differ, so they are not mistaken for a defect and "fixed" into the parser
- [x] T025 [US3] Add `tzdata` to `pyproject.toml` so the conversion does not depend on the host's timezone database
- [x] T026 [US3] Record the defect and its blast radius in `CHANGELOG.md`

**Checkpoint**: Dates are comparable across old and new records.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [x] T027 [P] Confirm the metadata contract holds against every real row (quickstart step 3)
- [x] T028 [P] Confirm the parser reproduces stored columns: number, owner and key checks all pass, and validity now matches except the documented historical four (quickstart step 4)
- [x] T029 [P] Confirm no parsed expiry is ever later than the stored one, so the fix cannot overstate validity (SC-006)
- [x] T030 [P] Pass ruff and hold mypy at its baseline, typing the parser to reject non-RSA material rather than suppressing the union warnings
- [x] T031 Record the unencrypted-password decision where it will be found again, so it is not re-raised as a defect

---

## Dependencies & Execution Order

- **Setup (T001–T002)**: T002 gates all of US2. The read endpoints shipped without it
- **Foundational (T003)**: blocks US1
- **US1 (T004–T009)**: independent of US2 and US3
- **US2 (T010–T020)**: depends on T002's answer and on US1's service module. T014 → T015/T016/T017 → T018 → T019
- **US3 (T021–T026)**: depends on US2 existing. T021 is what discovered the defect; T023 fixes it
- **Polish (T027–T031)**: after all stories

### Story independence

US1 is independently deliverable and was delivered that way. US2 depends on US1's service.
US3 is a correction to US2 and cannot precede it.

### Parallel opportunities

- T004–T005, T010–T013, T027–T030 are parallel within their phases
- T015, T016 and T017 are independent checks over the same parsed material

---

## Implementation Strategy

**MVP**: US1. Visibility of expiry dates is the highest-value, lowest-risk slice, and it ships
without touching credential handling at all.

**Then**: US2, once the storage question is answered.

**Then**: US3 — unplanned, and the reason the parser is now verified against real material
rather than only against fixtures it generated itself.
