# Phase 0 Research: Digital Seal Certificates

## R1 — Should this be a normal CRUD resource?

**Decision**: No. Metadata-only reads, an upload, and no amend.

**Rationale**: The table holds `certificate_data`, `key_data` and `key_password` — a signing
credential. The project's conventions elsewhere (expand every foreign key, return the whole
record) are actively wrong here. A certificate is also an externally issued artifact: there is
nothing to correct in place, so an amend endpoint would only offer ways to make a stored
credential inconsistent with itself.

**Alternatives considered**: The generic CRUD shape used by every other resource — rejected on
the above, and it would have put the private key on the wire by default.

## R2 — How should the secret columns be kept out of responses?

**Decision**: Exclude them from the query with `load_only`, not just from the response schema.

**Rationale**: Two independent benefits. The private key never enters application memory, so
it cannot be exposed by a later edit to the response model — a contributor adding a field
would get an unloaded-attribute error rather than a leak. And a list of 20 certificates does
not pull megabytes of binary out of the database to discard it.

**Verified**: the compiled SQL names only the five metadata columns.

## R3 — Was the password verified against the key before this feature?

**Decision**: No, and nothing anywhere read the stored material.

**Rationale**: Searched the codebase — no code in `app/` or `scripts/` ever read
`certificate_data`, `key_data` or `key_password`. `cryptography` was present only transitively
via `python-jose` for token handling. The columns were write-once storage populated outside
this API, which is what made an upload endpoint worth adding rather than assuming one existed.

## R4 — What should the upload verify?

**Decision**: Three checks, in order: the password opens the key; the key matches the
certificate; the certificate's stated owner matches the issuer it is attached to.

**Rationale**: The password check is the obvious one, but the **key/certificate match** is the
one that earns its keep: a correct password on the wrong key pair is otherwise undetectable
until the first signing attempt fails in production. Checking the owner prevents attaching a
valid certificate to the wrong entity, which no cryptographic check would catch.

**Additionally**: a key that opens *without* a password is reported as a malformed CSD rather
than a wrong password, so the two failure modes are distinguishable (FR-014).

## R5 — Should the certificate number and dates be trusted from the form?

**Decision**: No — read both from the certificate.

**Rationale**: They are properties of the artifact, not of the request. Taking them from the
form allows a typo to produce a record that disagrees with the credential it describes, which
is exactly the class of error this feature exists to catch. The number is the ASCII bytes of
the X.509 serial, per the issuing authority's convention; the owner's tax identifier is in the
subject.

**Both fall back rather than reject**: an unreadable number degrades to the raw serial, and an
unreadable owner defers to the administrator's stated entity. A convention surprise should not
reject a valid certificate.

## R6 — In what convention are validity windows stored? *(PR #106)*

**Decision**: `America/Mexico_City` local time, not the UTC the certificate carries.

**How it was found**: the parser was verified against the 17 real certificates in `mbe_demo`,
whose stored columns the legacy system populated and which are therefore ground truth. The
certificate number matched on 17 of 17, the owner on 17 of 17, and the password/key checks
passed on 17 of 17 — **and the validity window matched on 0 of 17**, every one off by a
constant 5 or 6 hours.

The two conventions I was unsure about held. The one I had not thought to question — a code
comment read "certificates are issued in UTC", which is true of the certificate and irrelevant
to the column — was wrong on every row.

**Impact had it shipped**: uploaded certificates would sit 6 hours ahead of every existing
row, and a certificate would read as expiring 6 hours *later* than it does. Silent: nothing
would have errored.

**Residual, accepted**: 4 of the 17 still differ by an hour after the fix. All were issued
while daylight saving was in force, and the legacy system applied the issuance-time offset to
the expiry as well, where converting each timestamp on its own date does not. Mexico abolished
daylight saving in October 2022, so both ends of every future certificate sit at a fixed
offset and the discrepancy cannot recur — confirmed: 5 of 5 post-2022 certificates match
exactly. Replicating the quirk would mean encoding a bug for cases that can no longer occur.

## R7 — Where should timezone data come from?

**Decision**: `zoneinfo` with `tzdata` declared as a dependency.

**Rationale**: `zoneinfo` falls back to the `tzdata` package when the host has no system tz
database — the normal case in slim containers, and always on Windows. Without it the upload
would raise at runtime on exactly the deployment targets most likely to be used.

**Alternative rejected**: a hard-coded −6 offset. Correct only since October 2022 and silently
wrong for any historical date, which is precisely the error class R6 exists to document.
