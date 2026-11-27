"""One-time migration helper: map rdt_strategy_lifecycle flags to paper_configs.

Dry-run by default (prints a migration plan without writing anything).
Pass --apply to write mapped configurations into paper_configs.
Never mutates any rdt_* table and never auto-runs on import.
"""
import argparse
import os
import sys
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.strategies.retailbot2_strategies import RETAILBOT2_STRATEGY_NAMES
from db.db import get_pool

VALID_MARKETS = {"spot", "futures"}
VALID_TIMEFRAMES = {"15m", "1h", "4h", "1d"}


def map_lifecycle_strategy_name(strategy_id: str, mode: str) -> Optional[str]:
    """Translate (strategy_id, mode) from rdt_strategy_lifecycle to Paper Trading strategy_name."""
    raw = (strategy_id or "").strip()
    norm_mode = (mode or "").strip().lower()
    if norm_mode not in {"shadow", "inverse"} or not raw:
        return None
    root = raw[8:] if raw.startswith("inverse_") else (raw[:-4] if raw.endswith("_INV") else raw)
    candidate = (
        f"{root}_INV"
        if (norm_mode == "inverse" or raw.endswith("_INV") or raw.startswith("inverse_"))
        else root
    )
    return candidate if candidate in RETAILBOT2_STRATEGY_NAMES else None


def parse_pair_spec(
    spec: str,
    default_market: str = "spot",
    default_timeframe: str = "1h",
) -> Tuple[str, Optional[str], Optional[str]]:
    """Parse 'SYMBOL' or 'SYMBOL:market:timeframe' into (symbol, market_or_none, timeframe_or_none)."""
    parts = [p.strip() for p in spec.split(":")]
    symbol = parts[0].upper()
    if not symbol or not symbol.isalnum():
        raise ValueError(f"Invalid pair specification: {spec!r}")
    if len(parts) == 1:
        return symbol, None, None
    if len(parts) != 3:
        raise ValueError(f"Pair specification must be SYMBOL or SYMBOL:market:timeframe, got {spec!r}")
    market = parts[1].lower()
    timeframe = parts[2]
    if market not in VALID_MARKETS:
        raise ValueError(f"Invalid market in {spec!r}: must be one of {sorted(VALID_MARKETS)}")
    if timeframe not in VALID_TIMEFRAMES:
        raise ValueError(f"Invalid timeframe in {spec!r}: must be one of {sorted(VALID_TIMEFRAMES)}")
    return symbol, market, timeframe


def resolve_target_pairs(
    existing_pairs: Sequence[Tuple[str, str, str]],
    pair_specs: Optional[Sequence[str]] = None,
    *,
    default_market: str = "spot",
    default_timeframe: str = "1h",
    keep_existing: bool = True,
) -> List[Tuple[str, str, str]]:
    """Determine target (symbol, market, timeframe) tuples while preserving existing pair/timeframes."""
    if default_market not in VALID_MARKETS:
        raise ValueError(f"default_market must be one of {sorted(VALID_MARKETS)}")
    if default_timeframe not in VALID_TIMEFRAMES:
        raise ValueError(f"default_timeframe must be one of {sorted(VALID_TIMEFRAMES)}")

    existing_clean = sorted({(s.upper(), m.lower(), tf) for s, m, tf in existing_pairs})
    if not pair_specs:
        return existing_clean

    targets = set(existing_clean) if keep_existing else set()
    existing_by_symbol: Dict[str, List[Tuple[str, str, str]]] = {}
    for s, m, tf in existing_clean:
        existing_by_symbol.setdefault(s, []).append((s, m, tf))

    for spec in pair_specs:
        symbol, market, timeframe = parse_pair_spec(spec, default_market, default_timeframe)
        if market is not None and timeframe is not None:
            targets.add((symbol, market, timeframe))
        elif symbol in existing_by_symbol:
            targets.update(existing_by_symbol[symbol])
        else:
            targets.add((symbol, default_market, default_timeframe))

    return sorted(targets)


