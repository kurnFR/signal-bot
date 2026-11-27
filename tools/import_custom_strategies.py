"""Import legacy custom strategies without replacing saved rows."""

import argparse
import json
import os
from pathlib import Path
import sys

from db.custom_strategy_store import (
    InvalidLegacyDefinitions, import_legacy_definitions, load_legacy_definitions,
    validate_legacy_definitions,
)
from db.db import _owned_cursor, get_pool


def run(path, dry_run):
    definitions = load_legacy_definitions(path)
    validate_legacy_definitions(definitions)
    conn = get_pool().get_connection()
    with _owned_cursor(conn) as cursor:
        if dry_run:
            cursor.execute("SELECT name FROM custom_strategies")
            existing = {name for name, in cursor.fetchall()}
            present = sum(definition["name"] in existing for definition in definitions)
            return len(definitions), 0, present, 0
        inserted = import_legacy_definitions(cursor, definitions)
        conn.commit()
        return len(definitions), inserted, len(definitions) - inserted, 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        if not os.environ.get("MYSQL_DATABASE"):
            raise ValueError("Set the target MYSQL_DATABASE explicitly")
        counts = run(args.path, args.dry_run)
    except InvalidLegacyDefinitions as exc:
        counts = (exc.found, 0, 0, exc.invalid)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"WARNING: Legacy import source unavailable or invalid ({type(exc).__name__}).", file=sys.stderr)
        counts = (0, 0, 0, 1)
    except Exception as exc:
        print(f"Import failed ({type(exc).__name__}).", file=sys.stderr)
        return 1
    print(dict(zip(("found", "inserted", "already_present", "invalid"), counts)))
    return int(counts[-1] != 0)


if __name__ == "__main__":
    raise SystemExit(main())
