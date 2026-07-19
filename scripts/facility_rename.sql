-- =============================================================================
-- facility_rename.sql
--
-- Renames the `store` entity to `facility` across the live `mbe` database:
--   - RENAMEs table `store` -> `facility`, PK `store_id` -> `facility_id`
--   - Adds `facility.type` INT NOT NULL DEFAULT 0
--     (values: 0 = store | 1 = production_site)
--   - Renames the facility table's own outbound index/constraint names
--     (store_address_idx/fk, store_location_idx/fk, store_taxpayer_idx/fk)
--     to facility_*, even though they don't reference the store/facility FK
--     column itself — they just used the old table name as a prefix
--   - Renames every FK column literally named `store` (in cash_drawer,
--     customer_payment, customer_refund, delivery_order, expense_voucher,
--     fiscal_document, inventory_issue, inventory_receipt, inventory_transfer,
--     payment_method_option, point_sale, sales_order, sales_quote,
--     special_receipt, user_settings, warehouse) to `facility`, along with
--     their indexes and FK constraints
--   - Drops the `production_site` table entirely (its rows are expected to
--     already exist, or be recreated, as `facility` rows with
--     `type = 1` — this script does not migrate data)
--   - Renames `abc_classification.sales_by_store` -> `sales_by_facility`
--
-- Run against the live `mbe` database inside a single session, during a
-- maintenance window. Statements are ordered so FKs referencing `store` are
-- dropped before the table is renamed, and new FKs (referencing `facility`)
-- are added only after `facility` exists in its final shape. Do not run
-- statements out of order or split across sessions.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- (a) Drop FK constraints referencing store(store_id) on every referencing
--     table, before the table is renamed.
-- -----------------------------------------------------------------------------

ALTER TABLE `cash_drawer` DROP FOREIGN KEY `cash_drawer_store_fk`;
ALTER TABLE `customer_payment` DROP FOREIGN KEY `customer_payment_store_fk`;
ALTER TABLE `customer_refund` DROP FOREIGN KEY `customer_refund_store_fk`;
ALTER TABLE `delivery_order` DROP FOREIGN KEY `FK_delivery_order_store`;
ALTER TABLE `expense_voucher` DROP FOREIGN KEY `FK_expense_voucher_store`;
ALTER TABLE `fiscal_document` DROP FOREIGN KEY `fiscal_document_store_fk`;
ALTER TABLE `inventory_issue` DROP FOREIGN KEY `inventory_issue_store_fk`;
ALTER TABLE `inventory_receipt` DROP FOREIGN KEY `inventory_receipt_store_fk`;
ALTER TABLE `inventory_transfer` DROP FOREIGN KEY `inventory_transfer_store_fk`;
ALTER TABLE `payment_method_option` DROP FOREIGN KEY `FK_payment_method_charge_store`;
ALTER TABLE `point_sale` DROP FOREIGN KEY `point_sale_store_fk`;
ALTER TABLE `production_site` DROP FOREIGN KEY `production_site_store_fk`;
ALTER TABLE `sales_order` DROP FOREIGN KEY `sales_order_store_fk`;
ALTER TABLE `sales_quote` DROP FOREIGN KEY `sales_quote_store_fk`;
ALTER TABLE `special_receipt` DROP FOREIGN KEY `FK__store`;
ALTER TABLE `user_settings` DROP FOREIGN KEY `user_settings_store_fk`;
ALTER TABLE `warehouse` DROP FOREIGN KEY `warehouse_store_fk`;

-- -----------------------------------------------------------------------------
-- (b) Rename the table itself.
-- -----------------------------------------------------------------------------

RENAME TABLE `store` TO `facility`;

-- -----------------------------------------------------------------------------
-- (c) Rename the PK column and add the new `type` column.
-- -----------------------------------------------------------------------------

ALTER TABLE `facility`
  CHANGE `store_id` `facility_id` int(11) NOT NULL AUTO_INCREMENT,
  ADD COLUMN `type` int(11) NOT NULL DEFAULT 0;

-- Rename the facility table's own outbound index names (they still say
-- `store_*` even though they don't reference the store/facility FK column).
ALTER TABLE `facility` RENAME INDEX `store_address_idx` TO `facility_address_idx`;
ALTER TABLE `facility` RENAME INDEX `store_location_idx` TO `facility_location_idx`;
ALTER TABLE `facility` RENAME INDEX `store_taxpayer_idx` TO `facility_taxpayer_idx`;

