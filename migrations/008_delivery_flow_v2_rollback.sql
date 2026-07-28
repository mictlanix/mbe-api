-- Rollback for 008_delivery_flow_v2.sql
--
-- Reverses the schema in inverse order and reconstructs the legacy booleans from the status
-- columns. Run by hand; the migration runner never applies `_rollback` files.
--
-- WHAT THIS CANNOT RESTORE. The forward migration settles every delivery order into a terminal
-- status, so the distinction between "completed and awaiting approval" and "abandoned" is gone.
-- Both mapped to CANCELLED, and both come back as `cancelled = 1`. Restoring the pre-008
-- distribution requires the backup taken before the migration ran -- 17,775 rows that were
-- `completed = 1, cancelled = 0` will NOT return to that state.
--
-- Likewise the 1,219 `serial = 0` placeholders come back as NULL, not 0.
--
-- Rows created after 008 (proof of delivery, events, stops, child delivery orders) are dropped.

-- ---------------------------------------------------------------------------
-- Step 6 -- remove the in-transit warehouse
-- ---------------------------------------------------------------------------

DELETE FROM `warehouse` WHERE `code` = 'IN-TRANSIT';

-- ---------------------------------------------------------------------------
-- Step 5 -- deliveries_itinerary_detail
-- ---------------------------------------------------------------------------

ALTER TABLE `deliveries_itinerary_detail` ADD COLUMN `deliveries_itinerary` INT(11) NULL;

UPDATE `deliveries_itinerary_detail` d
  JOIN `deliveries_itinerary_stop` s
    ON s.`deliveries_itinerary_stop_id` = d.`deliveries_itinerary_stop`
  SET d.`deliveries_itinerary` = s.`deliveries_itinerary`;

ALTER TABLE `deliveries_itinerary_detail` DROP KEY `ix_itinerary_detail_stop`;

ALTER TABLE `deliveries_itinerary_detail`
  DROP COLUMN `deliveries_itinerary_stop`,
  DROP COLUMN `reason_code`,
  DROP COLUMN `returned_quantity`,
  DROP COLUMN `delivered_quantity`,
  DROP COLUMN `sent_quantity`,
  CHANGE COLUMN `committed_quantity` `quantity` DECIMAL(20,6) NOT NULL;

-- Restore the foreign key the forward migration dropped, so the rolled-back schema matches
-- what was there before rather than merely having the right columns.
ALTER TABLE `deliveries_itinerary_detail`
  ADD CONSTRAINT `FK_deliveries_itinerary_detail_deliveries_itinerary`
  FOREIGN KEY (`deliveries_itinerary`) REFERENCES `deliveries_itinerary` (`deliveries_itinerary_id`);

-- ---------------------------------------------------------------------------
-- Step 4 -- deliveries_itinerary
-- ---------------------------------------------------------------------------

ALTER TABLE `deliveries_itinerary` DROP KEY `ix_deliveries_itinerary_status_date`;

ALTER TABLE `deliveries_itinerary`
  ADD COLUMN `cancelled` TINYINT(1) NOT NULL DEFAULT 0,
  ADD COLUMN `completed` TINYINT(1) NOT NULL DEFAULT 0;

-- ItineraryStatus: 0 = OPEN, 1 = DEPARTED, 2 = CLOSED, 3 = CANCELLED
UPDATE `deliveries_itinerary`
  SET `cancelled` = CASE WHEN `status` = 3 THEN 1 ELSE 0 END,
      `completed` = CASE WHEN `status` = 2 THEN 1 ELSE 0 END;

ALTER TABLE `deliveries_itinerary`
  DROP COLUMN `return_time`,
  DROP COLUMN `departure_time`,
  DROP COLUMN `status`;

-- ---------------------------------------------------------------------------
-- Step 3 -- drop the new tables (stops first: detail no longer references them)
-- ---------------------------------------------------------------------------

DROP TABLE `deliveries_itinerary_stop`;
DROP TABLE `delivery_order_event`;
DROP TABLE `proof_of_delivery`;

-- ---------------------------------------------------------------------------
-- Step 2 -- delivery_order_detail
-- ---------------------------------------------------------------------------

ALTER TABLE `delivery_order_detail`
  DROP COLUMN `warehouse`,
  DROP COLUMN `returned_quantity`,
  DROP COLUMN `delivered_quantity`,
  DROP COLUMN `committed_quantity`;

-- ---------------------------------------------------------------------------
-- Step 1 -- delivery_order
-- ---------------------------------------------------------------------------

ALTER TABLE `delivery_order`
  ADD COLUMN `completed` TINYINT(1) NOT NULL DEFAULT 0,
  ADD COLUMN `cancelled` TINYINT(1) NOT NULL DEFAULT 0,
  ADD COLUMN `confirmed` TINYINT(1) NULL,
  ADD COLUMN `delivered` TINYINT(1) NULL DEFAULT 0,
  ADD COLUMN `picked_up` TINYINT(1) NOT NULL DEFAULT 0;

-- DeliveryOrderStatus: 4 = PICKED_UP, 7 = DELIVERED, 8 = PARTIALLY_DELIVERED, 10 = CANCELLED.
-- Anything not terminal in the old model is reported as completed and not cancelled.
UPDATE `delivery_order`
  SET `cancelled` = CASE WHEN `status` = 10 THEN 1 ELSE 0 END,
      `delivered` = CASE WHEN `status` IN (7, 8) THEN 1 ELSE 0 END,
      `picked_up` = CASE WHEN `status` = 4 THEN 1 ELSE 0 END,
      `completed` = CASE WHEN `status` = 0 THEN 0 ELSE 1 END,
      `confirmed` = CASE WHEN `status` IN (0, 1) THEN 0 ELSE 1 END;

ALTER TABLE `delivery_order`
  DROP COLUMN `proof_of_delivery`,
  DROP COLUMN `rejection_reason`,
  DROP COLUMN `parent_delivery_order`,
  DROP COLUMN `fulfillment_type`,
  DROP COLUMN `status`;

ALTER TABLE `delivery_order` DROP KEY `uq_delivery_order_folio`;

-- The placeholder is not recoverable: 008 turned 1,219 zeros into NULLs and cannot tell them
-- from folios that were legitimately unassigned. They come back as 0 so the column can be
-- NOT NULL again.
UPDATE `delivery_order` SET `serial` = 0 WHERE `serial` IS NULL;
ALTER TABLE `delivery_order` MODIFY COLUMN `serial` INT(11) NOT NULL;
