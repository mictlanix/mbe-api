-- Rollback of 033 One live application per (order, payment) -- #309
--
-- Restores the legacy key `UNIQUE (sales_order, customer_payment)` and drops `live`.
--
-- FAILS while any pair has more than one row, live or reversed: every re-application made since
-- 033 is such a pair, and the ALTER stops with error 1062 and changes nothing. Find them with
--
--   SELECT `sales_order`, `customer_payment`, COUNT(*) FROM `sales_order_payment`
--   GROUP BY `sales_order`, `customer_payment` HAVING COUNT(*) > 1;
--
-- Which row goes is not mechanical: the reversed ones are the audit trail of a correction, and
-- the live one is money applied. Decide it before rolling back rather than letting a script pick.
--
-- MariaDB 10.11.

ALTER TABLE `sales_order_payment`
  DROP KEY IF EXISTS `sales_order_payment_live_uq`,
  ADD UNIQUE KEY IF NOT EXISTS `sales_order_payment_sales_order_customer_payment_idx` (`sales_order`, `customer_payment`),
  DROP COLUMN IF EXISTS `live`;
