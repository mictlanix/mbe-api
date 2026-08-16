-- 018 Renumber delivery_order.fulfillment_type onto the unified scale -- issue #170
--
-- Two columns describe how goods reach the customer and, until this migration, numbered it
-- differently:
--
--   delivery_order.fulfillment_type   0 = delivery       1 = counter pickup
--   sales_order.fulfillment_intent    0 = pickup         1 = delivery        2 = mixed   (017)
--
-- So `0` meant opposite things on two adjacent, similarly named columns, and any code converting
-- between them had to map by name and be right about it every time. This unifies them on the
-- sales-order scale, which leads with pickup because that is the ordinary counter sale: 310,609 of
-- 335,763 sales orders -- 92.5% -- never produced a delivery order at all.
--
-- After this there is one vocabulary, `FulfillmentType`, and a value read from either column means
-- the same thing.
--
-- MEASURED against the deployment database 2026-08-16, before:
--
--   delivery_order rows                                        28,225
--     fulfillment_type = 0  (delivery, old scale)              23,595
--     fulfillment_type = 1  (counter pickup, old scale)         4,630
--     anything else                                                 0
--
-- After, the same rows must read 4,630 zeros and 23,595 ones -- but step 3 does NOT hardcode those
-- figures. It records the counts before the swap and compares against them afterwards, so the check
-- holds in any environment and on any day, rather than only against the snapshot above.
--
-- ############################################################################
-- #  THIS MIGRATION IS NOT IDEMPOTENT. RUNNING IT TWICE SWAPS THE VALUES BACK. #
-- ############################################################################
--
-- Every other migration in this directory can be re-run harmlessly; this one cannot, because a swap
-- is its own inverse. The runner records applied migrations in `schema_migrations` and will not
-- repeat it, so the ordinary path is safe -- the hazard is a human running the file by hand to
-- "make sure it took". Do not. Check instead:
--
--   SELECT fulfillment_type, COUNT(*) FROM delivery_order GROUP BY 1;
--
-- 4,630 zeros means it has been applied. 23,595 zeros means it has not.
--
-- WHY THIS IS SAFE FROM THE LEGACY APPLICATION, checked rather than assumed. `fulfillment_type`
-- does not exist in `docs/mbe_schema.sql`: migration 008 added it and derived it from the legacy
-- `picked_up` boolean. `mbe/Model/DeliveryOrder.cs` maps `picked_up` and has no property for this
-- column, so the legacy application neither reads nor writes it and cannot observe the renumbering.
--
-- THE DEFAULT MOVES WITH THE VALUES, which is the part that is easy to miss. 008 created the column
-- `NOT NULL DEFAULT 0`, and that default is load-bearing precisely because the legacy application
-- does not know the column -- an INSERT from it supplies no value and takes the default. Today that
-- default means DELIVERY. Renumbering without touching it would silently turn every future legacy
-- insert into a PICKUP. Step 2 moves it to 1 so an omitted value keeps the meaning it has always
-- had.
--
-- (That the legacy application may insert a `picked_up = 1` row and still get the delivery default
-- is a pre-existing gap from 008, not something this migration introduces or repairs.)
--
-- MariaDB 10.11. Rollback: 018_fulfillment_type_renumber_rollback.sql

-- ---------------------------------------------------------------------------
-- Step 1 -- precondition: nothing outside the two known values
-- ---------------------------------------------------------------------------
--
-- The swap below maps 0->1 and 1->0 and leaves anything else untouched, so a stray value would
-- survive into a scale where it means something different. A CHECK on a temporary table rather than
-- SIGNAL, as in 011, 012, 015 and 016: the runner splits on semicolons, so a compound statement
-- would be torn apart.

CREATE TEMPORARY TABLE `_018_precondition` (
  `only_known_values` TINYINT NOT NULL,
  CONSTRAINT `chk_018_known_values` CHECK (`only_known_values` = 1)
);

INSERT INTO `_018_precondition` (`only_known_values`)
SELECT IF((SELECT COUNT(*) FROM `delivery_order`
           WHERE `fulfillment_type` NOT IN (0, 1)) = 0, 1, 0);

DROP TEMPORARY TABLE `_018_precondition`;

-- The counts as they stand, so step 3 can verify against what was actually here rather than
-- against the snapshot in the header. Kept for the rest of the session; the runner uses one
-- connection, which is what makes a temporary table usable across statements.

CREATE TEMPORARY TABLE `_018_before` (
  `zeros` INT NOT NULL,
  `ones`  INT NOT NULL,
  `total` INT NOT NULL
);

INSERT INTO `_018_before` (`zeros`, `ones`, `total`)
SELECT SUM(`fulfillment_type` = 0), SUM(`fulfillment_type` = 1), COUNT(*) FROM `delivery_order`;

-- ---------------------------------------------------------------------------
-- Step 2 -- the swap, and the default that has to move with it
-- ---------------------------------------------------------------------------
--
-- One statement, so no row is ever visible on a mixed scale. CASE rather than two UPDATEs: running
-- `SET x = 1 WHERE x = 0` then `SET x = 0 WHERE x = 1` would set every row to 0.

UPDATE `delivery_order`
   SET `fulfillment_type` = CASE `fulfillment_type` WHEN 0 THEN 1 WHEN 1 THEN 0
                            ELSE `fulfillment_type` END;

ALTER TABLE `delivery_order`
  MODIFY COLUMN `fulfillment_type` SMALLINT NOT NULL DEFAULT 1
  COMMENT '0=pickup 1=delivery (FulfillmentType; MIXED=2 is sales_order-only) (#170)';

-- ---------------------------------------------------------------------------
-- Step 3 -- postcondition: the counts swapped, and nothing was lost
-- ---------------------------------------------------------------------------
--
-- Asserted rather than eyeballed, because a half-applied renumbering is indistinguishable from a
-- correct one by inspection: both are a table full of 0s and 1s.

CREATE TEMPORARY TABLE `_018_postcondition` (
  `counts_swapped` TINYINT NOT NULL,
  CONSTRAINT `chk_018_swapped` CHECK (`counts_swapped` = 1)
);

INSERT INTO `_018_postcondition` (`counts_swapped`)
SELECT IF((SELECT SUM(`fulfillment_type` = 0) FROM `delivery_order`) = (SELECT `ones`  FROM `_018_before`)
      AND (SELECT SUM(`fulfillment_type` = 1) FROM `delivery_order`) = (SELECT `zeros` FROM `_018_before`)
      AND (SELECT COUNT(*) FROM `delivery_order`)                    = (SELECT `total` FROM `_018_before`),
      1, 0);

DROP TEMPORARY TABLE `_018_postcondition`;
DROP TEMPORARY TABLE `_018_before`;
