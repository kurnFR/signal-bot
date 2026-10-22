import os
import tempfile
import unittest
from pathlib import Path

from db.migrate import (MIGRATIONS, Migration, MigrationError, apply_pending, checksum,
                        split_statements, run_statements)

DB_DIR = Path(__file__).resolve().parents[1] / "db"


class DuplicateColumn(Exception):
    errno = 1060


class FakeCursor:
    def __init__(self, conn):
        self.conn, self._rows = conn, []

    def execute(self, sql, params=None):
        text = " ".join(sql.split())
        if "GET_LOCK" in text:
            self._rows = [(1 if self.conn.lock_ok else 0,)]
        elif "RELEASE_LOCK" in text:
            self.conn.released = True
            self._rows = [(1,)]
        elif text.startswith("SELECT version, checksum"):
            self._rows = list(self.conn.recorded.items())
        elif "information_schema.tables" in text:
            self._rows = [(1 if self.conn.has_ohlcv else 0,)]
        elif text.startswith("INSERT INTO schema_migrations"):
            self.conn.recorded[params[0]] = params[2]
            self.conn.statuses[params[0]] = params[3]
        elif text.startswith("CREATE TABLE IF NOT EXISTS schema_migrations"):
            pass
        else:
            self.conn.executed.append(text)
            for needle, exc in self.conn.fail_on.items():
                if needle in text:
                    raise exc

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows

    def close(self):
        pass


class FakeConn:
    def __init__(self, has_ohlcv=False, lock_ok=True, fail_on=None):
        self.has_ohlcv, self.lock_ok = has_ohlcv, lock_ok
        self.fail_on = fail_on or {}
        self.recorded, self.statuses, self.executed, self.released = {}, {}, [], False

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        pass


def write_set(directory, files):
    migrations = []
    for i, (name, sql, upgrade_only) in enumerate(files):
        (Path(directory) / name).write_text(sql)
        migrations.append(Migration(f"{i:03d}_{name}", name, name, upgrade_only))
    return tuple(migrations)


class TestSplitter(unittest.TestCase):
    def test_semicolons_in_comments_and_strings_do_not_split(self):
        sql = """
        -- a comment; with a semicolon
        CREATE TABLE a (x VARCHAR(10) DEFAULT 'a;b'); /* block; comment */
        # hash; comment
        INSERT INTO a VALUES ('it''s; fine'), ("q;uote");
        ALTER TABLE `we;ird` ADD COLUMN y INT
        """
        parts = split_statements(sql)
        self.assertEqual(len(parts), 3)
        self.assertIn("'a;b'", parts[0])
        self.assertIn("it''s; fine", parts[1])
        self.assertTrue(parts[2].startswith("ALTER TABLE `we;ird`"))

    def test_blank_and_trailing_content(self):
        self.assertEqual(split_statements(" ; ;; "), [])
        self.assertEqual(split_statements("SELECT 1"), ["SELECT 1"])

    def test_double_dash_without_space_is_not_a_comment(self):
        self.assertEqual(split_statements("SELECT 5--3;"), ["SELECT 5--3"])

    def test_every_shipped_migration_splits_into_nonempty_statements(self):
        for m in MIGRATIONS:
            parts = split_statements((DB_DIR / m.filename).read_text(encoding="utf-8"))
            self.assertTrue(parts, m.filename)
            self.assertTrue(all(p.strip() for p in parts), m.filename)


class TestRegistry(unittest.TestCase):
    def test_every_file_exists_and_versions_are_unique_and_ordered(self):
        versions = [m.version for m in MIGRATIONS]
        self.assertEqual(versions, sorted(versions))
        self.assertEqual(len(versions), len(set(versions)))
        for m in MIGRATIONS:
            self.assertTrue((DB_DIR / m.filename).is_file(), m.filename)

    def test_every_migration_file_on_disk_is_registered(self):
        registered = {m.filename for m in MIGRATIONS}
        on_disk = {p.name for p in DB_DIR.glob("*.sql")}
        self.assertEqual(on_disk - registered, set())

    def test_baseline_is_first(self):
        self.assertEqual(MIGRATIONS[0].version, "000_baseline")


