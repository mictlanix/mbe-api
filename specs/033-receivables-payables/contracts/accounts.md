# Contract: Accounts Receivable and Accounts Payable

Both sides have the same eight routes under `/api/v1`. `{side}` is `accounts-receivable` (customers, privilege 18) or `accounts-payable` (suppliers, privilege 19). Every route requires a login, and read on the side's privilege; otherwise it returns 401 or 403.

## GET /{side}

The summary: one row per party with an open document in scope.

**Query parameters:**

| Parameter | Type | Notes |
|---|---|---|
| `party` | int | One customer or supplier |
| `overdue` | bool | Only parties with `overdue > 0` |
| `facility` | int | AR only, for callers who can read 101 or are administrators. Otherwise 403 when it differs from their own facility |
| `sales_agent` | int | AR only |
| `sort` | `owed` \| `overdue` \| `oldest` | Default `owed`, descending (`oldest` ascending) |
| `skip`, `limit` | int | Default 0 and 50; maximum 200 |

**200** `ListResponse[PartySummary]`:

```json
{
  "items": [{
    "party": 11202, "name": "…", "sales_agent": 31, "sales_agent_name": "…",
    "owed": "40000.00", "overdue": "12000.00",
    "buckets": {"current": "28000.00", "1-30": "12000.00", "31-60": "0.00", "61-90": "0.00", "90+": "0.00"},
    "oldest": "2026-08-14T10:12:00",
    "credit": "500.00", "net": "39500.00",
    "credit_limit": "50000.00", "credit_days": 30,
    "owed_company": "40000.00", "utilization": "0.8000", "available_credit": "10000.00"
  }],
  "total": 136,
  "scope": {"facility": 54, "all_facilities": false},
  "totals": {"owed": "…", "overdue": "…", "buckets": {"…": "…"}}
}
```

- `sales_agent` and `sales_agent_name` appear on AR only.
- `scope.facility` is null when `all_facilities` is true.
- **AP** adds `"cutoff": "2027-01-01T00:00:00"` or `null`. With a null cutoff the list is empty.

## GET /{side}/{party_id}

The drill-down.

**200** `PartyDetail`:

```json
{
  "summary": { "…PartySummary…": "…" },
  "documents": [{
    "id": 912345, "serial": 4410, "date": "…", "due_date": "…", "days_past_due": 12, "bucket": "1-30",
    "facility": 54, "facility_code": "CMZ", "salesperson": 77,
    "linked": [{"id": 30111, "label": "A1234"}],
    "currency": "MXN", "exchange_rate": "1.0000",
    "total": "15000.00", "deductions": "0.00", "applied": "3000.00", "balance": "12000.00"
  }],
  "payments": [{
    "id": 551, "serial": 88, "date": "…", "method": 3, "currency": "MXN",
    "amount": "500.00", "unapplied": "500.00"
  }]
}
```

- **`documents`:** open documents in scope, oldest due first; documents without a due date come last.
- **`payments`:** the party's payments with credit above 0.01, the ones that can be applied (FR-020). On AR they're limited to the caller's facility unless they're an administrator or hold PaymentsEditor read (#271's rule). On AP they come from spec 032 and cover every facility.
- **AP shape:**
  - `serial` is null, and `facility` and `salesperson` are omitted;
  - `linked` lists confirmed receipts, labelled with facility code and serial;
  - `deductions` is the returns figure, always 0.
- **404** when the party doesn't exist.
- A party with nothing open in scope returns `documents: []` and a zero summary.

## GET /{side}/{party_id}/statement

The PDF statement (`application/pdf`, `estado-de-cuenta-{party_id}.pdf`). It always covers all facilities (FR-023). It's available for any existing party, including one with only credit or nothing open.

**404** when the party doesn't exist.

## POST /{side}/{party_id}/statement/email

```json
{"contacts": [123, 456], "to": ["cobranza@cliente.mx"]}
```

**Rules:**
- At least one recipient, at most 10, after duplicates are removed.
- Each contact must be one of the party's contacts and have an email address.
- Typed addresses are validated the way quotes' are.

**200**

```json
{"sent_at": "2026-11-03T12:01:44", "to": ["a@x.mx", "b@x.mx", "cobranza@cliente.mx"]}
```

The send records one incidence: source 7 (customer) or 16 (supplier), `instance_id` = the party, reason "Account statement emailed", context "To: …".

**Errors:**
- **404:** the party doesn't exist.
- **422:** no recipients, more than 10, a contact of another party or without an address, or an invalid address.
- **503:** no mailer is configured. Nothing is recorded.
- **502:** the mail server refused the message. Nothing is recorded (quotes' behaviour).

## Changed: existing routes

**Customer payment responses** (`GET /customer-payments`, `/{id}`, `/search`, `/unverified`): `unapplied` = amount − Σ(applied + change) over live applications. Today it doesn't subtract change.

**`GET /customer-payments/{id}/ticket`** (#275): the balance reads the same `unapplied`. The printed figure doesn't change.

**`POST /customer-payments/{id}/applications`:**
- **New 409:** "Sales order is already settled", when the order is flagged paid or its balance is 0.01 or less.
- **New 422:** "Amount X exceeds the order balance of Y".
- **Changed 422:** the unapplied check compares `amount + amount_change` with the unapplied amount, which now subtracts change.
- **New 409:** "The order is busy, retry", on lock contention.
- The other refusals are unchanged.

**`POST /customer-payments/{id}/applications/{application_id}/reverse`:** clears `paid` only on a crossing (R3).

**`GET /customer-payments/outstanding-orders`:**
- Lists only orders with a balance above 0.01.
- `balance` now deducts credit notes.
- Scope and search are unchanged.

**Order responses** (`/sales-orders` lists and detail): `balance` deducts credit notes.

**Credit check** (sales order create and confirm):
- The hold counts overdue orders with a balance above 0.01, not orders flagged unpaid.
- The limit check sums the shared balance.
- The messages are unchanged.

**`POST /fiscal-documents/{id}/confirm`** for a credit note: settles the orders on its invoice (R3).

## Job

```text
python -m app.jobs.settle_paid_flags            # dry run: prints order, date, balance, new zero time
python -m app.jobs.settle_paid_flags --apply    # writes, one incidence per order
```
