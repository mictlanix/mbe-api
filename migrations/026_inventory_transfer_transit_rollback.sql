-- Rollback for 026_inventory_transfer_transit.sql
--
-- Drops the in-transit flag and the received quantities. The ledger entries the transfers posted
-- are kept, so stock is unchanged; what is lost is which transfers are still in transit and how
-- much of each line has been received. Transfers dispatched through the API then read as received
-- in full.
--
-- The model still declares both columns afterwards, so revert the application code with it, or
-- every read of a transfer raises "Unknown column".
--
-- MariaDB 10.11.

ALTER TABLE `inventory_transfer_detail` DROP COLUMN IF EXISTS `quantity_received`;

ALTER TABLE `inventory_transfer` DROP COLUMN IF EXISTS `in_transit`;
