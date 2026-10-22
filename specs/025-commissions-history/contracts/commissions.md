# Contract: Commissions

All routes are under `/api/v1/commissions`, authenticated, and gated by `COMMISSIONS_BY_SALES_PERSON` (112): Read for GET, Create for POST of a settings row, Update for PUT, `/close`, `/reopen` and `/pay`, and Delete for DELETE. A refusal is `403 Insufficient privileges`, and an unknown id is `404`. Every settings POST, PUT and DELETE writes an `incidence` (source 12).

## Settings

| Method | Path | Body | Notes |
|---|---|---|---|
| GET | `/labels` | — | `[{commission_id, name, commission_rate, comment}]` |
| POST | `/labels` | `{name, commission_rate, comment?}` | rate 0–1 → 422 |
| PUT / DELETE | `/labels/{commission_id}` | partial / — | DELETE → 409 if products or agent rates use it |
| GET / POST | `/participations` | `{name}` | |
| PUT / DELETE | `/participations/{id}` | | DELETE → 409 if in use |
| GET | `/agents` | — | `[{employee, name, scheme}]` |
| POST | `/agents` | `{employee, scheme?: 1\|2}` | default 1; 404 unknown employee; 409 already an agent |
| PUT | `/agents/{employee}` | `{scheme: 1\|2}` | takes effect at the next computation |
| DELETE | `/agents/{employee}` | — | 409 if the agent still has rates |
| GET | `/agent-rates?salesperson=&commission=` | — | |
| POST | `/agent-rates` | `{salesperson, commission, commission_participation, participation_rate}` | 409 duplicate triple; 422 share outside 0–1 or not an agent |
| PUT / DELETE | `/agent-rates/{id}` | | |
| GET | `/product-labels?commission=&product=&skip=&limit=` | — | paged |
| PUT / DELETE | `/product-labels/{product}` | `{commission}` / — | set or change; remove |

## Periods

| Method | Path | Notes |
|---|---|---|
| GET | `/periods` | months that have a row: `[{year, month, status, closed_at, paid_at, total}]` |
| GET | `/periods/{year}/{month}?salesperson=` | an open month is computed live |
| POST | `/periods/{year}/{month}/close` | 409 `Period has not ended`; 409 `The previous period is not closed` (after the first close); 409 already closed or paid |
| POST | `/periods/{year}/{month}/reopen` | 409 unless it is the latest closed month and not paid |
| POST | `/periods/{year}/{month}/pay` | 409 unless closed |

`month` must be 1–12 (422).

`GET /periods/{year}/{month}` returns:

```json
{
  "year": 2026, "month": 9, "status": "closed", "final": true,
  "closed_by": 7, "closed_at": "...", "reopened_by": null, "reopened_at": null, "paid_by": null, "paid_at": null,
  "totals": [{"salesperson": 17, "label": 1, "label_name": "CONCRETOS", "lines": 40, "commission": "1234.56"}],
  "lines": [{
    "scheme": 2, "movement": "sale",
    "sales_order": 336924, "sales_order_detail": 1, "customer_refund_detail": null,
    "order_date": "...", "paid_date": "...",
    "customer": 9149, "customer_name": "…", "product": 12, "product_name": "…",
    "label": 1, "label_name": "CONCRETOS", "label_rate": "0.025000",
    "salesperson": 17, "order_salesperson": 17, "customer_salesperson": 17,
    "participation": 1, "participation_name": "CLIENTE VITALICIO", "participation_rate": "1.000000",
    "rate": "0.02500000", "price": "2143.10", "quantity": "7.0000", "total": "15001.70",
    "full_commission": "375.04", "share": "0.400000", "commission": "150.02"
  }]
}
```

- **`movement`:** `sale` or `refund`.
- **`share`:** null except on scheme 2 sales.
- **`commission`:** what this month pays for that row. For a scheme 2 sale it is the month's increment, not the cumulative.
