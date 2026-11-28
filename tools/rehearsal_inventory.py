"""Read-only inventory of an isolated rehearsal database.

Every base table is counted. Row hashes use all columns in ordinal order,
except added News AI fields, so the original settings remain comparable
across migration 039. SQL hashes each row with explicit NULL markers and
hex-encoded values. Summing those digests modulo 2**256 retains duplicates
and makes the fingerprint independent of row order.
"""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

VERSION = 1
PREFIX = "signal_bot_rehearsal_"
LOCAL_HOSTS = {"127.0.0.1", "localhost"}
KEY_TABLES = {"ohlcv", "users", "service_control", "app_telegram_settings", "custom_strategies"}
TIMESTAMP_TYPES = {"date", "datetime", "timestamp"}
NUMERIC_COLUMNS = {
    "open_time", "close_time", "entry_time", "exit_time", "created_at",
    "volume", "quote_volume", "quantity", "net_pnl", "pnl", "allocated_capital",
    "attempts", "is_active", "enabled", "delivery_authorized",
}
NEWS_BASELINE = {"id", "min_confidence", "max_trades_per_day", "updated_by", "updated_at"}


def validate_target(config, allow_hosts=(), prefix=PREFIX):
    database = config.get("database", "")
    if prefix != PREFIX or not database.startswith(prefix) or len(database) == len(prefix):
        raise ValueError("Database must start with signal_bot_rehearsal_ and include a suffix.")
    if config.get("host") not in LOCAL_HOSTS.union(allow_hosts):
        raise ValueError("Database host is outside the rehearsal allowlist.")
    if config.get("unix_socket"):
        raise ValueError("Rehearsal connections require a TCP host.")


@contextmanager
def read_only_connection(config, allow_hosts=(), prefix=PREFIX):
    validate_target(config, allow_hosts, prefix)
    import mysql.connector

    with mysql.connector.connect(**config, connection_timeout=5) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION TRANSACTION READ ONLY")
            cursor.execute("SELECT DATABASE()")
            actual, = cursor.fetchone()
            validate_target({**config, "database": actual}, allow_hosts, prefix)
            if actual != config["database"]:
                raise ValueError("Connected database does not match the requested rehearsal DB.")
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
        try:
            yield connection
        finally:
            connection.rollback()


