-- 037 The simulated PAC moves to the CFDI library -- spec 040
--
-- 1. `pac_simulator_store`, new: the state of the library's simulated PAC (mictlanix/cfdi), as
--    string values under string keys that only the library reads (`seal:<sha-256>` per sealed
--    document, `uuid:<UUID>` per stamp). Empty unless `PAC_PROVIDER=simulated`. API-only.
-- 2. `pac_simulation` (034), dropped without converting its rows: mbe-api's own simulator is gone,
--    and converting would mean computing the library's private keys here. On 2026-11-18 no
--    simulated document was stamping, no cancellation request was open, and no outcome was queued.
-- 3. The old simulator's provider RFC `SIM010101SIM`, which SAT's TFD schema rejects, becomes the
--    library simulator's `SIM010101SI0` on the documents it stamped and in their stored XML, so
--    they stay marked as simulated. The XML first, while `rfc_pac` still names the documents.
--
-- The current system never reads either table.
--
-- Idempotent. MariaDB 10.11.
-- Rollback: 037_pac_simulator_store_rollback.sql

CREATE TABLE IF NOT EXISTS `pac_simulator_store` (
  `store_key` varchar(100) NOT NULL,
  `value` mediumtext NOT NULL,
  `updated_at` datetime NOT NULL,
  PRIMARY KEY (`store_key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_unicode_ci;

DROP TABLE IF EXISTS `pac_simulation`;

UPDATE `fiscal_document_xml` x
  JOIN `fiscal_document` d ON d.`fiscal_document_id` = x.`fiscal_document_xml_id`
  SET x.`data` = REPLACE(x.`data`, 'RfcProvCertif="SIM010101SIM"', 'RfcProvCertif="SIM010101SI0"')
  WHERE d.`rfc_pac` = 'SIM010101SIM';

UPDATE `fiscal_document` SET `rfc_pac` = 'SIM010101SI0' WHERE `rfc_pac` = 'SIM010101SIM';
