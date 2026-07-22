# Phase 1 Data Model: Digital Seal Certificates

**No schema change.** `taxpayer_certificate` already existed with all its columns. What
changes is which of them the API reads, and in what convention two of them are written.

## Certificate (`taxpayer_certificate`)

| Field | Stored | Readable via API | Notes |
|-------|--------|------------------|-------|
| `taxpayer_certificate_id` | yes | yes | The authority's certificate number. Natural primary key, derived from the certificate itself on registration |
| `taxpayer` | yes | yes | Owning taxpayer entity |
| `valid_from` | yes | yes | Start of the validity window — **local time**, see below |
| `valid_to` | yes | yes | End of the validity window — **local time** |
| `status` | yes | yes | Lifecycle state |
| `certificate_data` | yes | **never** | The certificate file |
| `key_data` | yes | **never** | The private signing key |
| `key_password` | yes | **never** | Unencrypted, by explicit decision |

The three secret columns are excluded from the query, not filtered from the response. Nothing
that serves a read causes them to be read from the database.

## Validity window convention

Stored as **Mexico City local time**, matching every record the previous system wrote. The
certificate itself states its window in UTC; conversion happens at registration.

| Population | Convention agreement |
|------------|---------------------|
| Registered by this interface | Local time, converted per timestamp |
| Registered previously, issued after Oct 2022 | Identical — verified on all 5 |
| Registered previously, issued before Oct 2022 | `valid_from` identical; `valid_to` differs by one hour on 4 records, where the previous system applied the issuance-time offset to the expiry |

The historical divergence is left in place: correcting it would edit fiscal records to resolve
an inconsistency that cannot recur, daylight saving having been abolished.

## Derived at registration, never accepted from the request

| Value | Source |
|-------|--------|
| Certificate number | The certificate's serial |
| Validity window | The certificate's stated window, converted to local time |
| Owner | The certificate's subject, cross-checked against the entity named in the request |

An owner that cannot be read from the certificate defers to the entity named in the request —
an unreadable owner is not evidence of a wrong one.

## Relationships

Each certificate belongs to exactly one taxpayer entity, which must exist at registration.
Certificates are among the records that block deleting a taxpayer entity (feature 007, FR-006).

## State transitions

A certificate is registered `ACTIVE`. Nothing in this feature transitions it; expiry is a
property of the validity window, not a state change.
