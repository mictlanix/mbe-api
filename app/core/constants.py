"""Values fixed by the system, not by configuration.

Anything here is a constant precisely because a deployment getting it wrong is worse than a
deployment being unable to change it.
"""

from typing import Final

#: The employee recorded as the actor for automated actions — the expiry sweep, and anything else
#: that acts without a person behind it.
#:
#: A constant rather than a setting: `sales_order.updater` is an enforced foreign key, so a wrong
#: value is not a preference, it is a run that fails partway through with error 1452 after some
#: orders have already been cancelled. There is nothing a deployment gains by choosing it.
#:
#: Negative on purpose. Employee 0 cannot exist — `NO_AUTO_VALUE_ON_ZERO` is not in `sql_mode`, so
#: an explicit 0 becomes the next auto-increment value — and a high id such as 999999 would push
#: employee `AUTO_INCREMENT` past it, numbering every real employee thereafter from 1000000.
#: InnoDB only advances the counter for values above it, so -1 leaves normal numbering untouched.
SYSTEM_EMPLOYEE_ID: Final[int] = -1


#: The price list holding each product's average cost rather than a sale price. Read when
#: snapshotting a sales-order line's cost; refused wherever a sale price list is expected.
#:
#: A constant rather than a setting because the monolith that computes those averages writes to
#: this id. Point the API elsewhere and nothing fails — every line snapshots `cost` from a sale
#: list, and every margin booked after is wrong with nothing marking when it started.
#:
#: Zero is observed, not chosen, which is why it is not negative like `SYSTEM_EMPLOYEE_ID` above.
#: That row this repository created (migration 010) and could number out of band. This one already
#: exists: `price_list_id` 0 is "Costo", carrying 21,591 `product_price` rows, and spec 011's
#: source document calls it "cost price list, id=0" (research R3). Renumbering means rewriting
#: those rows while the monolith keeps writing to 0.
#:
#: The cost of 0 is that it is falsy, so nothing may test this id for truthiness —
#: `delete_price_list` tests `replacement_id is not None` for that reason (spec 015).
COST_PRICE_LIST_ID: Final[int] = 0
