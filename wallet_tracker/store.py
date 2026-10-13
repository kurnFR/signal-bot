import config
"""MySQL persistence for wallet tracking. Uses the shared pool in db.db."""
import logging
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from db.db import get_pool
from wallet_tracker.chains import CHAINS, explorer_url, tx_url

logger = logging.getLogger("wallet_tracker.store")

_MIGRATION = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "db", "migrate_wallet_tracking.sql")


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _clean(row, wallet_chain_keys=True):
    """Decimal -> float, datetime -> ISO string, add explorer link."""
    for k, v in list(row.items()):
        if isinstance(v, Decimal):
            row[k] = float(v)
        elif isinstance(v, datetime):
            row[k] = v.isoformat(sep=" ")
    if wallet_chain_keys and "chain" in row:
        if "wallet" in row and row.get("wallet"):
            row["explorer_url"] = explorer_url(row["chain"], row["wallet"])
        if "tx_hash" in row and row.get("tx_hash"):
            row["tx_url"] = tx_url(row["chain"], row["tx_hash"])
        row["chain_label"] = CHAINS.get(row["chain"], {}).get("label", row["chain"])
    return row


def ensure_tables():
    with open(_MIGRATION, "r", encoding="utf-8") as fh:
        raw = fh.read()
    cleaned = "\n".join(l for l in raw.splitlines() if not l.strip().startswith("--"))
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor()
        for stmt in cleaned.split(";"):
            stmt = stmt.strip()
            if stmt.upper().startswith("CREATE TABLE"):
                cur.execute(stmt)
        conn.commit()
        cur.close()
    finally:
        conn.close()


# ---------------------------------------------------------------- ingestion
def insert_swaps(swaps):
    """INSERT IGNORE (idempotent: re-reading the same trades is harmless).
    Returns the number of NEW rows."""
    if not swaps:
        return 0
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor()
        new = 0
        for s in swaps:
            cur.execute(
                """INSERT IGNORE INTO wallet_swaps
                   (chain, wallet, pool_address, pair, side, usd_value, token_amount,
                    price_usd, tx_hash, block_time)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (s["chain"], s["wallet"], s["pool_address"], s["pair"], s["side"],
                 s["usd_value"], s["token_amount"], s["price_usd"], s["tx_hash"], s["block_time"]),
            )
            new += cur.rowcount
        conn.commit()
        cur.close()
        return new
    finally:
        conn.close()


def load_swaps_since(since):
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """SELECT chain, wallet, pool_address, pair, side, usd_value, token_amount, block_time
               FROM wallet_swaps WHERE block_time >= %s""", (since,))
        rows = cur.fetchall()
        cur.close()
        for r in rows:
            r["usd_value"] = float(r["usd_value"])
            r["token_amount"] = float(r["token_amount"])
        return rows
    finally:
        conn.close()


def prune_swaps(retention_days):
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM wallet_swaps WHERE block_time < %s",
                    (_utcnow() - timedelta(days=retention_days),))
        conn.commit()
        n = cur.rowcount
        cur.close()
        return n
    finally:
        conn.close()


_POS_COLS = ("chain", "wallet", "pool_address", "pair", "period_hours", "side", "bias",
             "buy_usd", "sell_usd", "net_usd", "total_usd", "buy_tokens", "sell_tokens",
             "net_tokens", "avg_buy_price", "avg_sell_price", "avg_price", "trade_count",
             "buy_count", "sell_count", "first_trade_at", "last_trade_at")


def replace_positions(period_hours, positions):
    """Atomically swap the stored candidate set for one period (InnoDB
    transaction: readers see either the old set or the new one, never half)."""
    conn = get_pool().get_connection()
    try:
        conn.start_transaction()
        cur = conn.cursor()
        cur.execute("DELETE FROM wallet_positions WHERE period_hours = %s", (period_hours,))
        if positions:
            cols = ", ".join(_POS_COLS)
            marks = ", ".join(["%s"] * len(_POS_COLS))
            cur.executemany(
                f"INSERT INTO wallet_positions ({cols}) VALUES ({marks})",
                [tuple(p[c] for c in _POS_COLS) for p in positions],
            )
        conn.commit()
        cur.close()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def auto_flag_wallets(positions, min_net_abs):
    """Add big candidates to the monitored list. Never revives a wallet the
    user IGNORED, and never overwrites a manual label."""
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor()
        flagged = 0
        for p in positions:
            if abs(p["net_usd"]) < min_net_abs:
                continue
            cur.execute(
                """INSERT INTO tracked_wallets (chain, wallet, source, last_net_usd)
                   VALUES (%s,%s,'auto',%s)
                   ON DUPLICATE KEY UPDATE last_net_usd = VALUES(last_net_usd),
                                           last_seen_at = CURRENT_TIMESTAMP""",
                (p["chain"], p["wallet"], p["net_usd"]),
            )
            flagged += 1
        conn.commit()
        cur.close()
        return flagged
    finally:
        conn.close()


def set_status(pools_scanned, swaps_ingested, candidates, seconds, error=None):
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO wallet_tracker_status
                 (id, last_cycle_at, last_cycle_seconds, pools_scanned, swaps_ingested, candidates, last_error)
               VALUES (1, %s, %s, %s, %s, %s, %s)
               ON DUPLICATE KEY UPDATE last_cycle_at=VALUES(last_cycle_at),
                 last_cycle_seconds=VALUES(last_cycle_seconds), pools_scanned=VALUES(pools_scanned),
                 swaps_ingested=VALUES(swaps_ingested), candidates=VALUES(candidates),
                 last_error=VALUES(last_error)""",
            (_utcnow(), round(seconds, 2), pools_scanned, swaps_ingested, candidates,
             (error or None) and str(error)[:500]),
        )
        conn.commit()
        cur.close()
    finally:
        conn.close()



