-- Rollback for 020_sales_order_origin.sql
--
-- Drops `sales_order`.`origin`.
--
-- WHAT IS LOST. Every origin recorded since the forward migration ran, and it is not recoverable
-- from anything else in the schema -- that is the whole premise of issue #209. `point_sale` is
-- populated on every row whichever workflow raised it, `fulfillment_intent` answers a different
-- question, and `sales_quote` identifies only the orders that came from a quote. Nothing else on
-- the row remembers which workflow captured it.
--
-- As in 017's rollback there is no precondition to check: this drop cannot fail on data or corrupt
-- a neighbouring value. The loss is total and unconditional instead, which is easier to reason
-- about and worse to do by accident. Before running it, if the rows matter:
--
--   SELECT `sales_order_id`, `origin` FROM `sales_order` WHERE `origin` IS NOT NULL;
--
-- The model still declares the column afterwards, so revert the application code with it or every
-- read of `sales_order` raises "Unknown column". The two filter parameters on GET /sales-orders go
-- with it; a client still sending them gets a 500, not a 422, until the code is reverted too.
--
-- MariaDB 10.11.

ALTER TABLE `sales_order`
  DROP COLUMN IF EXISTS `origin`;
