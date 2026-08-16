-- Rollback for 018_fulfillment_type_renumber.sql
--
-- Puts `delivery_order`.`fulfillment_type` back on the pre-unification scale: 0 = delivery,
-- 1 = counter pickup, default 0.
--
-- The swap is its own inverse, so this file is the forward migration with the default moved the
-- other way. That symmetry is the reason to read carefully rather than the reason to relax: RUNNING
-- EITHER FILE TWICE UNDOES IT. Check the state before running anything:
--
--   SELECT fulfillment_type, COUNT(*) FROM delivery_order GROUP BY 1;
--
-- On the unified scale most rows are 1 (delivery). On the old scale most rows are 0 (delivery).
-- "Most rows are delivery" is true either way -- it is the NUMBER that differs, which is exactly
-- why this cannot be eyeballed.
--
-- REVERT THE APPLICATION CODE WITH IT. `FulfillmentType` is PICKUP=0, DELIVERY=1, MIXED=2, and with
-- the database back on the old scale every delivery order would read as its opposite -- a counter
-- pickup resting at APPROVED would be routed to IN_PREPARATION and vice versa, silently, with no
-- error anywhere. This is the one rollback in this directory that corrupts meaning rather than
-- losing data if run alone.
--
-- `sales_order`.`fulfillment_intent` is NOT touched. It has been on this scale since 017, which is
-- its own migration with its own rollback; MIXED has no representation on the old delivery scale
-- and nothing to map to.
--
-- MariaDB 10.11.

-- ---------------------------------------------------------------------------
-- Step 1 -- precondition: nothing outside the two known values
-- ---------------------------------------------------------------------------

CREATE TEMPORARY TABLE `_018r_precondition` (
  `only_known_values` TINYINT NOT NULL,
  CONSTRAINT `chk_018r_known_values` CHECK (`only_known_values` = 1)
);

INSERT INTO `_018r_precondition` (`only_known_values`)
SELECT IF((SELECT COUNT(*) FROM `delivery_order`
           WHERE `fulfillment_type` NOT IN (0, 1)) = 0, 1, 0);

DROP TEMPORARY TABLE `_018r_precondition`;

-- A MIXED row would mean the application wrote one to a delivery order, which
-- `create_from_sales_order` refuses. If this stops here, do not force it: find out what wrote it.

-- ---------------------------------------------------------------------------
-- Step 2 -- the swap back, and the default with it
-- ---------------------------------------------------------------------------

UPDATE `delivery_order`
   SET `fulfillment_type` = CASE `fulfillment_type` WHEN 0 THEN 1 WHEN 1 THEN 0
                            ELSE `fulfillment_type` END;

ALTER TABLE `delivery_order`
  MODIFY COLUMN `fulfillment_type` SMALLINT NOT NULL DEFAULT 0
  COMMENT '0=delivery 1=counter_pickup';
