-- 026 Two-step inventory transfers: the in-transit flag and the received quantity (spec 029, R4)
--
-- A transfer is dispatched from its origin into the origin facility's in-transit warehouse, then
-- received at its destination, in full or in parts, any number of times. Two columns carry that:
--
-- 1. `inventory_transfer.in_transit`: set when the transfer is dispatched, cleared once nothing is
--    outstanding, when the remainder is closed back to the origin, or when the transfer is
--    cancelled. Every existing row gets 0.
-- 2. `inventory_transfer_detail.quantity_received`: 0 from dispatch, raised by each receipt.
--    Existing rows stay NULL, which reads as the full quantity once the transfer is completed.
--
-- No backfill: the current system confirms a transfer in one step, so its 3,318 confirmed
-- transfers read as received in full with the defaults. Its NHibernate mapping names its columns
-- explicitly, so it neither reads nor writes these two, and a transfer it confirms after this
-- migration reads the same way.
--
-- Idempotent. MariaDB 10.11.
-- Rollback: 026_inventory_transfer_transit_rollback.sql

ALTER TABLE `inventory_transfer`
    ADD COLUMN IF NOT EXISTS `in_transit` TINYINT(1) NOT NULL DEFAULT 0 AFTER `cancelled`;

ALTER TABLE `inventory_transfer_detail`
    ADD COLUMN IF NOT EXISTS `quantity_received` DECIMAL(18,4) NULL DEFAULT NULL AFTER `quantity`;
