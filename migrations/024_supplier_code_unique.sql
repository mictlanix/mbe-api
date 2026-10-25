-- 024 One supplier per code, enforced by the database (#256)
--
-- The current system documents `supplier.code` as unique (scope 01 §6) but never enforced it, and
-- this API accepted duplicates on create and update. mbe_dev holds six duplicated codes across 14
-- suppliers, most with purchase orders. The API now rejects a taken code with 409 before the
-- insert; this adds the index behind it, as `warehouse.code_UNIQUE` already does for warehouses.
--
-- As in 007, the earliest supplier (lowest id) keeps the code and every later one becomes
-- `<code>-<supplier_id>`, which cannot collide with anything. In mbe_dev, eight suppliers move:
--
--   12589   → 12589-265, 12589-266, 12589-268  (217 keeps it)
--   125897  → 125897-242                       (210 keeps it)
--   1478963 → 1478963-229                      (159 keeps it)
--   15746   → 15746-259                        (258 keeps it; both are named TUBCONCRE)
--   CM      → CM-203                           (19 keeps it)
--   Duar    → Duar-182                         (181 keeps it; both are named DUAR)
--
-- 258/259 and 181/182 look like the same supplier entered twice. They are renamed, not merged:
-- merging rewrites purchase orders and cannot be undone, and a rename leaves that choice open.
--
-- The current system writes `supplier` directly. Once this runs, saving a supplier there with a
-- code another supplier uses fails with a duplicate-key error instead of saving.
--
-- The column's collation is utf8mb3_unicode_ci, so `CM` and `cm` are the same code to the index
-- and to the partition below. Idempotent: once no duplicates remain the UPDATE matches nothing,
-- and the index is created only if missing.
--
-- MariaDB 10.11 (ROW_NUMBER requires 10.2+).
-- Rollback: 024_supplier_code_unique_rollback.sql

UPDATE `supplier` s
JOIN (
    SELECT `supplier_id`,
           ROW_NUMBER() OVER (PARTITION BY `code` ORDER BY `supplier_id`) AS rn
    FROM `supplier`
) d ON d.`supplier_id` = s.`supplier_id`
SET s.`code` = CONCAT(s.`code`, '-', s.`supplier_id`)
WHERE d.rn > 1;

CREATE UNIQUE INDEX IF NOT EXISTS `supplier_code_uq` ON `supplier` (`code`);
