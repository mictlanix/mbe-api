-- 034 CFDI stamping and cancellation against a simulated PAC -- spec 039
--
-- 1. `fiscal_document.stamping_since` (R3): set when a stamp request is about to reach the PAC,
--    cleared when the stamp is stored or the PAC refuses. A non-null value means the document is
--    being stamped.
-- 2. `fiscal_document.cancelling_since` (R8): set with a cancellation request, cleared when that
--    request closes. On a completed, uncancelled document it means the cancellation is pending.
-- 3. `fiscal_document_cancellation`, new: one row per cancellation request. `open_document`, a
--    virtual column, is the document while the request is requested or pending and NULL once it
--    closes; its unique key allows at most one open request per document. MariaDB has no partial
--    indexes; this is its equivalent, the technique migration 033 uses.
-- 4. `pac_simulation`, new: the state of the simulated PAC. Empty unless `PAC_PROVIDER=simulated`.
--
-- The current system's mappings name their columns, so it ignores every addition here.
--
-- Idempotent. MariaDB 10.11 (indexes on virtual columns require 10.2+).
-- Rollback: 034_cfdi_stamping_cancellation_rollback.sql

ALTER TABLE `fiscal_document`
  ADD COLUMN IF NOT EXISTS `stamping_since` DATETIME NULL,
  ADD COLUMN IF NOT EXISTS `cancelling_since` DATETIME NULL;

CREATE TABLE IF NOT EXISTS `fiscal_document_cancellation` (
  `fiscal_document_cancellation_id` int(11) NOT NULL AUTO_INCREMENT,
  `fiscal_document` int(11) NOT NULL,
  `reason` varchar(2) NOT NULL,
  `substitution` varchar(36) DEFAULT NULL,
  `status` varchar(20) NOT NULL COMMENT 'requested, pending, accepted, expired, rejected, not_cancellable or failed',
  `pac_code` varchar(20) DEFAULT NULL,
  `pac_message` varchar(500) DEFAULT NULL,
  `requested_by` int(11) NOT NULL,
  `requested_at` datetime NOT NULL,
  `answered_at` datetime DEFAULT NULL,
  `resolved_at` datetime DEFAULT NULL,
  `last_checked_at` datetime DEFAULT NULL,
  `open_document` int(11) AS (IF(`status` IN ('requested','pending'), `fiscal_document`, NULL)) VIRTUAL,
  PRIMARY KEY (`fiscal_document_cancellation_id`),
  UNIQUE KEY `fiscal_document_cancellation_open_uq` (`open_document`),
  KEY `fiscal_document_cancellation_status_requested_at_idx` (`status`, `requested_at`),
  KEY `fiscal_document_cancellation_fiscal_document_idx` (`fiscal_document`),
  KEY `fiscal_document_cancellation_reason_idx` (`reason`),
  KEY `fiscal_document_cancellation_requested_by_idx` (`requested_by`),
  CONSTRAINT `fiscal_document_cancellation_fiscal_document_fk` FOREIGN KEY (`fiscal_document`) REFERENCES `fiscal_document` (`fiscal_document_id`),
  CONSTRAINT `fiscal_document_cancellation_reason_fk` FOREIGN KEY (`reason`) REFERENCES `sat_reason_cancellation` (`sat_reason_cancellation_id`),
  CONSTRAINT `fiscal_document_cancellation_requested_by_fk` FOREIGN KEY (`requested_by`) REFERENCES `employee` (`employee_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_unicode_ci;

CREATE TABLE IF NOT EXISTS `pac_simulation` (
  `pac_simulation_id` int(11) NOT NULL AUTO_INCREMENT,
  `issuer` varchar(13) NOT NULL,
  `batch` varchar(10) DEFAULT NULL,
  `serial` int(11) DEFAULT NULL,
  `uuid` char(36) DEFAULT NULL,
  `stamped_at` datetime DEFAULT NULL,
  `stamped_xml` mediumtext DEFAULT NULL,
  `sat_status` varchar(10) NOT NULL DEFAULT 'valid',
  `cancellation_status` varchar(10) NOT NULL DEFAULT 'none',
  `next_stamp` varchar(20) DEFAULT NULL,
  `next_cancellation` varchar(20) DEFAULT NULL,
  `pac_code` varchar(20) DEFAULT NULL,
  `pac_message` varchar(500) DEFAULT NULL,
  `updated_at` datetime NOT NULL,
  PRIMARY KEY (`pac_simulation_id`),
  UNIQUE KEY `pac_simulation_document_uq` (`issuer`, `batch`, `serial`),
  UNIQUE KEY `pac_simulation_uuid_uq` (`uuid`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_unicode_ci;
