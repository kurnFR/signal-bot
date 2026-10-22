"""Versioned, repeatable database migrations.

Usage::

    python -m db.migrate              # apply everything pending (creates the DB if needed)
    python -m db.migrate --status     # show applied / pending / changed migrations
    python -m db.migrate --dry-run    # list what would run, change nothing

Design:

* ``schema_migrations`` records each applied version with a checksum and timestamp.
* ``000_baseline`` is ``schema.sql``. It already contains the columns added by the
  upgrade-only migrations (``upgrade_only=True``), so on a fresh install those are
  recorded as *baseline-covered* instead of being run.
* Everything is safe to re-run: a statement that fails only because the change is
  already present (duplicate column/key, table exists, can't drop missing key) is
  tolerated and logged. That is also how a legacy, hand-migrated database is adopted.
* Any other error stops the run and the migration is NOT recorded.
* An advisory lock prevents two processes migrating at once.

MySQL/MariaDB DDL is not transactional, so a migration that fails half-way is
re-run from the start next time; the tolerance above is what makes that safe.
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

HERE = Path(__file__).resolve().parent
logger = logging.getLogger("db.migrate")

LOCK_NAME = "signal_bot_schema_migrations"
LOCK_TIMEOUT_SECONDS = 60

# MySQL / MariaDB error numbers meaning "this change is already in place".
TOLERATED_ERRNOS = {
    1050,  # table already exists
    1060,  # duplicate column name
    1061,  # duplicate key name
    1091,  # can't DROP; column/key doesn't exist
    1068,  # multiple primary key defined
    1826,  # duplicate foreign key constraint name
}


@dataclass(frozen=True)
class Migration:
    version: str
    filename: str
    description: str
    upgrade_only: bool = False  # already part of schema.sql for fresh installs


# Order matters. This is the order previously documented in the README.
MIGRATIONS: tuple[Migration, ...] = (
    Migration("000_baseline", "schema.sql", "Core schema (raw data, features, backtests)"),
    Migration("001_users_and_paper", "migrate_add_users_and_paper.sql", "Users and paper-trading tables"),
    Migration("002_paper_accounting", "migrate_p0_paper_accounting.sql", "Paper accounting columns"),
    Migration("003_trail_exit_reason", "migrate_add_trail_exit_reason.sql", "Trailing-stop exit reason"),
    Migration("004_nullable_mark_price", "migrate_nullable_mark_price.sql", "Nullable funding mark price"),
    Migration("005_pro_trader_and_ml", "migrate_pro_trader_and_ml.sql", "ML columns, revoked tokens, audit log"),
    Migration("006_news_events", "migrate_news_events.sql", "News AI events and signals"),
    Migration("007_news_execution", "migrate_news_execution.sql", "News AI execution columns"),
    Migration("008_smart_money", "migrate_smart_money.sql", "Smart-money screener signals"),
    Migration("009_wallet_tracking", "migrate_wallet_tracking.sql", "Wallet tracker tables"),
    Migration("010_wib_columns", "migrate_add_wib_columns.sql", "WIB generated columns", upgrade_only=True),
    Migration("011_data_segment", "migrate_add_data_segment.sql", "Backtest data_segment column", upgrade_only=True),
    Migration("012_fix_wib_timezone", "migrate_fix_wib_timezone.sql", "WIB timezone fix", upgrade_only=True),
    Migration("013_login_attempts", "migrate_login_attempts.sql", "Durable login rate limiting"),
    Migration("014_background_jobs", "migrate_background_jobs.sql", "Durable background job state"),
    Migration("015_system_flags", "migrate_system_flags.sql", "Cross-process emergency-stop flag"),
)


# --------------------------------------------------------------------------- SQL

def split_statements(sql: str) -> list[str]:
    """Split a SQL script on ``;`` outside strings, backtick names and comments.

    Handles ``-- line`` and ``# line`` comments, ``/* block */`` comments (kept out of
    the output), single/double-quoted strings with ``\\`` and doubled-quote escapes.
    """
    statements: list[str] = []
    buf: list[str] = []
    i, n = 0, len(sql)
    quote: str | None = None
    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""
        if quote:
            buf.append(ch)
            if ch == "\\" and quote != "`" and i + 1 < n:
                buf.append(nxt)
                i += 2
                continue
            if ch == quote:
                if nxt == quote:  # doubled quote escape
                    buf.append(nxt)
                    i += 2
                    continue
                quote = None
            i += 1
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            buf.append(ch)
        elif ch == "-" and nxt == "-" and (i + 2 >= n or sql[i + 2] in " \t\r\n"):
            while i < n and sql[i] != "\n":
                i += 1
            continue
        elif ch == "#":
            while i < n and sql[i] != "\n":
                i += 1
            continue
        elif ch == "/" and nxt == "*":
            end = sql.find("*/", i + 2)
            i = n if end == -1 else end + 2
            buf.append(" ")
            continue
        elif ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
        else:
            buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def _is_database_scoping(statement: str) -> bool:
    """``USE db`` / ``CREATE DATABASE``: the runner owns database selection."""
    head = statement.lstrip().upper()
    return head.startswith("USE ") or head.startswith("CREATE DATABASE") or head.startswith("CREATE SCHEMA")


def checksum(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------------ runner

class MigrationError(RuntimeError):
    pass


def _errno(exc: Exception) -> int | None:
    return getattr(exc, "errno", None)


def _read(migration: Migration, directory: Path) -> str:
    path = directory / migration.filename
    if not path.is_file():
        raise MigrationError(f"missing migration file: {path}")
    return path.read_text(encoding="utf-8")


def ensure_tracking_table(cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version     VARCHAR(100) NOT NULL PRIMARY KEY,
            filename    VARCHAR(200) NOT NULL,
            checksum    CHAR(64)     NOT NULL,
            status      VARCHAR(30)  NOT NULL DEFAULT 'applied',
            applied_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB
        """
    )


