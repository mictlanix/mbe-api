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