def build_migration_plan(
    lifecycle_rows: Iterable[Dict[str, Any]],
    target_pairs: Sequence[Tuple[str, str, str]],
    existing_configs: Iterable[Dict[str, Any]],
    *,
    strategies_filter: Optional[Sequence[str]] = None,
    allocated_capital: float = 5000.0,
    risk_per_trade_pct: float = 1.0,
) -> List[Dict[str, Any]]:
    """Compute CREATE/UPDATE/UNCHANGED/SKIP entries without mutating any DB table."""
    allowed_strategies = {s.strip() for s in strategies_filter} if strategies_filter else None
    existing_map: Dict[Tuple[str, str, str, str], Dict[str, Any]] = {}
    for cfg in existing_configs:
        if cfg.get("ml_model_id") or cfg.get("ml_generator_model_id"):
            continue
        key = (
            str(cfg["symbol"]).upper(),
            str(cfg["market"]).lower(),
            str(cfg["timeframe"]),
            str(cfg["strategy_name"]),
        )
        existing_map[key] = cfg

    plan: List[Dict[str, Any]] = []
    for row in lifecycle_rows:
        strategy_id = str(row["strategy_id"])
        mode = str(row["mode"])
        strategy_name = map_lifecycle_strategy_name(strategy_id, mode)
        if strategy_name is None:
            plan.append({
                "action": "SKIP",
                "reason": "unmapped_strategy_or_mode",
                "source_strategy_id": strategy_id,
                "source_mode": mode,
                "strategy_name": None,
                "symbol": None,
                "market": None,
                "timeframe": None,
            })
            continue
        if allowed_strategies is not None and strategy_name not in allowed_strategies:
            continue

        is_active = bool(row.get("evaluation_enabled", False))
        telegram_enabled = bool(row.get("telegram_enabled", False))
        report_enabled = bool(row.get("report_enabled", True))

        for symbol, market, timeframe in target_pairs:
            existing = existing_map.get((symbol, market, timeframe, strategy_name))
            if existing is None:
                plan.append({
                    "action": "CREATE",
                    "source_strategy_id": strategy_id,
                    "source_mode": mode,
                    "strategy_name": strategy_name,
                    "symbol": symbol,
                    "market": market,
                    "timeframe": timeframe,
                    "is_active": is_active,
                    "telegram_enabled": telegram_enabled,
                    "report_enabled": report_enabled,
                    "allocated_capital": allocated_capital,
                    "risk_per_trade_pct": risk_per_trade_pct,
                    "existing_id": None,
                })
            else:
                unchanged = (
                    bool(existing.get("is_active", False)) == is_active
                    and bool(existing.get("telegram_enabled", False)) == telegram_enabled
                    and bool(existing.get("report_enabled", True)) == report_enabled
                )
                plan.append({
                    "action": "UNCHANGED" if unchanged else "UPDATE",
                    "source_strategy_id": strategy_id,
                    "source_mode": mode,
                    "strategy_name": strategy_name,
                    "symbol": symbol,
                    "market": market,
                    "timeframe": timeframe,
                    "is_active": is_active,
                    "telegram_enabled": telegram_enabled,
                    "report_enabled": report_enabled,
                    "allocated_capital": float(existing.get("allocated_capital", allocated_capital)),
                    "risk_per_trade_pct": float(existing.get("risk_per_trade_pct", risk_per_trade_pct)),
                    "existing_id": existing.get("id"),
                })
    return plan


def fetch_source_and_existing_state(conn) -> Tuple[List[Dict[str, Any]], List[Tuple[str, str, str]], List[Dict[str, Any]]]:
    """Read rdt_strategy_lifecycle and paper_configs cleanly without mutating any table."""
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """
            SELECT strategy_id, mode, evaluation_enabled, telegram_enabled, report_enabled
            FROM rdt_strategy_lifecycle
            ORDER BY strategy_id, mode
            """
        )
        lifecycle_rows = cur.fetchall()
        cur.execute(
            """
            SELECT id, symbol, market, timeframe, strategy_name, is_active,
                   allocated_capital, risk_per_trade_pct, ml_model_id,
                   ml_generator_model_id, telegram_enabled, report_enabled, archived_at
            FROM paper_configs
            ORDER BY symbol, market, timeframe, strategy_name
            """
        )
        existing_configs = cur.fetchall()
    finally:
        cur.close()

    existing_pairs = sorted({
        (str(r["symbol"]).upper(), str(r["market"]).lower(), str(r["timeframe"]))
        for r in existing_configs
    })
    return lifecycle_rows, existing_pairs, existing_configs


