-- Rollback for 009_drop_sales_order_detail_delivery.sql
--
-- Restores the column with the value every row held before it was dropped: 0 on all 910,891 rows.
-- Nothing is lost, because nothing varied.
--
-- Note that restoring the column does not restore a consumer. Spec 012 bounds a delivery order by
-- coverage, so re-adding the filter to `delivery_order_service` would break delivery-order
-- creation, the sales-order `delivered` write-back and the derived coverage figures — see the
-- forward migration's header.

ALTER TABLE `sales_order_detail` ADD COLUMN `delivery` TINYINT(1) NOT NULL DEFAULT 0;