-- MySQL/MariaDB cannot rename a FK constraint in place; drop and re-add with
-- the exact original definitions.
ALTER TABLE `facility` DROP FOREIGN KEY `store_address_fk`;
ALTER TABLE `facility` DROP FOREIGN KEY `store_location_fk`;
ALTER TABLE `facility` DROP FOREIGN KEY `store_taxpayer_fk`;
ALTER TABLE `facility`
  ADD CONSTRAINT `facility_address_fk` FOREIGN KEY (`address`) REFERENCES `address` (`address_id`) ON DELETE NO ACTION ON UPDATE NO ACTION,
  ADD CONSTRAINT `facility_location_fk` FOREIGN KEY (`location`) REFERENCES `sat_postal_code` (`sat_postal_code_id`) ON DELETE NO ACTION ON UPDATE NO ACTION,
  ADD CONSTRAINT `facility_taxpayer_fk` FOREIGN KEY (`taxpayer`) REFERENCES `taxpayer_issuer` (`taxpayer_issuer_id`) ON DELETE NO ACTION ON UPDATE NO ACTION;

-- -----------------------------------------------------------------------------
-- (d) For every referencing table (except production_site, which is dropped
--     in step (e)): rename column `store` -> `facility`, rename affected
--     indexes, and re-add the FK constraint against facility(facility_id),
--     preserving the original ON DELETE / ON UPDATE clauses.
-- -----------------------------------------------------------------------------

ALTER TABLE `cash_drawer`
  CHANGE COLUMN `store` `facility` int(11) NOT NULL DEFAULT 1,
  DROP KEY `cash_drawer_store_fk_idx`,
  ADD KEY `cash_drawer_facility_fk_idx` (`facility`),
  ADD CONSTRAINT `cash_drawer_facility_fk` FOREIGN KEY (`facility`) REFERENCES `facility` (`facility_id`) ON DELETE NO ACTION ON UPDATE NO ACTION;

ALTER TABLE `customer_payment`
  CHANGE COLUMN `store` `facility` int(11) NOT NULL,
  DROP KEY `customer_payment_store_fk_idx`,
  ADD KEY `customer_payment_facility_fk_idx` (`facility`),
  ADD CONSTRAINT `customer_payment_facility_fk` FOREIGN KEY (`facility`) REFERENCES `facility` (`facility_id`) ON DELETE NO ACTION ON UPDATE NO ACTION;

ALTER TABLE `customer_refund`
  CHANGE COLUMN `store` `facility` int(11) NOT NULL,
  DROP KEY `customer_refund_store_idx`,
  ADD KEY `customer_refund_facility_idx` (`facility`),
  ADD CONSTRAINT `customer_refund_facility_fk` FOREIGN KEY (`facility`) REFERENCES `facility` (`facility_id`) ON DELETE NO ACTION ON UPDATE NO ACTION;

ALTER TABLE `delivery_order`
  CHANGE COLUMN `store` `facility` int(11) NOT NULL,
  DROP KEY `FK_delivery_order_store`,
  ADD KEY `FK_delivery_order_facility` (`facility`),
  ADD CONSTRAINT `FK_delivery_order_facility` FOREIGN KEY (`facility`) REFERENCES `facility` (`facility_id`) ON DELETE NO ACTION ON UPDATE NO ACTION;

ALTER TABLE `expense_voucher`
  CHANGE COLUMN `store` `facility` int(11) NOT NULL DEFAULT 0,
  DROP KEY `FK_expense_voucher_store`,
  ADD KEY `FK_expense_voucher_facility` (`facility`),
  ADD CONSTRAINT `FK_expense_voucher_facility` FOREIGN KEY (`facility`) REFERENCES `facility` (`facility_id`);

ALTER TABLE `fiscal_document`
  CHANGE COLUMN `store` `facility` int(11) NOT NULL,
  DROP KEY `fiscal_document_store_fk_idx`,
  ADD KEY `fiscal_document_facility_fk_idx` (`facility`),
  ADD CONSTRAINT `fiscal_document_facility_fk` FOREIGN KEY (`facility`) REFERENCES `facility` (`facility_id`) ON DELETE NO ACTION ON UPDATE NO ACTION;

ALTER TABLE `inventory_issue`
  CHANGE COLUMN `store` `facility` int(11) NOT NULL,
  DROP KEY `inventory_issue_store_serial_idx`,
  ADD UNIQUE KEY `inventory_issue_facility_serial_idx` (`facility`,`serial`),
  DROP KEY `inventory_issue_store_idx`,
  ADD KEY `inventory_issue_facility_idx` (`facility`),
  ADD CONSTRAINT `inventory_issue_facility_fk` FOREIGN KEY (`facility`) REFERENCES `facility` (`facility_id`) ON DELETE NO ACTION ON UPDATE NO ACTION;