def safe_output(path):
    path = Path(path).resolve()
    parent = next(directory for directory in path.parents if directory.exists())
    result = subprocess.run(
        ["git", "-C", str(parent), "rev-parse", "--is-inside-work-tree"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode == 0:
        raise ValueError("Evidence output must be outside all Git work trees.")
    if "not a git repository" not in result.stderr:
        raise ValueError("Could not verify that the evidence directory is outside Git.")
    return path


def write_snapshot(path, data):
    path = safe_output(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        json.dump(data, output, default=str, sort_keys=True, indent=2)
        output.write("\n")


def identifier(name):
    return "`" + name.replace("`", "``") + "`"


def fetch(cursor, sql, args=()):
    cursor.execute(sql, args)
    return cursor.fetchall()


def signature(value):
    serialized = json.dumps(value, default=str, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()


def table_data(cursor, table, columns):
    names = [row[0] for row in columns if table != "news_ai_settings" or row[0] in NEWS_BASELINE]
    values = ", ".join(f"IFNULL(HEX(CAST({identifier(name)} AS BINARY)), '-')" for name in names)
    cursor.execute(f"SELECT SHA2(CONCAT_WS('|', {values}), 256) FROM {identifier(table)}")
    count = total = 0
    for digest, in cursor:
        count += 1
        total += int(digest, 16)
    result = {"rows": count, "content_hash": f"{total % (1 << 256):064x}"}
    if table in KEY_TABLES or table.startswith(("paper_", "wallet_")):
        expressions, keys = [], []
        for name, _, data_type, *_ in columns:
            if name in names and (data_type in TIMESTAMP_TYPES or name.endswith(("_at", "_time"))):
                operators = ("MIN", "MAX")
            elif name in names and name in NUMERIC_COLUMNS:
                operators = ("SUM", "COUNT")
            else:
                continue
            for operator in operators:
                keys.append(f"{operator.lower()}_{name}")
                expressions.append(f"{operator}({identifier(name)})")
        if expressions:
            aggregates, = fetch(cursor, f"SELECT {', '.join(expressions)} FROM {identifier(table)}")
            result["aggregates"] = dict(zip(keys, aggregates))
    return result


def snapshot(connection):
    with connection.cursor() as cursor:
        (database, server), = fetch(cursor, "SELECT DATABASE(), VERSION()")
        table_names = fetch(cursor, """SELECT TABLE_NAME FROM information_schema.TABLES
            WHERE TABLE_SCHEMA=DATABASE() AND TABLE_TYPE='BASE TABLE' ORDER BY TABLE_NAME""")
        tables = {}
        migrations = []
        for table, in table_names:
            columns = fetch(cursor, """SELECT COLUMN_NAME, COLUMN_TYPE, DATA_TYPE, IS_NULLABLE,
                COLUMN_KEY, COLUMN_DEFAULT, EXTRA, COLLATION_NAME, GENERATION_EXPRESSION
                FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s
                ORDER BY ORDINAL_POSITION""", (table,))
            indexes = fetch(cursor, """SELECT INDEX_NAME, NON_UNIQUE, SEQ_IN_INDEX, COLUMN_NAME,
                COLLATION, SUB_PART, INDEX_TYPE FROM information_schema.STATISTICS
                WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s ORDER BY INDEX_NAME, SEQ_IN_INDEX""", (table,))
            keys = fetch(cursor, """SELECT CONSTRAINT_NAME, COLUMN_NAME, ORDINAL_POSITION,
                REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE
                WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s ORDER BY CONSTRAINT_NAME, ORDINAL_POSITION""", (table,))
            tables[table] = {"schema": signature([columns, indexes, keys]), **table_data(cursor, table, columns)}
            if table == "schema_migrations":
                migrations = [
                    {"version": version, "checksum": checksum}
                    for version, checksum in fetch(cursor, "SELECT version, checksum FROM schema_migrations ORDER BY version")
                ]
        return {"tool_version": VERSION, "utc": datetime.now(timezone.utc).isoformat(),
                "database": database, "server_version": server, "migrations": migrations, "tables": tables}


def compare(before, after, expected, emit=print):
    old, new = before["tables"], after["tables"]
    allowed = expected.get("tables", {})
    if not isinstance(allowed, dict) or any(
        not isinstance(fields, list) or not all(isinstance(field, str) for field in fields)
        for fields in allowed.values()
    ):
        raise ValueError("Expected table changes must be lists of exact field names.")
    additions = expected.get("migrations_added", [])
    if not isinstance(additions, list) or not all(isinstance(version, str) for version in additions):
        raise ValueError("Expected migration additions must be a list of versions.")
    failed = before["tool_version"] != after["tool_version"]
    if failed:
        emit("Inventory versions differ.")
    for table in sorted(old.keys() | new.keys()):
        if table not in old:
            changes = ["added"]
        elif table not in new:
            changes = ["removed"]
        else:
            changes = sorted(key for key in old[table].keys() | new[table].keys()
                             if old[table].get(key) != new[table].get(key))
        for change in changes:
            expected_change = change in allowed.get(table, [])
            label = change
            if change == "rows":
                label = f"rows {old[table]['rows']} → {new[table]['rows']}"
            emit(f"{table}: {label} ({'expected' if expected_change else 'unexpected'})")
            failed |= not expected_change
    old_migrations = {row["version"]: row["checksum"] for row in before["migrations"]}
    new_migrations = {row["version"]: row["checksum"] for row in after["migrations"]}
    for version in sorted(old_migrations.keys() | new_migrations.keys()):
        if old_migrations.get(version) == new_migrations.get(version):
            continue
        expected_change = version not in old_migrations and version in additions
        emit(f"migration {version}: {'expected addition' if expected_change else 'unexpected change'}")
        failed |= not expected_change
    if not failed:
        emit("Inventory comparison passed.")
    return int(failed)


def add_guard_options(parser):
    parser.add_argument("--allow-host", action="append", default=[])
    parser.add_argument("--rehearsal-db-name-prefix", default=PREFIX)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    capture = commands.add_parser("snapshot")
    add_guard_options(capture)
    capture.add_argument("--out", type=Path)
    diff = commands.add_parser("compare")
    diff.add_argument("before", type=Path)
    diff.add_argument("after", type=Path)
    diff.add_argument("--expect", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "compare":
            return compare(json.loads(args.before.read_text()), json.loads(args.after.read_text()),
                           json.loads(args.expect.read_text()) if args.expect else {})
        from config import MYSQL_CONFIG

        out_dir = os.getenv("REHEARSAL_OUT")
        if args.out is None and not out_dir:
            parser.error("provide --out or set REHEARSAL_OUT")
        output = safe_output(args.out or Path(out_dir) / "inventory.json")
        with read_only_connection(MYSQL_CONFIG, args.allow_host, args.rehearsal_db_name_prefix) as connection:
            data = snapshot(connection)
        write_snapshot(output, data)
        print(f"Snapshot written for {data['database']}.")
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f"Inventory refused or failed ({type(exc).__name__}). Check the rehearsal target and evidence paths.", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Inventory failed ({type(exc).__name__}).", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
