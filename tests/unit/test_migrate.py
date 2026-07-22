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
