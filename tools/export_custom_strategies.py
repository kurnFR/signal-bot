"""Export saved custom strategies for rollback to the JSON-backed release."""

import argparse
import json
import os
from pathlib import Path
import sys

from db.custom_strategy_store import export_definitions
from db.db import _owned_cursor, get_pool


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if not os.environ.get("MYSQL_DATABASE"):
            raise ValueError("Set the source MYSQL_DATABASE explicitly")
        with _owned_cursor(get_pool().get_connection()) as cursor:
            definitions = list(export_definitions(cursor))
        serialized = json.dumps(definitions, indent=2, allow_nan=False) + "\n"
        fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(serialized)
        print(f"Exported {len(definitions)} strategies.")
        return 0
    except Exception as exc:
        print(f"Export failed ({type(exc).__name__}).", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