ALTER TABLE `inventory_receipt`
  CHANGE COLUMN `store` `facility` int(11) NOT NULL,
  DROP KEY `inventory_receipt_store_serial_idx`,
  ADD UNIQUE KEY `inventory_receipt_facility_serial_idx` (`facility`,`serial`),
  DROP KEY `inventory_receipt_store_idx`,
  ADD KEY `inventory_receipt_facility_idx` (`facility`),
  ADD CONSTRAINT `inventory_receipt_facility_fk` FOREIGN KEY (`facility`) REFERENCES `facility` (`facility_id`) ON DELETE NO ACTION ON UPDATE NO ACTION;

ALTER TABLE `inventory_transfer`
  CHANGE COLUMN `store` `facility` int(11) NOT NULL,
  DROP KEY `inventory_transfer_store_serial_idx`,
  ADD UNIQUE KEY `inventory_transfer_facility_serial_idx` (`facility`,`serial`),
  DROP KEY `inventory_transfer_store_idx`,
  ADD KEY `inventory_transfer_facility_idx` (`facility`),
  ADD CONSTRAINT `inventory_transfer_facility_fk` FOREIGN KEY (`facility`) REFERENCES `facility` (`facility_id`) ON DELETE NO ACTION ON UPDATE NO ACTION;

ALTER TABLE `payment_method_option`
  CHANGE COLUMN `store` `facility` int(11) NOT NULL,
  DROP KEY `FK_payment_method_charge_store`,
  ADD KEY `FK_payment_method_charge_facility` (`facility`),
  ADD CONSTRAINT `FK_payment_method_charge_facility` FOREIGN KEY (`facility`) REFERENCES `facility` (`facility_id`);

ALTER TABLE `point_sale`
  CHANGE COLUMN `store` `facility` int(11) NOT NULL DEFAULT 1,
  DROP KEY `point_sale_store_fk_idx`,
  ADD KEY `point_sale_facility_fk_idx` (`facility`),
  ADD CONSTRAINT `point_sale_facility_fk` FOREIGN KEY (`facility`) REFERENCES `facility` (`facility_id`) ON DELETE NO ACTION ON UPDATE NO ACTION;

ALTER TABLE `sales_order`
  CHANGE COLUMN `store` `facility` int(11) NOT NULL,
  DROP KEY `sales_order_store_serial_idx`,
  ADD UNIQUE KEY `sales_order_facility_serial_idx` (`facility`,`serial`),
  DROP KEY `sales_order_store_fk_idx`,
  ADD KEY `sales_order_facility_fk_idx` (`facility`),
  ADD CONSTRAINT `sales_order_facility_fk` FOREIGN KEY (`facility`) REFERENCES `facility` (`facility_id`) ON DELETE NO ACTION ON UPDATE NO ACTION;

ALTER TABLE `sales_quote`
  CHANGE COLUMN `store` `facility` int(11) NOT NULL,
  DROP KEY `sales_quote_store_fk_idx`,
  ADD KEY `sales_quote_facility_fk_idx` (`facility`),
  ADD CONSTRAINT `sales_quote_facility_fk` FOREIGN KEY (`facility`) REFERENCES `facility` (`facility_id`) ON DELETE NO ACTION ON UPDATE NO ACTION;

ALTER TABLE `special_receipt`
  CHANGE COLUMN `store` `facility` int(11) NOT NULL DEFAULT 0,
  DROP KEY `FK__store`,
  ADD KEY `FK__facility` (`facility`),
  ADD CONSTRAINT `FK__facility` FOREIGN KEY (`facility`) REFERENCES `facility` (`facility_id`);

ALTER TABLE `user_settings`
  CHANGE COLUMN `store` `facility` int(11) NOT NULL,
  DROP KEY `user_settings_store_fk_idx`,
  ADD KEY `user_settings_facility_fk_idx` (`facility`),
  ADD CONSTRAINT `user_settings_facility_fk` FOREIGN KEY (`facility`) REFERENCES `facility` (`facility_id`) ON DELETE NO ACTION ON UPDATE NO ACTION;

ALTER TABLE `warehouse`
  CHANGE COLUMN `store` `facility` int(11) NOT NULL,
  DROP KEY `warehouse_store_fk_idx`,
  ADD KEY `warehouse_facility_fk_idx` (`facility`),
  ADD CONSTRAINT `warehouse_facility_fk` FOREIGN KEY (`facility`) REFERENCES `facility` (`facility_id`) ON DELETE NO ACTION ON UPDATE NO ACTION;

-- -----------------------------------------------------------------------------
-- (e) production_site is unused as a standalone table; drop it.
-- -----------------------------------------------------------------------------

DROP TABLE `production_site`;

-- -----------------------------------------------------------------------------
-- (f) Rename the computed/report column that referenced "store".
-- -----------------------------------------------------------------------------

ALTER TABLE `abc_classification`
  CHANGE COLUMN `sales_by_store` `sales_by_facility` decimal(65,2) DEFAULT NULL;
