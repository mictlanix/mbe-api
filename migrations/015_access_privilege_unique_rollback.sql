-- Rollback for 015_access_privilege_unique.sql
--
-- Drops the uniqueness constraint. No row is touched and no data is lost -- the index carried no
-- information of its own.
--
-- WHAT ROLLING BACK RESTORES: the ability to insert a duplicate (user, object) pair, which both
-- applications answer with an exception rather than a degraded result (`scalar_one_or_none()` here,
-- `SingleOrDefault` in the legacy user-edit screen). Neither writer creates one, so in practice this
-- returns the table to a state whose invariant is maintained by convention rather than by the
-- database. That is exactly where it was before 015.
--
-- `user_access_privilege_idx` (`user`) is untouched by both the forward migration and this, so
-- lookups by user keep an index either way.
--
-- MariaDB 10.11.

ALTER TABLE `access_privilege`
  DROP KEY IF EXISTS `access_privilege_user_object_uq`;