def apply_migration_plan(conn, plan: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    """Write CREATE/UPDATE plan items to paper_configs only. Never touches rdt_* tables."""
    counts = {"created": 0, "updated": 0, "unchanged": 0, "skipped": 0}
    cur = conn.cursor()
    try:
        for item in plan:
            action = item["action"]
            if action == "SKIP":
                counts["skipped"] += 1
                continue
            if action == "UNCHANGED":
                counts["unchanged"] += 1
                continue
            if action == "UPDATE":
                cur.execute(
                    """
                    UPDATE paper_configs
                    SET is_active = %s, telegram_enabled = %s, report_enabled = %s
                    WHERE id = %s
                    """,
                    (
                        1 if item["is_active"] else 0,
                        1 if item["telegram_enabled"] else 0,
                        1 if item["report_enabled"] else 0,
                        int(item["existing_id"]),
                    ),
                )
                counts["updated"] += 1
            elif action == "CREATE":
                cur.execute(
                    """
                    INSERT INTO paper_configs
                        (symbol, market, timeframe, strategy_name, is_active,
                         allocated_capital, risk_per_trade_pct, telegram_enabled, report_enabled)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        item["symbol"],
                        item["market"],
                        item["timeframe"],
                        item["strategy_name"],
                        1 if item["is_active"] else 0,
                        item["allocated_capital"],
                        item["risk_per_trade_pct"],
                        1 if item["telegram_enabled"] else 0,
                        1 if item["report_enabled"] else 0,
                    ),
                )
                counts["created"] += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
    return counts


def format_plan(plan: Sequence[Dict[str, Any]], *, apply: bool) -> str:
    lines = [
        f"RetailBot2 lifecycle -> paper_configs migration plan ({'APPLY' if apply else 'DRY-RUN'})",
        f"{'ACTION':10} {'SOURCE':22} {'TARGET STRATEGY':16} {'PAIR':12} {'MKT':7} {'TF':4} {'ACT':3} {'TG':3} {'RPT':3}",
    ]
    for item in plan:
        src = f"{item['source_strategy_id']}:{item['source_mode']}"
        if item["action"] == "SKIP":
            lines.append(f"{'SKIP':10} {src:22} ({item.get('reason', 'skipped')})")
            continue
        lines.append(
            f"{item['action']:10} {src:22} {item['strategy_name']:16} "
            f"{item['symbol']:12} {item['market']:7} {item['timeframe']:4} "
            f"{int(item['is_active']):3} {int(item['telegram_enabled']):3} {int(item['report_enabled']):3}"
        )
    if not apply:
        lines.append("DRY RUN: no changes written (pass --apply to execute).")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pairs",
        help="Comma-separated symbols (BTCUSDT,ETHUSDT) or SYMBOL:market:timeframe specs.",
    )
    parser.add_argument("--market", default="spot", choices=sorted(VALID_MARKETS))
    parser.add_argument("--timeframe", default="1h", choices=sorted(VALID_TIMEFRAMES))
    parser.add_argument(
        "--no-keep-existing",
        action="store_true",
        help="When --pairs is passed, do not retain unrelated existing paper_configs pairs.",
    )
    parser.add_argument("--strategies", help="Optional comma-separated Paper strategy names to include.")
    parser.add_argument("--capital", type=float, default=5000.0)
    parser.add_argument("--risk-pct", type=float, default=1.0)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write CREATE/UPDATE rows to paper_configs (default is dry-run).",
    )
    args = parser.parse_args(argv)

    pair_specs = [p.strip() for p in args.pairs.split(",") if p.strip()] if args.pairs else None
    strategies_filter = [s.strip() for s in args.strategies.split(",") if s.strip()] if args.strategies else None

    conn = get_pool().get_connection()
    try:
        lifecycle_rows, existing_pairs, existing_configs = fetch_source_and_existing_state(conn)
        target_pairs = resolve_target_pairs(
            existing_pairs,
            pair_specs,
            default_market=args.market,
            default_timeframe=args.timeframe,
            keep_existing=not args.no_keep_existing,
        )
        if not target_pairs:
            print("No target pairs resolved (specify --pairs or ensure paper_configs has existing rows).")
            return 1
        plan = build_migration_plan(
            lifecycle_rows,
            target_pairs,
            existing_configs,
            strategies_filter=strategies_filter,
            allocated_capital=args.capital,
            risk_per_trade_pct=args.risk_pct,
        )
        print(format_plan(plan, apply=args.apply))
        if args.apply:
            counts = apply_migration_plan(conn, plan)
            print(f"Applied: {counts}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
