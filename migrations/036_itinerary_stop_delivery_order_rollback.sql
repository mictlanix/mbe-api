-- Rollback of 036 Store the delivery order a stop was added for (#343)
--
-- Drops the foreign key, then its index, then the column. The stops themselves are kept; what is
-- lost is which delivery order each stop created since 036 was opened for. A stop that has lines
-- can still be traced through them, but a stop with no lines committed yet can no longer be traced
-- to any order. Legacy synthetic stops lose nothing: 036 left them NULL.
--
-- MariaDB 10.11.

ALTER TABLE `deliveries_itinerary_stop`
  DROP FOREIGN KEY IF EXISTS `deliveries_itinerary_stop_delivery_order_fk`;

ALTER TABLE `deliveries_itinerary_stop`
  DROP KEY IF EXISTS `deliveries_itinerary_stop_delivery_order_idx`;

ALTER TABLE `deliveries_itinerary_stop` DROP COLUMN IF EXISTS `delivery_order`;
