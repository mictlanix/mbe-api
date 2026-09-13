-- Rollback for 019_sales_order_payment_cancelled.sql
--
-- Drops `sales_order_payment`.`cancelled`, which is what mictlanix/mbe#55 does on the MVC side.
-- Running this is therefore not "undoing a mistake" so much as conceding the column after all.
--
-- WHAT IS LOST. Every reversal recorded through `POST /customer-payments/{id}/applications/{id}/
-- reverse`. Nothing else in the schema carries the flag, so an application that was reversed
-- becomes indistinguishable from one that still stands, and every balance, `paid` flag and credit
-- figure derived from the applications silently counts money the supervisor unwound.
--
-- The incidence rows survive and name the employee, the time and the reason (SC-009), so the
-- history remains reconstructable by hand -- but nothing reads them arithmetically.
--
-- Check what the drop would cost before running it:
--
--   SELECT `sales_order_payment_id`, `sales_order`, `amount`
--     FROM `sales_order_payment` WHERE `cancelled` = 1;
--
-- The model still maps the column afterwards, so revert the application code with it or every read
-- of `sales_order_payment` raises "Unknown column" -- which is the whole of issue #212.
--
-- MariaDB 10.11.

ALTER TABLE `sales_order_payment`
  DROP COLUMN IF EXISTS `cancelled`;
