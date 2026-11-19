-- 036 Store the delivery order a stop was added for (#343)
--
-- `POST /delivery-itineraries/{id}/stops` takes the delivery order the stop is for, checks it
-- (it exists, is a delivery, is IN_PREPARATION) and then dropped it: `deliveries_itinerary_stop`
-- had no column to keep it. A stop with no lines committed yet could not be traced back to its
-- order, and so had no customer or ship-to to show. This adds the column, nullable, with a foreign
-- key to `delivery_order` and its index.
--
-- The column records the order the stop was opened for, not a rule: a stop may still carry lines
-- of other orders (06a section 4, "one or more per stop"), and the API returns those as well.
--
-- MEASURED against mbe_dev 2026-11-18: 4,916 stops, every one the synthetic stop migration 008
-- created per legacy itinerary. Their lines span 0 to 21 delivery orders and 1 to 19 customers:
-- 3,699 hold one order, 1,208 hold several (887 of them several customers), and 9 hold none. No
-- itinerary was created after 2026-09-29. NO BACKFILL: a synthetic stop that mixes orders and
-- customers has no one order it was opened for, and picking one would invent a customer for the
-- stop. Every existing row stays NULL, which the API reports as "no stored order".
--
-- The current system has no stop table at all (008 created it), so it neither reads nor writes
-- this column.
--
-- Idempotent. MariaDB 10.11.
-- Rollback: 036_itinerary_stop_delivery_order_rollback.sql

ALTER TABLE `deliveries_itinerary_stop`
  ADD COLUMN IF NOT EXISTS `delivery_order` INT(11) NULL DEFAULT NULL;

ALTER TABLE `deliveries_itinerary_stop`
  ADD KEY IF NOT EXISTS `deliveries_itinerary_stop_delivery_order_idx` (`delivery_order`);

ALTER TABLE `deliveries_itinerary_stop`
  ADD CONSTRAINT `deliveries_itinerary_stop_delivery_order_fk`
  FOREIGN KEY IF NOT EXISTS (`delivery_order`)
  REFERENCES `delivery_order` (`delivery_order_id`) ON DELETE NO ACTION ON UPDATE NO ACTION;
