-- 014 User profiles as permission templates (specs/014-user-profiles)
--
-- A user's permissions are 107 rows in `access_privilege`, one per SystemObject. Provisioning an
-- account therefore means deciding 107 masks, and every cashier is provisioned the same way as the
-- last cashier with nothing in the system recording that fact. This adds a named, reusable template
-- that an administrator maintains once and copies onto users.
--
-- A TEMPLATE, NOT A GROUPING. Applying a profile copies its masks into `access_privilege`; the copy
-- is the account's own. Editing a profile afterwards reaches nobody. `user`.`profile` records where
-- an account was provisioned from -- provenance only, never read by an authorization decision.
--
-- MEASURED 2026-08-12 against the deployment database, and re-confirmed immediately before this
-- migration was written (research R0, task T002):
--
--   users                                               31  (3 administrators, 0 unlinked)
--   access_privilege rows                            3,355
--   duplicate (user, object) pairs                       0  <-- see the note below
--   privilege rows per user                    106 min, 110 max
--   distinct `object` values in use                    110  (range 0-113)
--   rows on objects SystemObject omitted                88  (28 of them granting something)
--
-- THE 88 ROWS. Objects 70, 104, 105 and 107 appear in the table and did not appear in
-- `SystemObject`. The legacy catalog (`mbe/Model/Constants/SystemObjects.cs`) names them:
-- `SalesOrderShipments = 70`, `SearchAllSalesOrderFromAllUsers = 104` and
-- `SearchAllSalesOrderFromAllStores = 105` are all COMMENTED OUT there -- retired features whose
-- grants outlived them, read by neither application. `ProductionSites = 107` is NOT commented out;
-- the API's enum was simply missing it, behind a `# 107 absent` comment the data contradicted.
-- Both are handled in application code, not here: the enum gains PRODUCTION_SITES = 107, and an
-- applied profile rewrites all 107 rows, which removes the 59 rows on 70/104/105 for whichever
-- account it touches. THIS MIGRATION DELETES NOTHING -- the cleanup is lazy and per-account, so a
-- database that never has a profile applied keeps every row it has today.
--
-- NO UNIQUE KEY ON (user, object), and none added here. `deps.py` reads a privilege with
-- `scalar_one_or_none()`, which RAISES on two matching rows -- so a duplicate would answer 500 for
-- every request gated on that object. Measured zero, so the hazard is latent rather than live, and
-- constraining a table before repairing it is the wrong order. Its own issue (research R2).
--
-- THE OTHER WRITER. The legacy application writes `user` and `access_privilege`. It has no profile
-- concept at all -- checked, not assumed: no table matching '%profile%' or '%role%' exists in the
-- deployment database, and nothing in `mbe/Model` references one. So the two new tables are this
-- API's alone, and `user`.`profile` is a column the legacy application never sets. It is NULL for
-- every row it inserts, which is exactly the value that means "no recorded origin".
--
-- ZERO ROWS SEEDED. Profiles are authored by administrators, not inferred. No attempt is made to
-- guess which profile an existing account resembles, and no account gets an origin retroactively.
--
-- Idempotent: IF NOT EXISTS throughout, so re-applying changes nothing.
--
-- MariaDB 10.11. Rollback: 014_user_profiles_rollback.sql

-- ---------------------------------------------------------------------------
-- Step 1 -- the profile catalog
-- ---------------------------------------------------------------------------
--
-- `name` is UNIQUE with the schema's usual utf8mb3_unicode_ci collation, which makes the index
-- case-insensitive: "cashier" collides with "Cashier". The service also compares on LOWER(), so
-- the rule holds under SQLite in the integration tests, where `=` on TEXT is case-sensitive and
-- the collation would not help (research R4).

CREATE TABLE IF NOT EXISTS `user_profile` (
  `user_profile_id` INT(11) NOT NULL AUTO_INCREMENT,
  `name` VARCHAR(100) CHARACTER SET utf8mb3 COLLATE utf8mb3_unicode_ci NOT NULL,
  `description` VARCHAR(250) CHARACTER SET utf8mb3 COLLATE utf8mb3_unicode_ci DEFAULT NULL,
  `status` INT(11) NOT NULL DEFAULT 0,
  PRIMARY KEY (`user_profile_id`),
  UNIQUE KEY `user_profile_name_uq` (`name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_unicode_ci;

-- ---------------------------------------------------------------------------
-- Step 2 -- the masks a profile grants
-- ---------------------------------------------------------------------------
--
-- Column names mirror `access_privilege` exactly -- `object` and `privileges` -- so the copy in
-- `user_service._write_privileges_from` reads as a field-for-field transfer.
--
-- SPARSE: a row exists only for an object the profile grants something on. There is no row per
-- SystemObject here, unlike `access_privilege`. Absence means denied.
--
-- No UNIQUE KEY on (user_profile, object) either, for consistency with `access_privilege`: the
-- service replaces the whole entry set on every write, so this API cannot produce a duplicate.

CREATE TABLE IF NOT EXISTS `user_profile_privilege` (
  `user_profile_privilege_id` INT(11) NOT NULL AUTO_INCREMENT,
  `user_profile` INT(11) NOT NULL,
  `object` INT(11) NOT NULL,
  `privileges` INT(11) NOT NULL DEFAULT 0,
  PRIMARY KEY (`user_profile_privilege_id`),
  KEY `user_profile_privilege_idx` (`user_profile`),
  CONSTRAINT `user_profile_privilege_profile` FOREIGN KEY (`user_profile`)
    REFERENCES `user_profile` (`user_profile_id`) ON DELETE NO ACTION ON UPDATE NO ACTION
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_unicode_ci;

-- ---------------------------------------------------------------------------
-- Step 3 -- where an account was provisioned from
-- ---------------------------------------------------------------------------
--
-- NULLABLE, and that is load-bearing: all 31 existing accounts have no origin, and an account
-- created without naming a profile keeps none.
--
-- NO `ON DELETE SET NULL`. The FK is plain so that deleting a profile users were provisioned from
-- is REFUSED (409, naming the blocking table) rather than silently rewriting user rows. Retiring a
-- role is `status`, not deletion. `assert_not_referenced` derives this from FK metadata, so the
-- refusal needs no application code.

-- Index and constraint follow this schema's `<target>_<table>` naming, as `user`.`employee` does
-- with `employee_user_idx` / `employee_user`. The constraint is deliberately NOT named
-- `user_profile`: that is the table's name, and a constraint sharing it reads as a typo.
--
-- MariaDB accepts IF NOT EXISTS on all three forms -- ADD COLUMN, ADD KEY and ADD FOREIGN KEY --
-- so re-applying this file by hand is a no-op rather than an error.

ALTER TABLE `user`
  ADD COLUMN IF NOT EXISTS `profile` INT(11) DEFAULT NULL;

ALTER TABLE `user`
  ADD KEY IF NOT EXISTS `user_profile_user_idx` (`profile`);

ALTER TABLE `user`
  ADD CONSTRAINT `user_profile_user` FOREIGN KEY IF NOT EXISTS (`profile`)
    REFERENCES `user_profile` (`user_profile_id`) ON DELETE NO ACTION ON UPDATE NO ACTION;
