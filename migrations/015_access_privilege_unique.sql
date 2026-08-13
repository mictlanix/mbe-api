-- 015 One privilege row per (user, object) -- issue #160
--
-- `access_privilege` had an index on `user` alone and no uniqueness, while BOTH applications that
-- read it assume a single row per pair:
--
--   app/core/deps.py         `require_privilege` -> result.scalar_one_or_none()
--   mbe/Web/.../UsersController.cs  user.Privileges.SingleOrDefault (x => x.Object == obj)
--
-- Neither degrades on a duplicate; both raise. In this API that is a 500 on every request gated on
-- that object, for that user, until a row is deleted -- an authorisation check answering 500 rather
-- than allow or deny. The legacy application throws out of its user-edit screen for the same reason.
--
-- NEITHER WRITER CAN CREATE ONE, which is why this has never fired. The legacy controller is
-- find-or-create per SystemObjects value, and this API either seeds one row per member
-- (`_write_privileges_from`) or upserts by object (`update_user`). So the constraint records an
-- invariant both codebases already maintain by construction; it is protective, not corrective.
--
-- MEASURED against the deployment database 2026-08-12, immediately before this was written:
--
--   access_privilege rows                             3,355
--   distinct (user, object) pairs                     3,355   <-- equal, so no repair needed
--   duplicate pairs                                       0
--   users                                                31
--
-- WHY THIS IS A SEPARATE MIGRATION FROM THE CODE THAT NEEDED IT. Spec 014 (user profiles) found
-- this and deliberately left it alone: adding a unique index to a table that might already contain
-- duplicates fails at apply time on a condition nobody has diagnosed. The order is measure, repair,
-- constrain. The measurement came back clean, so there is nothing to repair and this is the third
-- step arriving on its own. See specs/014-user-profiles/research.md R2.
--
-- `deps.py` IS NOT CHANGED, and that is deliberate. With this index in place `scalar_one_or_none()`
-- can never raise, so it becomes an accurate assertion rather than a hazard. Relaxing it to
-- `first()` would trade a loud 500 for a silent, arbitrary authorisation decision -- strictly worse
-- on a permission check.
--
-- Idempotent: ADD UNIQUE KEY IF NOT EXISTS.
--
-- MariaDB 10.11. Rollback: 015_access_privilege_unique_rollback.sql

-- ---------------------------------------------------------------------------
-- Step 1 -- precondition: no pair is duplicated
-- ---------------------------------------------------------------------------
--
-- A CHECK on a temporary table rather than SIGNAL, for the same reason as 011 and 012: SIGNAL is
-- only legal inside a compound statement and the migration runner splits this file on semicolons,
-- so BEGIN...END would be torn apart. The INSERT below fails on `chk_015_no_duplicate_privileges`
-- while any pair is duplicated, and the runner prints the failing statement verbatim.
--
-- Without this the ALTER still refuses -- error 1062, "Duplicate entry 'x-y' for key" -- but it
-- names one offending pair rather than the condition, and gives no way to see how many there are:
--
--   SELECT `user`, `object`, COUNT(*) FROM `access_privilege`
--   GROUP BY `user`, `object` HAVING COUNT(*) > 1;
--
-- If any do remain, deciding WHICH row survives is a policy question, not a mechanical one -- the
-- widest mask, the narrowest, or the lowest id are all defensible and they are not the same thing.
-- Stop and decide it rather than letting a dedupe script pick.

CREATE TEMPORARY TABLE `_015_precondition` (
  `every_pair_is_unique` TINYINT NOT NULL,
  CONSTRAINT `chk_015_no_duplicate_privileges` CHECK (`every_pair_is_unique` = 1)
);

INSERT INTO `_015_precondition` (`every_pair_is_unique`)
SELECT IF(
  (SELECT COUNT(*) FROM `access_privilege`) =
  (SELECT COUNT(DISTINCT CONCAT(`user`, ':', `object`)) FROM `access_privilege`),
  1, 0
);

DROP TEMPORARY TABLE `_015_precondition`;

-- ---------------------------------------------------------------------------
-- Step 2 -- the constraint
-- ---------------------------------------------------------------------------
--
-- The existing `user_access_privilege_idx` (`user`) is left in place. It is redundant as a prefix
-- of this one, but dropping it is a separate judgement about query plans and belongs to whoever
-- measures them -- not to a migration whose purpose is an integrity constraint.

ALTER TABLE `access_privilege`
  ADD UNIQUE KEY IF NOT EXISTS `access_privilege_user_object_uq` (`user`, `object`);
