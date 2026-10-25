-- Rollback for 024_supplier_code_unique.sql
--
-- Drops the unique index. The renamed codes are NOT restored: putting the duplicates back would
-- only reintroduce the ambiguity, and the forward migration's header lists every rename should one
-- need undoing by hand.
--
-- Afterwards uniqueness is enforced only by the API's check before insert and update.
--
-- MariaDB 10.11.

DROP INDEX IF EXISTS `supplier_code_uq` ON `supplier`;
