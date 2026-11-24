from pathlib import Path

import pytest

from app.db.migrate import MigrationError, discover, split_statements


class TestSplitStatements:
    def test_splits_on_semicolons(self):
        sql = 'ALTER TABLE `a` ADD COLUMN `x` INT;\nALTER TABLE `b` DROP COLUMN `y`;'
        assert split_statements(sql) == [
            'ALTER TABLE `a` ADD COLUMN `x` INT',
            'ALTER TABLE `b` DROP COLUMN `y`',
        ]

    def test_strips_line_comments(self):
        sql = '-- a comment\nSELECT 1;\n-- another; with a semicolon\nSELECT 2;'
        assert split_statements(sql) == ['SELECT 1', 'SELECT 2']

    def test_ignores_semicolon_inside_single_quotes(self):
        sql = "UPDATE `t` SET `c` = 'a;b' WHERE `d` = 1;"
        assert split_statements(sql) == ["UPDATE `t` SET `c` = 'a;b' WHERE `d` = 1"]

    def test_ignores_semicolon_inside_double_quotes(self):
        sql = 'UPDATE `t` SET `c` = "a;b";'
        assert split_statements(sql) == ['UPDATE `t` SET `c` = "a;b"']

    def test_ignores_semicolon_inside_backticks(self):
        sql = 'ALTER TABLE `we;rd` ADD COLUMN `x` INT;'
        assert split_statements(sql) == ['ALTER TABLE `we;rd` ADD COLUMN `x` INT']

    def test_ignores_double_dash_inside_string(self):
        sql = "UPDATE `t` SET `c` = 'a -- b';"
        assert split_statements(sql) == ["UPDATE `t` SET `c` = 'a -- b'"]

    def test_keeps_trailing_statement_without_terminator(self):
        assert split_statements('SELECT 1;\nSELECT 2') == ['SELECT 1', 'SELECT 2']

    def test_drops_empty_fragments(self):
        assert split_statements('SELECT 1;;\n\n;') == ['SELECT 1']

    def test_empty_input(self):
        assert split_statements('-- only a comment\n\n') == []

    def test_escaped_quote_inside_string(self):
        sql = "UPDATE `t` SET `c` = 'it\\'s; fine';"
        assert split_statements(sql) == ["UPDATE `t` SET `c` = 'it\\'s; fine'"]


def _write(directory, name, body='SELECT 1;'):
    path = directory / name
    path.write_text(body)
    return path


class TestDiscover:
    def test_orders_numerically_not_lexically(self, tmp_path):
        _write(tmp_path, '010_ten.sql')
        _write(tmp_path, '009_nine.sql')
        _write(tmp_path, '004_four.sql')
        assert [m.version for m in discover(tmp_path)] == ['004_four', '009_nine', '010_ten']

    def test_excludes_rollback_files(self, tmp_path):
        _write(tmp_path, '005_thing.sql')
        _write(tmp_path, '005_thing_rollback.sql')
        assert [m.version for m in discover(tmp_path)] == ['005_thing']

    def test_does_not_scan_subdirectories(self, tmp_path):
        _write(tmp_path, '001_top.sql')
        nested = tmp_path / 'sql'
        nested.mkdir()
        _write(nested, '002_nested.sql')
        assert [m.version for m in discover(tmp_path)] == ['001_top']

    def test_rejects_duplicate_prefix_naming_both_files(self, tmp_path):
        _write(tmp_path, '006_one.sql')
        _write(tmp_path, '006_two.sql')
        with pytest.raises(MigrationError) as exc:
            discover(tmp_path)
        assert '006_one.sql' in str(exc.value)
        assert '006_two.sql' in str(exc.value)

    def test_rejects_non_numeric_prefix(self, tmp_path):
        _write(tmp_path, 'facility_rename.sql')
        with pytest.raises(MigrationError) as exc:
            discover(tmp_path)
        assert 'facility_rename.sql' in str(exc.value)

    def test_empty_directory(self, tmp_path):
        assert discover(tmp_path) == []

    def test_rollback_alone_is_not_a_migration(self, tmp_path):
        _write(tmp_path, '007_thing_rollback.sql')
        assert discover(tmp_path) == []


