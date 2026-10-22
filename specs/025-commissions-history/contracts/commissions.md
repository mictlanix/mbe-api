# Contract: Commissions

All routes are under `/api/v1/commissions`, authenticated, and gated by `COMMISSIONS_BY_SALES_PERSON` (112): Read for GET, Create for POST of a settings row, Update for PUT, `/close`, `/reopen` and `/pay`, and Delete for DELETE. A refusal is `403 Insufficient privileges`, and an unknown id is `404`.

## Settings

| Method | Path | Body | Notes |
|---|---|---|---|
| GET | `/labels` | — | `[{commission_id, name, commission_rate, comment}]` |
| POST | `/labels` | `{name, commission_rate, comment?}` | rate 0–1 → 422 |
| PUT | `/labels/{commission_id}` | partial | |
| DELETE | `/labels/{commission_id}` | — | 409 if products or agent rates use it |
| GET/POST | `/participations` | `{name}` | |
| PUT/DELETE | `/participations/{id}` | | 409 if agent rates use it |
| GET | `/agents` | — | `[{employee, name}]` |
| POST | `/agents` | `{employee}` | 404 if the employee is unknown; 409 if already an agent |
| DELETE | `/agents/{employee}` | — | 409 if the agent still has rates |
| GET | `/agent-rates?salesperson=&commission=` | — | `[{commission_salesperson_id, salesperson, commission, commission_participation, participation_rate}]` |
| POST | `/agent-rates` | `{salesperson, commission, commission_participation, participation_rate}` | 409 if the triple exists; 422 if the share is outside 0–1 or the salesperson is not an agent |
| PUT/DELETE | `/agent-rates/{id}` | | |
| GET | `/product-labels?commission=&product=&skip=&limit=` | — | paged `{items: [{product, product_code, product_name, commission}], total}` |
| PUT | `/product-labels/{product}` | `{commission}` | creates or **changes** the label |
| DELETE | `/product-labels/{product}` | — | the product becomes unlabelled |

Every POST, PUT and DELETE above writes an `incidence` (source 12) naming the change.

## Periods

| Method | Path | Notes |
|---|---|---|
| GET | `/periods` | months that have a row: `[{year, month, status, closed_at, paid_at, total}]` |
| GET | `/periods/{year}/{month}?salesperson=` | see below; an open month is computed live |
| POST | `/periods/{year}/{month}/close` | 409 `Period has not ended` before the month ends; 409 if already closed or paid |
| POST | `/periods/{year}/{month}/reopen` | 409 unless closed; a paid period never reopens |
| POST | `/periods/{year}/{month}/pay` | 409 unless closed |

`month` must be 1–12 (422).

`GET /periods/{year}/{month}`:

```json
{
  "year": 2026, "month": 9,
  "status": "closed",
  "final": true,
  "closed_by": 7, "closed_at": "2026-10-02T10:00:00",
  "reopened_by": null, "reopened_at": null,
  "paid_by": null, "paid_at": null,
  "totals": [
    {"salesperson": 17, "label": 1, "label_name": "CONCRETOS", "lines": 40, "commission": "1234.56"}
  ],
  "lines": [
    {
      "movement": "sale",
      "sales_order": 336924, "sales_order_detail": 1, "order_date": "...", "paid_date": "...",
      "customer": 9149, "customer_name": "…",
      "product": 12, "product_name": "…",
      "label": 1, "label_name": "CONCRETOS", "label_rate": "0.025000",
      "salesperson": 17, "order_salesperson": 17, "customer_salesperson": 17,
      "participation": 1, "participation_name": "CLIENTE VITALICIO", "participation_rate": "1.000000",
      "rate": "0.02500000", "price": "2143.10", "quantity": "7.0000", "total": "15001.70", "commission": "375.04"
    }
  ]
}
```

- **`status` and `final`:** `status` is `open`, `closed` or `paid`; `final` is `status != open`.
- **Agent filter:** `salesperson` filters both `lines` and `totals`.
- **Ordering:** lines are ordered by salesperson, paid date, order and line.
