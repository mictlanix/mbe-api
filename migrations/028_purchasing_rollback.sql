-- Rollback of 028 Purchasing -- spec 031
--
-- Drops the cost history, the request-line key on PO lines, and the approver columns. Every cost
-- change and every recorded approver is lost with them; export them first if they matter:
--   SELECT * FROM product_cost_change;
--   SELECT purchase_request_id, approver, approval_time FROM purchase_request
--     WHERE approver IS NOT NULL;
--
-- Costs themselves (`product_price`, list 0) stay as the API last set them. The models still
-- declare the table and columns afterwards, so revert the application code with it.
--
-- MariaDB 10.11.

DROP TABLE IF EXISTS `product_cost_change`;

ALTER TABLE `purchase_order_detail`
    DROP FOREIGN KEY IF EXISTS `purchase_order_detail_request_detail_fk`;

DROP INDEX IF EXISTS `purchase_order_detail_request_detail_idx` ON `purchase_order_detail`;

ALTER TABLE `purchase_request` DROP FOREIGN KEY IF EXISTS `purchase_request_approver_fk`;

DROP INDEX IF EXISTS `purchase_request_approver_idx` ON `purchase_request`;

ALTER TABLE `purchase_request`
    DROP COLUMN IF EXISTS `approval_time`,
    DROP COLUMN IF EXISTS `approver`;