# --------------------------------------------------------------- settings
def get_settings():
    """Return persisted UI settings, falling back to environment defaults."""
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM wallet_tracker_settings WHERE id = 1")
        row = cur.fetchone()
        cur.close()
        if not row:
            return {
                "enabled": bool(config_value("WALLET_TRACKER_ENABLED", True)),
                "networks": config_value("WALLET_TRACKER_NETWORKS", ["eth", "solana", "base", "bsc"]),
                "pools_per_network": int(config_value("WALLET_TRACKER_POOLS_PER_NETWORK", 4)),
                "min_trade_usd": float(config_value("WALLET_TRACKER_MIN_TRADE_USD", 10000)),
                "min_net_usd": float(config_value("WALLET_TRACKER_MIN_NET_USD", 150000)),
                "auto_track_net_usd": float(config_value("WALLET_TRACKER_AUTO_TRACK_NET_USD", 500000)),
                "poll_interval_seconds": int(config_value("WALLET_TRACKER_POLL_INTERVAL_SECONDS", 300)),
                "retention_days": int(config_value("WALLET_TRACKER_RETENTION_DAYS", 8)),
                "api_delay_seconds": float(config_value("WALLET_TRACKER_API_DELAY_SECONDS", 4.0)),
                "token_mode": "all",
                "token_addresses": [],
            }
        row["enabled"] = bool(row["enabled"])
        row["networks"] = [n.strip() for n in row["networks"].split(",") if n.strip()]
        row["token_addresses"] = [v.strip() for v in (row.get("token_addresses") or "").splitlines() if v.strip()]
        for k in ("min_trade_usd", "min_net_usd", "auto_track_net_usd", "api_delay_seconds"):
            row[k] = float(row[k])
        for k in ("pools_per_network", "poll_interval_seconds", "retention_days"):
            row[k] = int(row[k])
        return row
    finally:
        conn.close()


def config_value(name, default):
    # Import lazily to avoid introducing a module-level config/store cycle.
    import config
    return getattr(config, name, default)


