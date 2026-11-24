#!/usr/bin/env python3
"""CLI utility to mint and verify commercial SB1 license keys for Signal Bot.

Usage:
    # Mint a 365-day Pro license for up to 10 seats
    python scripts/generate_license.py \
        --customer "Acme Quant Desk" \
        --tier pro \
        --max-users 10 \
        --days 365 \
        --signing-key "$SIGNAL_BOT_LICENSE_SIGNING_KEY"

    # Verify an existing license key
    python scripts/generate_license.py --verify "SB1...." --signing-key "$SIGNAL_BOT_LICENSE_SIGNING_KEY"
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from web.licensing import VALID_TIERS, sign_license_key, verify_license_key  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mint or verify Signal Bot commercial SB1 license keys.")
    parser.add_argument("--verify", dest="verify_key", help="Verify an existing SB1 license key and print JSON status.")
    parser.add_argument("--customer", help="Customer or organization name (required when minting).")
    parser.add_argument("--tier", default="pro", choices=[t for t in VALID_TIERS if t != "development"], help="License tier.")
    parser.add_argument("--max-users", type=int, default=10, help="Maximum active user seats allowed.")
    parser.add_argument("--days", type=int, default=365, help="Validity duration in days (0 = perpetual).")
    parser.add_argument(
        "--signing-key",
        default=None,
        help="HMAC signing secret (defaults to SIGNAL_BOT_LICENSE_SIGNING_KEY or APP_SECRET_KEY env var).",
    )
    args = parser.parse_args(argv)

    signing_key = (
        args.signing_key
        or os.getenv("SIGNAL_BOT_LICENSE_SIGNING_KEY", "").strip()
        or os.getenv("APP_SECRET_KEY", "").strip()
    )

    if args.verify_key:
        result = verify_license_key(args.verify_key, signing_key=signing_key)
        print(json.dumps(result, indent=2))
        return 0 if result.get("valid") else 1

    if not args.customer:
        parser.error("--customer is required when minting a license key (or pass --verify).")
    if args.days < 0:
        parser.error("--days must be >= 0")

    expires_at = int(time.time()) + args.days * 86400 if args.days > 0 else None
    key = sign_license_key(
        customer=args.customer,
        tier=args.tier,
        max_users=args.max_users,
        expires_at=expires_at,
        signing_key=signing_key,
    )
    print(key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
