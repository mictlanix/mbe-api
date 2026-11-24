-- Rollback of 037 The simulated PAC moves to the CFDI library -- spec 040
--
-- Gives the simulated documents back the old provider RFC (the documents first, while the XML
-- still names them), recreates `pac_simulation` empty as 034 created it, and drops
-- `pac_simulator_store`: the library simulator's state is lost. Run it only with the code from
-- before spec 040, whose simulator reads `pac_simulation`.
--
-- MariaDB 10.11.

UPDATE `fiscal_document` SET `rfc_pac` = 'SIM010101SIM' WHERE `rfc_pac` = 'SIM010101SI0';

UPDATE `fiscal_document_xml` x
  JOIN `fiscal_document` d ON d.`fiscal_document_id` = x.`fiscal_document_xml_id`
  SET x.`data` = REPLACE(x.`data`, 'RfcProvCertif="SIM010101SI0"', 'RfcProvCertif="SIM010101SIM"')
  WHERE d.`rfc_pac` = 'SIM010101SIM';

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

DROP TABLE IF EXISTS `pac_simulator_store`;