class TestRealCorpus:
    """The shipped `migrations/` directory, not a fixture — ordering there is what actually runs."""

    def test_008_is_discovered_and_ordered_after_007(self):
        versions = [m.version for m in discover(Path('migrations'))]
        assert any(v.startswith('008_') for v in versions), '008 missing from migrations/'
        assert versions.index('008_delivery_flow_v2') > versions.index('007_document_serial_unique')

    def test_008_rollback_is_not_applied_automatically(self):
        versions = [m.version for m in discover(Path('migrations'))]
        assert '008_delivery_flow_v2_rollback' not in versions

    def test_010_is_discovered_and_ordered_after_009(self):
        versions = [m.version for m in discover(Path('migrations'))]
        assert versions.index('010_system_employee') > versions.index(
            '009_drop_sales_order_detail_delivery'
        )

    def test_009_is_discovered_and_ordered_after_008(self):
        versions = [m.version for m in discover(Path('migrations'))]
        assert versions.index('009_drop_sales_order_detail_delivery') > versions.index(
            '008_delivery_flow_v2'
        )

    def test_011_is_discovered_and_ordered_after_010(self):
        versions = [m.version for m in discover(Path('migrations'))]
        assert versions.index('011_facility_transit_warehouses') > versions.index(
            '010_system_employee'
        )

    def test_011_rollback_is_not_applied_automatically(self):
        versions = [m.version for m in discover(Path('migrations'))]
        assert '011_facility_transit_warehouses_rollback' not in versions

    def test_014_is_discovered_and_ordered_after_013(self):
        versions = [m.version for m in discover(Path('migrations'))]
        # 013 is the highest applied before this one; there is no 013_*.sql, so 012 is the anchor
        assert versions.index('014_user_profiles') > versions.index('012_user_employee_required')

    def test_014_rollback_is_not_applied_automatically(self):
        versions = [m.version for m in discover(Path('migrations'))]
        assert '014_user_profiles_rollback' not in versions

    def test_015_and_016_are_discovered_and_ordered_after_014(self):
        versions = [m.version for m in discover(Path('migrations'))]
        assert versions.index('015_access_privilege_unique') > versions.index('014_user_profiles')
        assert versions.index('016_user_password_width') > versions.index(
            '015_access_privilege_unique'
        )

    def test_015_and_016_rollbacks_are_not_applied_automatically(self):
        versions = [m.version for m in discover(Path('migrations'))]
        assert '015_access_privilege_unique_rollback' not in versions
        assert '016_user_password_width_rollback' not in versions

    def test_030_is_discovered_and_ordered_after_029(self):
        """Spec 035. Its SQL (an `UPDATE … JOIN` with a window function, `CREATE INDEX IF NOT
        EXISTS`) is MariaDB's and cannot run on SQLite; it is verified on mbe_dev instead."""
        versions = [m.version for m in discover(Path('migrations'))]
        assert versions.index('030_serials_ledger_index') > versions.index('029_supplier_payments')

    def test_030_rollback_is_not_applied_automatically(self):
        versions = [m.version for m in discover(Path('migrations'))]
        assert '030_serials_ledger_index_rollback' not in versions

    def test_033_is_discovered_and_ordered_after_032(self):
        """#309. A virtual column and a unique key over it are MariaDB's; not run on SQLite."""
        versions = [m.version for m in discover(Path('migrations'))]
        assert versions.index('033_payment_application_live_unique') > versions.index(
            '032_operations_analytics'
        )
        assert '033_payment_application_live_unique_rollback' not in versions

    def test_033_keys_live_applications_only(self):
        """The legacy pair key goes, replaced by one a reversed application (NULL `live`) is
        outside of — so only a live application holds the pair."""
        sql = Path('migrations/033_payment_application_live_unique.sql').read_text()
        assert 'AS (IF(`cancelled` = 0, 1, NULL)) VIRTUAL' in sql
        assert 'DROP KEY IF EXISTS `sales_order_payment_sales_order_customer_payment_idx`' in sql
        assert (
            'ADD UNIQUE KEY IF NOT EXISTS `sales_order_payment_live_uq` '
            '(`sales_order`, `customer_payment`, `live`)'
        ) in sql

    def test_034_is_discovered_and_ordered_after_033(self):
        """Spec 039. A virtual column and a unique key over it are MariaDB's; not run on SQLite."""
        versions = [m.version for m in discover(Path('migrations'))]
        assert versions.index('034_cfdi_stamping_cancellation') > versions.index(
            '033_payment_application_live_unique'
        )
        assert '034_cfdi_stamping_cancellation_rollback' not in versions

    def test_034_keys_open_requests_only(self):
        """At most one open request per document: `open_document` is the document while the
        request is requested or pending, NULL once closed, and unique."""
        sql = Path('migrations/034_cfdi_stamping_cancellation.sql').read_text()
        assert (
            "AS (IF(`status` IN ('requested','pending'), `fiscal_document`, NULL)) VIRTUAL" in sql
        )
        assert 'UNIQUE KEY' in sql
        assert '`open_document`)' in sql

    def test_035_is_discovered_and_ordered_after_034(self):
        """#341. Not run on SQLite, which does not enforce a varchar length."""
        versions = [m.version for m in discover(Path('migrations'))]
        assert versions.index('035_fiscal_document_usage_width') > versions.index(
            '034_cfdi_stamping_cancellation'
        )
        assert '035_fiscal_document_usage_width_rollback' not in versions

    def test_035_widens_around_the_foreign_key(self):
        """MariaDB refuses to change a column a foreign key uses (error 1832), so the key is
        dropped first and re-added after, as it was."""
        statements = split_statements(
            Path('migrations/035_fiscal_document_usage_width.sql').read_text()
        )
        assert [s.split('`fiscal_document`', 1)[1].split()[0] for s in statements] == [
            'DROP',
            'MODIFY',
            'ADD',
        ]
        assert 'MODIFY COLUMN `usage` VARCHAR(4) NULL' in statements[1]
        assert 'ON DELETE NO ACTION ON UPDATE NO ACTION' in statements[2]

    def test_036_is_discovered_and_ordered_after_035(self):
        """#343. Not run on SQLite; the test schema comes from the models."""
        versions = [m.version for m in discover(Path('migrations'))]
        assert versions.index('036_itinerary_stop_delivery_order') > versions.index(
            '035_fiscal_document_usage_width'
        )
        assert '036_itinerary_stop_delivery_order_rollback' not in versions

    def test_036_adds_the_column_its_key_and_its_foreign_key(self):
        """Nullable, with no backfill: the legacy synthetic stops mix orders and customers."""
        statements = split_statements(
            Path('migrations/036_itinerary_stop_delivery_order.sql').read_text()
        )
        assert len(statements) == 3
        assert 'ADD COLUMN IF NOT EXISTS `delivery_order` INT(11) NULL' in statements[0]
        assert 'ADD KEY IF NOT EXISTS' in statements[1]
        assert 'FOREIGN KEY IF NOT EXISTS (`delivery_order`)' in statements[2]
        assert 'REFERENCES `delivery_order` (`delivery_order_id`)' in statements[2]
        assert not any(s.lstrip().upper().startswith('UPDATE') for s in statements)

    def test_038_is_discovered_and_ordered_after_037(self):
        """#349. Not run on SQLite; the test schema comes from the models."""
        versions = [m.version for m in discover(Path('migrations'))]
        assert versions.index('038_cancellation_sat_pending_seen') > versions.index(
            '037_pac_simulator_store'
        )
        assert '038_cancellation_sat_pending_seen_rollback' not in versions

    def test_038_adds_one_nullable_column_with_no_backfill(self):
        """NULL on every existing row keeps today's flow for the open request."""
        statements = split_statements(
            Path('migrations/038_cancellation_sat_pending_seen.sql').read_text()
        )
        assert len(statements) == 1
        assert 'ALTER TABLE `fiscal_document_cancellation`' in statements[0]
        assert 'ADD COLUMN IF NOT EXISTS `sat_pending_at` DATETIME NULL' in statements[0]
        rollback = split_statements(
            Path('migrations/038_cancellation_sat_pending_seen_rollback.sql').read_text()
        )
        assert rollback == [
            'ALTER TABLE `fiscal_document_cancellation` DROP COLUMN IF EXISTS `sat_pending_at`'
        ]