def save_settings(settings):
    """Persist validated settings atomically; the worker reads them each cycle."""
    networks = ",".join(settings["networks"])
    token_addresses = "\n".join(settings.get("token_addresses", []))
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO wallet_tracker_settings
               (id, enabled, networks, pools_per_network, min_trade_usd, min_net_usd,
                auto_track_net_usd, poll_interval_seconds, retention_days, api_delay_seconds,
                token_mode, token_addresses)
               VALUES (1,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON DUPLICATE KEY UPDATE enabled=VALUES(enabled), networks=VALUES(networks),
                pools_per_network=VALUES(pools_per_network), min_trade_usd=VALUES(min_trade_usd),
                min_net_usd=VALUES(min_net_usd), auto_track_net_usd=VALUES(auto_track_net_usd),
                poll_interval_seconds=VALUES(poll_interval_seconds), retention_days=VALUES(retention_days),
                api_delay_seconds=VALUES(api_delay_seconds), token_mode=VALUES(token_mode),
                token_addresses=VALUES(token_addresses)""",
            (int(settings["enabled"]), networks, settings["pools_per_network"], settings["min_trade_usd"],
             settings["min_net_usd"], settings["auto_track_net_usd"], settings["poll_interval_seconds"],
             settings["retention_days"], settings["api_delay_seconds"], settings["token_mode"], token_addresses),
        )
        conn.commit()
        cur.close()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# --------------------------------------------------------------- API reads
def get_positions(period_hours, side=None, chain=None, min_net_usd=0.0, limit=100, only_tracked=False):
    where = ["p.period_hours = %s", "ABS(p.net_usd) >= %s"]
    args = [period_hours, float(min_net_usd)]
    if side in ("BUY", "SELL"):
        where.append("p.side = %s")
        args.append(side)
    if chain:
        where.append("p.chain = %s")
        args.append(chain)
    if only_tracked:
        where.append("t.id IS NOT NULL AND t.status = 'ACTIVE'")
    args.append(int(limit))
    sql = (
        "SELECT p.*, t.id AS tracked_id, t.label AS tracked_label, "
        "(t.id IS NOT NULL AND t.status = 'ACTIVE') AS is_tracked "
        "FROM wallet_positions p "
        "LEFT JOIN tracked_wallets t ON t.chain = p.chain AND t.wallet = p.wallet "
        "WHERE " + " AND ".join(where) + " "
        "ORDER BY ABS(p.net_usd) DESC LIMIT %s"
    )
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(sql, args)
        rows = [_clean(r) for r in cur.fetchall()]
        cur.close()
        for r in rows:
            r["is_tracked"] = bool(r["is_tracked"])
        return rows
    finally:
        conn.close()


def get_all_positions_for_period(period_hours):
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM wallet_positions WHERE period_hours = %s", (period_hours,))
        rows = [_clean(r, wallet_chain_keys=False) for r in cur.fetchall()]
        cur.close()
        return rows
    finally:
        conn.close()


def get_events(limit=100, since_id=0, chain=None, wallet=None, side=None):
    """Latest swaps by ACTIVE monitored wallets, newest first."""
    where, args = ["t.status = 'ACTIVE'", "s.id > %s"], [int(since_id)]
    if chain:
        where.append("s.chain = %s"); args.append(chain)
    if wallet:
        where.append("s.wallet = %s"); args.append(wallet)
    if side and side.lower() in ("buy", "sell"):
        where.append("s.side = %s"); args.append(side.lower())
    args.append(int(limit))
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            f"""SELECT s.id, s.chain, s.wallet, s.pair, s.pool_address, s.side, s.usd_value,
                       s.token_amount, s.price_usd, s.tx_hash, s.block_time, t.label
                FROM wallet_swaps s
                JOIN tracked_wallets t ON t.chain = s.chain AND t.wallet = s.wallet
                WHERE {' AND '.join(where)}
                ORDER BY s.block_time DESC, s.id DESC LIMIT %s""", args)
        rows = [_clean(r) for r in cur.fetchall()]
        cur.close()
        return rows
    finally:
        conn.close()


def get_tracked(status="ACTIVE"):
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM tracked_wallets WHERE status = %s ORDER BY last_seen_at DESC", (status,))
        rows = [_clean(r) for r in cur.fetchall()]
        cur.close()
        return rows
    finally:
        conn.close()


def is_wallet_tracked(chain, wallet):
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT id FROM tracked_wallets WHERE chain = %s AND wallet = %s AND status = 'ACTIVE'",
            (chain, wallet),
        )
        row = cur.fetchone()
        cur.close()
        return bool(row)
    finally:
        conn.close()


def track_wallet(chain, wallet, label=None):
    """Manual add / re-activate."""
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO tracked_wallets (chain, wallet, label, source, status)
               VALUES (%s,%s,%s,'manual','ACTIVE')
               ON DUPLICATE KEY UPDATE status='ACTIVE', source='manual',
                                       label=COALESCE(VALUES(label), label)""",
            (chain, wallet, label))
        conn.commit()
        cur.close()
    finally:
        conn.close()


def untrack_wallet(tracked_id):
    """Soft-remove: IGNORED so auto-flagging does not silently re-add it."""
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE tracked_wallets SET status='IGNORED' WHERE id=%s", (int(tracked_id),))
        conn.commit()
        changed = cur.rowcount
        cur.close()
        return changed > 0
    finally:
        conn.close()


def untrack_wallet_by_address(chain, wallet):
    """Soft-remove by chain and wallet address."""
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE tracked_wallets SET status='IGNORED' WHERE chain=%s AND wallet=%s", (chain, wallet))
        conn.commit()
        changed = cur.rowcount
        cur.close()
        return changed > 0
    finally:
        conn.close()


def get_status():
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM wallet_tracker_status WHERE id = 1")
        row = cur.fetchone() or {}
        cur.execute("SELECT COUNT(*) AS n FROM tracked_wallets WHERE status='ACTIVE'")
        row["tracked_wallets"] = int(cur.fetchone()["n"])
        cur.execute("SELECT COUNT(*) AS n FROM wallet_swaps WHERE block_time >= %s",
                    (_utcnow() - timedelta(hours=24),))
        row["swaps_24h"] = int(cur.fetchone()["n"])
        cur.close()
        return _clean(row, wallet_chain_keys=False)
    finally:
        conn.close()
