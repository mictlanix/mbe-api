-- Rollback for 017_sales_order_fulfillment_intent.sql
--
-- Drops `sales_order`.`fulfillment_intent`.
--
-- WHAT IS LOST. Every intent a cashier recorded through the point of sale since the forward
-- migration ran, and it is not recoverable from anything else in the schema -- that is the whole
-- premise of issue #170. `ship_to` can distinguish delivery from counter pickup and cannot express
-- mixed at all, and `partial_deliveries` answers a different question at a different moment.
--
-- Unlike 016's rollback there is no precondition to check, because there is no way for this drop to
-- fail on data or to corrupt a neighbouring value. The loss is total and unconditional instead,
-- which is easier to reason about and worse to do by accident. Before running it, if the rows
-- matter:
--
--   SELECT `sales_order_id`, `fulfillment_intent` FROM `sales_order`
--    WHERE `fulfillment_intent` IS NOT NULL;
--
-- The model still declares the column afterwards, so revert the application code with it or every
-- read of `sales_order` raises "Unknown column".
--
-- MariaDB 10.11.

ALTER TABLE `sales_order`
  DROP COLUMN IF EXISTS `fulfillment_intent`;