class TestApply(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_applies_in_order_records_and_is_idempotent(self):
        migs = write_set(self.dir, [("a.sql", "USE x; CREATE TABLE a (i INT);", False),
                                    ("b.sql", "CREATE TABLE b (i INT);", False)])
        conn = FakeConn()
        self.assertEqual(apply_pending(conn, directory=self.dir, migrations=migs, log=lambda _: None),
                         [m.version for m in migs])
        self.assertEqual(conn.executed, ["CREATE TABLE a (i INT)", "CREATE TABLE b (i INT)"])  # USE skipped
        self.assertTrue(conn.released)
        again = apply_pending(conn, directory=self.dir, migrations=migs, log=lambda _: None)
        self.assertEqual(again, [])
        self.assertEqual(len(conn.executed), 2)

    def test_fresh_install_marks_upgrade_only_as_baseline_covered(self):
        migs = write_set(self.dir, [("base.sql", "CREATE TABLE t (i INT);", False),
                                    ("upg.sql", "ALTER TABLE t ADD COLUMN c INT;", True)])
        conn = FakeConn(has_ohlcv=False)
        apply_pending(conn, directory=self.dir, migrations=migs, log=lambda _: None)
        self.assertEqual(conn.executed, ["CREATE TABLE t (i INT)"])
        self.assertEqual(conn.statuses[migs[1].version], "baseline-covered")

    def test_existing_database_runs_upgrade_only_migrations(self):
        migs = write_set(self.dir, [("base.sql", "CREATE TABLE t (i INT);", False),
                                    ("upg.sql", "ALTER TABLE t ADD COLUMN c INT;", True)])
        conn = FakeConn(has_ohlcv=True)
        apply_pending(conn, directory=self.dir, migrations=migs, log=lambda _: None)
        self.assertIn("ALTER TABLE t ADD COLUMN c INT", conn.executed)

    def test_already_applied_errors_are_tolerated_and_flagged(self):
        migs = write_set(self.dir, [("upg.sql", "ALTER TABLE t ADD COLUMN c INT;", True)])
        conn = FakeConn(has_ohlcv=True, fail_on={"ADD COLUMN c": DuplicateColumn("dup")})
        apply_pending(conn, directory=self.dir, migrations=migs, log=lambda _: None)
        self.assertEqual(conn.statuses[migs[0].version], "applied-idempotent")

    def test_real_error_stops_the_run_and_is_not_recorded(self):
        migs = write_set(self.dir, [("a.sql", "CREATE TABLE a (i INT);", False),
                                    ("b.sql", "BROKEN STATEMENT;", False),
                                    ("c.sql", "CREATE TABLE c (i INT);", False)])
        conn = FakeConn(fail_on={"BROKEN": RuntimeError("syntax error")})
        with self.assertRaises(MigrationError):
            apply_pending(conn, directory=self.dir, migrations=migs, log=lambda _: None)
        self.assertIn(migs[0].version, conn.recorded)
        self.assertNotIn(migs[1].version, conn.recorded)
        self.assertNotIn("CREATE TABLE c (i INT)", conn.executed)
        self.assertTrue(conn.released)  # lock released even on failure

    def test_lock_failure_aborts_without_changes(self):
        migs = write_set(self.dir, [("a.sql", "CREATE TABLE a (i INT);", False)])
        conn = FakeConn(lock_ok=False)
        with self.assertRaises(MigrationError):
            apply_pending(conn, directory=self.dir, migrations=migs, log=lambda _: None)
        self.assertEqual(conn.executed, [])

    def test_dry_run_changes_nothing(self):
        migs = write_set(self.dir, [("a.sql", "CREATE TABLE a (i INT);", False)])
        conn = FakeConn()
        out = apply_pending(conn, directory=self.dir, migrations=migs, dry_run=True, log=lambda _: None)
        self.assertEqual(out, [migs[0].version])
        self.assertEqual(conn.executed, [])
        self.assertEqual(conn.recorded, {})

    def test_changed_file_after_apply_is_not_rerun(self):
        migs = write_set(self.dir, [("a.sql", "CREATE TABLE a (i INT);", False)])
        conn = FakeConn()
        apply_pending(conn, directory=self.dir, migrations=migs, log=lambda _: None)
        (self.dir / "a.sql").write_text("CREATE TABLE a2 (i INT);")
        notes = []
        apply_pending(conn, directory=self.dir, migrations=migs, log=notes.append)
        self.assertEqual(conn.executed, ["CREATE TABLE a (i INT)"])
        self.assertTrue(any("changed" in n for n in notes))


@unittest.skipUnless(os.getenv("WALLET_TRACKER_MARIADB_TESTS") == "1", "opt-in MariaDB integration test")
class TestMariaDBIntegration(unittest.TestCase):
    def test_fresh_install_then_rerun(self):
        import mysql.connector
        from db import migrate
        from config import MYSQL_CONFIG

        cfg = dict(MYSQL_CONFIG)
        name = "sb_migrate_it"
        admin = mysql.connector.connect(**{k: v for k, v in cfg.items() if k != "database"})
        cur = admin.cursor()
        cur.execute(f"DROP DATABASE IF EXISTS {name}")
        cur.execute(f"CREATE DATABASE {name}")
        admin.commit()
        conn = mysql.connector.connect(**dict(cfg, database=name))
        try:
            first = migrate.apply_pending(conn, log=lambda _: None)
            self.assertEqual(first, [m.version for m in MIGRATIONS])
            self.assertEqual(migrate.apply_pending(conn, log=lambda _: None), [])
        finally:
            conn.close()
            cur.execute(f"DROP DATABASE IF EXISTS {name}")
            admin.commit()
            admin.close()


    def test_existing_populated_database_adoption_preserves_ohlcv_rows(self):
        """Adopt an existing schema without rewriting historical candle data.

        The baseline SQL represents a pre-tracking database snapshot for this
        rehearsal. The tracking table is deliberately removed before the
        runner is invoked, simulating a legacy install that predates migration
        bookkeeping. This does not replace a rehearsal against a production
        schema snapshot.
        """
        import mysql.connector
        from db import migrate
        from config import MYSQL_CONFIG

        cfg = dict(MYSQL_CONFIG)
        name = "sb_migrate_populated_it"
        admin = mysql.connector.connect(**{k: v for k, v in cfg.items() if k != "database"})
        cur = admin.cursor()
        cur.execute(f"DROP DATABASE IF EXISTS {name}")
        cur.execute(f"CREATE DATABASE {name}")
        admin.commit()
        conn = mysql.connector.connect(**dict(cfg, database=name))
        try:
            # Install the current baseline without recording migration versions.
            cur2 = conn.cursor()
            baseline = next(m for m in MIGRATIONS if m.version == "000_baseline")
            migrate.run_statements(
                cur2,
                migrate.split_statements((DB_DIR / baseline.filename).read_text(encoding="utf-8")),
                label="integration-baseline",
                log=lambda _: None,
            )
            cur2.execute(
                """INSERT INTO ohlcv
                   (symbol, market, timeframe, open_time, close_time, open, high, low,
                    close, volume, quote_volume, num_trades, taker_buy_base_vol,
                    taker_buy_quote_vol, is_closed)
                   VALUES (%s, 'spot', '1m', %s, %s, 100, 102, 99, 101, 12, 1212, 7, 6, 606, 1)""",
                ("MIGRATION_REHEARSAL", 1_790_000_000_000, 1_790_000_059_999),
            )
            conn.commit()
            cur2.execute("SELECT symbol, open_time, close, volume FROM ohlcv WHERE symbol = %s",
                         ("MIGRATION_REHEARSAL",))
            before = cur2.fetchall()
            self.assertEqual(before, [("MIGRATION_REHEARSAL", 1_790_000_000_000, 101, 12)])
            cur2.execute("DROP TABLE schema_migrations")
            conn.commit()
            cur2.close()

            # Existing ohlcv means this is an upgrade/adoption, not a fresh install.
            migrate.apply_pending(conn, log=lambda _: None)
            cur3 = conn.cursor()
            cur3.execute("SELECT symbol, open_time, close, volume FROM ohlcv WHERE symbol = %s",
                         ("MIGRATION_REHEARSAL",))
            after = cur3.fetchall()
            self.assertEqual(after, before)
            cur3.execute("SELECT COUNT(*) FROM schema_migrations")
            self.assertEqual(cur3.fetchone()[0], len(MIGRATIONS))
            cur3.close()
            self.assertEqual(migrate.apply_pending(conn, log=lambda _: None), [])
        finally:
            conn.close()
            cur.execute(f"DROP DATABASE IF EXISTS {name}")
            admin.commit()
            admin.close()


if __name__ == "__main__":
    unittest.main()
