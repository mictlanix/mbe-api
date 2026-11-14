-- 033 One live application per (order, payment), so a reversed one can be applied again (#309)
--
-- `sales_order_payment` carries the legacy key `UNIQUE (sales_order, customer_payment)`. A reversed
-- application is kept as evidence with `cancelled = 1` (spec 011, FR-045), so it still holds the
-- pair: applying the same payment to the same order again failed with a duplicate key, which the
-- API answers with a generic 409 `resource.conflict`. Reversing an application recorded with the
-- wrong amount and applying the right one, the most common correction, was impossible.
--
-- The rule the key stood for, one application per pair, still holds for live applications. The
-- API now refuses a second live one with `customer_payment.already_applied` (409) before the
-- insert; this narrows the key to match:
--
-- 1. `live`, a virtual generated column: 1 while the application is live, NULL once reversed.
-- 2. The legacy key is replaced by `UNIQUE (sales_order, customer_payment, live)`. A unique key
--    treats NULLs as distinct, so any number of reversed applications can share a pair, but only
--    one live one. MariaDB has no partial indexes; this is its equivalent.
--
-- The foreign keys keep their own indexes: `sales_order_payment_sales_order_idx` and
-- `sales_order_payment_customer_payment_idx` exist beside the legacy key and are untouched.
--
-- No precondition: the legacy key allowed no duplicate pair at all, so no pair has two live rows.
--
-- The current system's NHibernate mappings name their columns, so it never writes `live`; once
-- this runs it could also apply a payment to an order again after a reversal there.
--
-- Idempotent. MariaDB 10.11 (indexes on virtual columns require 10.2+).
-- Rollback: 033_payment_application_live_unique_rollback.sql

ALTER TABLE `sales_order_payment`
  ADD COLUMN IF NOT EXISTS `live` TINYINT(1) AS (IF(`cancelled` = 0, 1, NULL)) VIRTUAL,
  DROP KEY IF EXISTS `sales_order_payment_sales_order_customer_payment_idx`,
  ADD UNIQUE KEY IF NOT EXISTS `sales_order_payment_live_uq` (`sales_order`, `customer_payment`, `live`);