def applied_versions(cursor) -> dict[str, str]:
    cursor.execute("SELECT version, checksum FROM schema_migrations")
    return {row[0]: row[1] for row in cursor.fetchall()}


def _table_exists(cursor, name: str) -> bool:
    cursor.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = %s",
        (name,),
    )
    return int(cursor.fetchone()[0]) > 0


def plan(cursor, directory: Path = HERE, migrations: Sequence[Migration] = MIGRATIONS) -> list[dict]:
    """Return one entry per migration: state is applied | pending | changed."""
    done = applied_versions(cursor)
    out = []
    for m in migrations:
        current = checksum(_read(m, directory))
        if m.version not in done:
            state = "pending"
        elif done[m.version] != current:
            state = "changed"  # applied earlier, file edited since (informational)
        else:
            state = "applied"
        out.append({"version": m.version, "filename": m.filename, "description": m.description,
                    "state": state, "upgrade_only": m.upgrade_only})
    return out


def run_statements(cursor, statements: Iterable[str], *, label: str, log: Callable[[str], None] = logger.info) -> int:
    """Execute statements, tolerating 'already applied' errors. Returns tolerated count."""
    tolerated = 0
    for index, statement in enumerate(statements, start=1):
        if _is_database_scoping(statement):
            continue
        try:
            cursor.execute(statement)
        except Exception as exc:  # driver-specific error classes
            code = _errno(exc)
            if code in TOLERATED_ERRNOS:
                tolerated += 1
                log(f"{label}: statement {index} already applied (errno {code}); continuing")
                continue
            preview = " ".join(statement.split())[:120]
            raise MigrationError(f"{label}: statement {index} failed: {exc} [{preview}]") from exc
    return tolerated


def apply_pending(
    conn,
    *,
    directory: Path = HERE,
    migrations: Sequence[Migration] = MIGRATIONS,
    dry_run: bool = False,
    log: Callable[[str], None] = logger.info,
) -> list[str]:
    """Apply pending migrations in order. Returns the versions that were applied/recorded."""
    cursor = conn.cursor()
    cursor.execute("SELECT GET_LOCK(%s, %s)", (LOCK_NAME, LOCK_TIMEOUT_SECONDS))
    locked = cursor.fetchone()
    if not locked or int(locked[0] or 0) != 1:
        raise MigrationError("could not acquire the migration lock (another migration is running?)")
    handled: list[str] = []
    try:
        ensure_tracking_table(cursor)
        done = applied_versions(cursor)
        fresh_install = not _table_exists(cursor, "ohlcv")

        for m in migrations:
            text = _read(m, directory)
            digest = checksum(text)
            if m.version in done:
                if done[m.version] != digest:
                    log(f"NOTE {m.version}: {m.filename} changed since it was applied; not re-run")
                continue

            if m.upgrade_only and fresh_install and migrations[0].version in handled:
                if dry_run:
                    log(f"would record {m.version} as covered by baseline")
                else:
                    _record(cursor, conn, m, digest, "baseline-covered")
                    log(f"recorded {m.version} (covered by baseline)")
                handled.append(m.version)
                continue

            if dry_run:
                log(f"would apply {m.version}  {m.filename}")
                handled.append(m.version)
                continue

            log(f"applying {m.version}  {m.filename}")
            tolerated = run_statements(cursor, split_statements(text), label=m.version, log=log)
            _record(cursor, conn, m, digest, "applied" if not tolerated else "applied-idempotent")
            handled.append(m.version)
        return handled
    finally:
        try:
            cursor.execute("SELECT RELEASE_LOCK(%s)", (LOCK_NAME,))
            cursor.fetchall()
        except Exception:  # pragma: no cover - connection already gone
            pass
        cursor.close()


def _record(cursor, conn, m: Migration, digest: str, status: str) -> None:
    cursor.execute(
        "INSERT INTO schema_migrations (version, filename, checksum, status) VALUES (%s, %s, %s, %s) "
        "ON DUPLICATE KEY UPDATE filename = VALUES(filename), checksum = VALUES(checksum), status = VALUES(status)",
        (m.version, m.filename, digest, status),
    )
    conn.commit()


# --------------------------------------------------------------------------- CLI

def _connect(create_database: bool = True):
    import mysql.connector

    from config import MYSQL_CONFIG

    cfg = dict(MYSQL_CONFIG)
    database = cfg.pop("database")
    conn = mysql.connector.connect(init_command="SET time_zone = '+00:00'", **cfg)
    cursor = conn.cursor()
    if create_database:
        safe = database.replace("`", "")
        cursor.execute(
            f"CREATE DATABASE IF NOT EXISTS `{safe}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        )
    cursor.execute(f"USE `{database.replace('`', '')}`")
    cursor.close()
    return conn


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply database migrations")
    parser.add_argument("--status", action="store_true", help="show migration state and exit")
    parser.add_argument("--dry-run", action="store_true", help="show what would run, change nothing")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    try:
        conn = _connect(create_database=not args.status)
    except Exception as exc:
        print(f"Cannot connect to the database: {exc}", file=sys.stderr)
        return 2
    try:
        if args.status:
            cur = conn.cursor()
            ensure_tracking_table(cur)
            conn.commit()
            for row in plan(cur):
                print(f"{row['state']:<8} {row['version']:<28} {row['description']}")
            cur.close()
            return 0
        applied = apply_pending(conn, dry_run=args.dry_run)
        print(f"{'Would apply' if args.dry_run else 'Applied'} {len(applied)} migration(s)." if applied
              else "Database is up to date.")
        return 0
    except MigrationError as exc:
        print(f"MIGRATION FAILED: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
