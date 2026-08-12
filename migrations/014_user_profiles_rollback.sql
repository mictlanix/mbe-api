-- Rollback for 014_user_profiles.sql
--
-- WHAT IS LOST: every profile, every profile entry, and every recorded origin. Those live only in
-- the objects this drops.
--
-- WHAT IS NOT LOST: any user's permissions. `access_privilege` is not touched here, and was never
-- touched by the forward migration either -- an applied profile writes those rows through the
-- application, not through DDL. So every account keeps exactly the masks it holds when this runs.
--
-- That asymmetry is the design, not an oversight: permissions are VALUES, copied once and owned by
-- the account; an origin is a POINTER, and a pointer is what a rollback can afford to discard.
-- After rolling back, accounts provisioned from a profile keep their permissions and simply stop
-- recording where they came from -- which is the same state as every account before spec 014.
--
-- WHAT THIS DOES NOT RESTORE: the 59 rows on objects 70, 104 and 105 that applied profiles deleted
-- (research R9). Those are grants on features commented out in the legacy catalog, read by neither
-- application, and they are gone for whichever accounts had a profile applied. Rolling back the
-- schema cannot bring them back; nothing reads them, so nothing notices.
--
-- ORDER MATTERS. The FK on `user`.`profile` has to go before `user_profile` can be dropped, and
-- `user_profile_privilege` references `user_profile` too.
--
-- ALSO NOT UNDONE: `SystemObject.PRODUCTION_SITES = 107` is application code, not schema. If this
-- rollback is being run to undo spec 014 entirely, that enum member and its `docs/constants.md` row
-- go with the code revert. Leaving it in place is harmless -- it describes an object the legacy
-- application really does have.
--
-- MariaDB 10.11.

-- ---------------------------------------------------------------------------
-- Step 1 -- release the reference from `user`
-- ---------------------------------------------------------------------------

ALTER TABLE `user`
  DROP FOREIGN KEY IF EXISTS `user_profile_user`;

ALTER TABLE `user`
  DROP KEY IF EXISTS `user_profile_user_idx`;

ALTER TABLE `user`
  DROP COLUMN IF EXISTS `profile`;

-- ---------------------------------------------------------------------------
-- Step 2 -- the tables, child first
-- ---------------------------------------------------------------------------

DROP TABLE IF EXISTS `user_profile_privilege`;

DROP TABLE IF EXISTS `user_profile`;
