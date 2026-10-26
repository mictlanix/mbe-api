-- 027 Physical inventory counts -- spec 030 (research R1, R8)
--
-- A count records what one warehouse holds, product by product. While a draft its lines carry only
-- the counted quantity: they are blind, so no expected figure is stored or shown. Confirming reads
-- the on-hand quantity of each counted product, stores it and the difference beside the count, and
-- posts every non-zero difference to `lot_serial_tracking` as source 12 (physical count),
-- referencing the count. A confirmed count is final: it cannot be cancelled.
--
-- Two new tables, read and written by this API only:
--
-- 1. `inventory_count`: the header, shaped like `inventory_issue` so the shared inventory document
--    mechanics (spec 029) work on it unchanged. `serial` is set on confirm and is unique per
--    facility.
-- 2. `inventory_count_detail`: one line per product (UNIQUE `(count, product)`), with code and name
--    snapshots. `quantity_on_hand` and `difference` stay NULL until the count is confirmed.
--
-- The current system's adjustments (source 8, referencing its count receipt) are left as they are;
-- nothing here reads or converts them.
--
-- Idempotent: `IF NOT EXISTS` on both tables. MariaDB 10.11.
-- Rollback: 027_inventory_count_rollback.sql

CREATE TABLE IF NOT EXISTS `inventory_count` (
  `inventory_count_id` int(11) NOT NULL AUTO_INCREMENT,
  `facility` int(11) NOT NULL,
  `warehouse` int(11) NOT NULL,
  `serial` int(11) DEFAULT NULL,
  `creation_time` datetime NOT NULL,
  `modification_time` datetime NOT NULL,
  `creator` int(11) NOT NULL,
  `updater` int(11) NOT NULL,
  `completed` tinyint(1) NOT NULL DEFAULT 0,
  `cancelled` tinyint(1) NOT NULL DEFAULT 0,
  `comment` varchar(500) DEFAULT NULL,
  PRIMARY KEY (`inventory_count_id`),
  UNIQUE KEY `inventory_count_facility_serial_uq` (`facility`, `serial`),
  KEY `inventory_count_warehouse_idx` (`warehouse`),
  KEY `inventory_count_creator_idx` (`creator`),
  KEY `inventory_count_updater_idx` (`updater`),
  CONSTRAINT `inventory_count_facility_fk` FOREIGN KEY (`facility`) REFERENCES `facility` (`facility_id`),
  CONSTRAINT `inventory_count_warehouse_fk` FOREIGN KEY (`warehouse`) REFERENCES `warehouse` (`warehouse_id`),
  CONSTRAINT `inventory_count_creator_fk` FOREIGN KEY (`creator`) REFERENCES `employee` (`employee_id`),
  CONSTRAINT `inventory_count_updater_fk` FOREIGN KEY (`updater`) REFERENCES `employee` (`employee_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_unicode_ci;

CREATE TABLE IF NOT EXISTS `inventory_count_detail` (
  `count_detail_id` int(11) NOT NULL AUTO_INCREMENT,
  `count` int(11) NOT NULL,
  `product` int(11) NOT NULL,
  `product_code` varchar(25) DEFAULT NULL,
  `product_name` varchar(250) DEFAULT NULL,
  `quantity` decimal(18,4) NOT NULL COMMENT 'Counted, >= 0',
  `quantity_on_hand` decimal(18,4) DEFAULT NULL COMMENT 'Read at confirm; NULL while a draft',
  `difference` decimal(18,4) DEFAULT NULL COMMENT 'quantity - quantity_on_hand; NULL while a draft',
  PRIMARY KEY (`count_detail_id`),
  UNIQUE KEY `inventory_count_detail_count_product_uq` (`count`, `product`),
  KEY `inventory_count_detail_product_idx` (`product`),
  CONSTRAINT `inventory_count_detail_count_fk` FOREIGN KEY (`count`) REFERENCES `inventory_count` (`inventory_count_id`),
  CONSTRAINT `inventory_count_detail_product_fk` FOREIGN KEY (`product`) REFERENCES `product` (`product_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_unicode_ci;
