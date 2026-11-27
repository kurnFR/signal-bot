import config
"""MySQL persistence for wallet tracking. Uses the shared pool in db.db."""
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from db.db import get_pool
from wallet_tracker.chains import CHAINS, explorer_url, tx_url

logger = logging.getLogger("wallet_tracker.store")


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _clean(row, wallet_chain_keys=True):
    """Decimal -> float, datetime -> ISO string, add explorer link."""
    for k, v in list(row.items()):
        if isinstance(v, Decimal):
            row[k] = float(v)
        elif isinstance(v, datetime):
            # The shared DB pool uses UTC sessions; serialize naive DB DATETIME
            # values explicitly as UTC so browsers never interpret them as local.
            if v.tzinfo is None:
                row[k] = v.isoformat(timespec="seconds") + "Z"
            else:
                row[k] = v.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    if wallet_chain_keys and "chain" in row:
        if "wallet" in row and row.get("wallet"):
            row["explorer_url"] = explorer_url(row["chain"], row["wallet"])
        if "tx_hash" in row and row.get("tx_hash"):
            row["tx_url"] = tx_url(row["chain"], row["tx_hash"])
        row["chain_label"] = CHAINS.get(row["chain"], {}).get("label", row["chain"])
    return row


_REQUIRED_TABLES = (
    "tracked_wallets",
    "wallet_swaps",
    "wallet_positions",
    "wallet_tracker_status",
    "wallet_tracker_settings",
    "wallet_tracker_alert_cursor",
    "wallet_telegram_alerts",
)


def ensure_tables():
    """Verify migration-managed Wallet Tracker schema without issuing DDL/DML."""
    conn = get_pool().get_connection()
    cur = None
    try:
        cur = conn.cursor(dictionary=True)
        placeholders = ", ".join(["%s"] * len(_REQUIRED_TABLES))
        cur.execute(
            f"""SELECT TABLE_NAME AS table_name
                FROM information_schema.tables
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME IN ({placeholders})""",
            _REQUIRED_TABLES,
        )
        existing_tables = {str(row["table_name"]) for row in cur.fetchall()}
        missing_tables = sorted(set(_REQUIRED_TABLES) - existing_tables)
        if missing_tables:
            raise RuntimeError(
                "Wallet Tracker schema is missing tables "
                f"({', '.join(missing_tables)}); run 'python -m db.migrate' "
                "with a migration-capable database account before startup."
            )

        cur.execute(
            """SELECT TABLE_NAME AS table_name,
                      COLUMN_NAME AS column_name,
                      COLUMN_TYPE AS column_type
               FROM information_schema.columns
               WHERE TABLE_SCHEMA = DATABASE()
                 AND ((TABLE_NAME = %s AND COLUMN_NAME IN (%s, %s))
                   OR (TABLE_NAME = %s AND COLUMN_NAME = %s))""",
            ("tracked_wallets", "status", "telegram_after_id", "wallet_tracker_settings", "birdeye_api_key"),
        )
        columns = {
            (str(row["table_name"]), str(row["column_name"])): str(row["column_type"])
            for row in cur.fetchall()
        }
        missing_columns = []
        if ("tracked_wallets", "telegram_after_id") not in columns:
            missing_columns.append("tracked_wallets.telegram_after_id")
        status_type = columns.get(("tracked_wallets", "status"), "")
        if "inactive" not in status_type.lower():
            missing_columns.append("tracked_wallets.status with INACTIVE")
        if ("wallet_tracker_settings", "birdeye_api_key") not in columns:
            missing_columns.append("wallet_tracker_settings.birdeye_api_key")
        if missing_columns:
            raise RuntimeError(
                "Wallet Tracker schema is outdated "
                f"({', '.join(missing_columns)}); run 'python -m db.migrate' "
                "with a migration-capable database account before startup."
            )
    finally:
        try:
            if cur is not None:
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



# -------------------------------------------------------- Telegram alert queue
def queue_new_wallet_alerts(min_trade_usd, max_age_minutes=30):
    """Queue every fresh qualifying swap; cooldown is applied to digest delivery.

    Keeping all qualifying rows pending lets the sender aggregate a wallet's
    activity after its cooldown instead of advancing the cursor past lost swaps.
    """
    max_age_minutes = max(1, min(int(max_age_minutes), 1440))
    conn = get_pool().get_connection()
    try:
        conn.start_transaction()
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT last_swap_id FROM wallet_tracker_alert_cursor WHERE id = 1 FOR UPDATE"
        )
        row = cur.fetchone()
        if not row:
            cur.execute("SELECT COALESCE(MAX(id), 0) AS max_id FROM wallet_swaps")
            max_id = int(cur.fetchone()["max_id"])
            cur.execute(
                "INSERT INTO wallet_tracker_alert_cursor (id, last_swap_id) VALUES (1, %s)",
                (max_id,),
            )
            conn.commit()
            cur.close()
            return 0
        last_id = int(row["last_swap_id"])
        cur.execute("SELECT COALESCE(MAX(id), 0) AS max_id FROM wallet_swaps")
        max_id = int(cur.fetchone()["max_id"])
        if max_id <= last_id:
            conn.commit()
            cur.close()
            return 0
        cur.execute(
            """INSERT IGNORE INTO wallet_telegram_alerts (swap_id, delivery_status)
               SELECT s.id, 'PENDING'
               FROM wallet_swaps s
               JOIN tracked_wallets t
                 ON t.chain = s.chain AND t.wallet = s.wallet
               WHERE s.id > %s AND s.id <= %s
                 AND s.usd_value >= %s AND t.status = 'ACTIVE' AND s.id > t.telegram_after_id
                 AND s.block_time >= UTC_TIMESTAMP() - INTERVAL %s MINUTE
               ORDER BY s.id""",
            (last_id, max_id, float(min_trade_usd), max_age_minutes),
        )
        queued = max(0, cur.rowcount)
        cur.execute(
            "UPDATE wallet_tracker_alert_cursor SET last_swap_id = %s WHERE id = 1",
            (max_id,),
        )
        conn.commit()
        cur.close()
        return queued
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_pending_wallet_alerts(limit=50, max_attempts=5, cooldown_minutes=10):
    """Return one due activity digest per active wallet, respecting its cooldown."""
    limit = max(1, min(int(limit), 200))
    max_attempts = max(1, min(int(max_attempts), 20))
    cooldown_minutes = max(1, min(int(cooldown_minutes), 1440))
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """UPDATE wallet_telegram_alerts a
               JOIN wallet_swaps s ON s.id = a.swap_id
               LEFT JOIN tracked_wallets t
                 ON t.chain = s.chain AND t.wallet = s.wallet
               SET a.delivery_status = 'CANCELLED', a.updated_at = UTC_TIMESTAMP()
               WHERE a.delivery_status = 'PENDING'
                 AND (t.id IS NULL OR t.status <> 'ACTIVE' OR t.telegram_after_id IS NULL
                      OR s.id <= t.telegram_after_id OR s.block_time < UTC_TIMESTAMP() - INTERVAL 30 MINUTE)"""
        )
        cur.execute(
            """SELECT MIN(a.swap_id) AS swap_id,
                      MAX(a.swap_id) AS max_swap_id,
                      MAX(UTC_TIMESTAMP()) AS eligible_as_of,
                      COUNT(*) AS swap_count,
                      COUNT(DISTINCT s.pair) AS pair_count,
                      CASE WHEN COUNT(DISTINCT s.pair) = 1
                           THEN MIN(s.pair)
                           ELSE LEFT(GROUP_CONCAT(DISTINCT CONCAT(UPPER(s.side), ' ', s.pair) ORDER BY s.pair SEPARATOR ', '), 500) END AS pair,
                      CASE WHEN COUNT(DISTINCT s.pair) = 1
                           THEN SUM(s.token_amount) ELSE NULL END AS token_amount,
                      MAX(s.price_usd) AS price_usd,
                      CASE
                        WHEN SUM(CASE WHEN s.side = 'buy' THEN 1 ELSE 0 END) = COUNT(*) THEN 'buy'
                        WHEN SUM(CASE WHEN s.side = 'sell' THEN 1 ELSE 0 END) = COUNT(*) THEN 'sell'
                        ELSE 'mixed'
                      END AS side,
                      SUM(s.usd_value) AS usd_value,
                      SUM(CASE WHEN s.side = 'buy' THEN s.usd_value ELSE 0 END) AS buy_usd_value,
                      SUM(CASE WHEN s.side = 'sell' THEN s.usd_value ELSE 0 END) AS sell_usd_value,
                      SUBSTRING_INDEX(GROUP_CONCAT(s.tx_hash ORDER BY s.id DESC), ',', 1) AS tx_hash,
                      MAX(s.block_time) AS block_time,
                      s.chain, s.wallet, t.label
               FROM wallet_telegram_alerts a
               JOIN wallet_swaps s ON s.id = a.swap_id
               JOIN tracked_wallets t
                 ON t.chain = s.chain AND t.wallet = s.wallet
               WHERE a.delivery_status = 'PENDING'
                 AND t.status = 'ACTIVE' AND s.id > t.telegram_after_id AND a.attempts < %s
                 AND (a.next_attempt_at IS NULL OR a.next_attempt_at <= UTC_TIMESTAMP())
                 AND NOT EXISTS (
                     SELECT 1
                     FROM wallet_telegram_alerts recent_a
                     JOIN wallet_swaps recent_s ON recent_s.id = recent_a.swap_id
                     WHERE recent_s.chain = s.chain AND recent_s.wallet = s.wallet
                       AND recent_a.delivery_status = 'SENT'
                       AND recent_a.delivered_at >= UTC_TIMESTAMP() - INTERVAL %s MINUTE
                 )
               GROUP BY s.chain, s.wallet, t.label
               ORDER BY MIN(a.swap_id)
               LIMIT %s""",
            (max_attempts, cooldown_minutes, limit),
        )
        rows = cur.fetchall()
        digests = []
        for row in rows:
            eligible_as_of = row.pop("eligible_as_of", None)
            digest = _clean(row)
            digest["eligible_as_of"] = eligible_as_of
            digests.append(digest)
        cur.close()
        conn.commit()
        return digests
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def mark_wallet_alert_attempt(alert, success, error=None, max_attempts=5):
    """Atomically update every eligible swap included in a wallet digest."""
    if not isinstance(alert, dict):
        raise TypeError("wallet alert digest must be a mapping")
    chain = str(alert["chain"])
    wallet = str(alert["wallet"])
    max_swap_id = int(alert["max_swap_id"])
    eligible_as_of = alert["eligible_as_of"]
    max_attempts = max(1, min(int(max_attempts), 20))
    conn = get_pool().get_connection()
    try:
        conn.start_transaction()
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """SELECT a.swap_id, a.attempts
               FROM wallet_telegram_alerts a
               JOIN wallet_swaps s ON s.id = a.swap_id
               WHERE s.chain = %s AND s.wallet = %s
                 AND a.swap_id <= %s
                 AND a.delivery_status = 'PENDING'
                 AND a.attempts < %s
                 AND (a.next_attempt_at IS NULL OR a.next_attempt_at <= %s)
               ORDER BY a.swap_id
               FOR UPDATE""",
            (chain, wallet, max_swap_id, max_attempts, eligible_as_of),
        )
        rows = cur.fetchall()
        now = _utcnow()
        for row in rows:
            attempts = int(row["attempts"]) + 1
            if success:
                status, next_attempt, delivered_at, last_error = "SENT", None, now, None
            elif attempts >= max_attempts:
                status, next_attempt, delivered_at = "FAILED", None, None
                last_error = str(error or "Telegram send failed")[:500]
            else:
                status = "PENDING"
                next_attempt = now + timedelta(seconds=min(300, 15 * (2 ** (attempts - 1))))
                delivered_at = None
                last_error = str(error or "Telegram send failed")[:500]
            cur.execute(
                """UPDATE wallet_telegram_alerts
                   SET attempts = %s, delivery_status = %s, next_attempt_at = %s,
                       delivered_at = %s, last_error = %s, updated_at = UTC_TIMESTAMP()
                   WHERE swap_id = %s AND delivery_status = 'PENDING'""",
                (attempts, status, next_attempt, delivered_at, last_error, int(row["swap_id"])),
            )
        conn.commit()
        cur.close()
        return len(rows) > 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def prune_wallet_alerts(retention_days=30):
    """Delete old terminal alert records while preserving all pending retries."""
    retention_days = max(1, min(int(retention_days), 365))
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """DELETE FROM wallet_telegram_alerts
               WHERE delivery_status IN ('SENT','FAILED','CANCELLED')
                 AND created_at < UTC_TIMESTAMP() - INTERVAL %s DAY""",
            (retention_days,),
        )
        deleted = cur.rowcount
        conn.commit()
        cur.close()
        return deleted
    except Exception:
        conn.rollback()
        raise
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
    """Add qualifying auto candidates; preserve manual and user-ignored decisions.

    Expired auto wallets can be reactivated when they qualify again, but IGNORED
    wallets are never silently revived.
"""
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
                                           last_seen_at = UTC_TIMESTAMP(),
                                           status = IF(status = 'INACTIVE' AND source = 'auto',
                                                       'ACTIVE', status)""",
                (p["chain"], p["wallet"], p["net_usd"]),
            )
            flagged += 1
        conn.commit()
        cur.close()
        return flagged
    finally:
        conn.close()


def expire_inactive_auto_wallets(inactive_days=7):
    """Mark stale auto-followed wallets INACTIVE without touching manual follows.

    INACTIVE wallets may be auto-reactivated by a later qualifying signal.
    IGNORED is reserved for explicit user decisions.
    """
    inactive_days = max(1, min(int(inactive_days), 365))
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """UPDATE tracked_wallets
               SET status = 'INACTIVE', telegram_after_id=NULL
               WHERE source = 'auto' AND status = 'ACTIVE'
                 AND last_seen_at < UTC_TIMESTAMP() - INTERVAL %s DAY""",
            (inactive_days,),
        )
        changed = cur.rowcount
        conn.commit()
        cur.close()
        return changed
    except Exception:
        conn.rollback()
        raise
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
                "birdeye_api_key": str(config_value("BIRDEYE_API_KEY", "") or "").strip(),
            }
        row["enabled"] = bool(row["enabled"])
        row["networks"] = [n.strip() for n in row["networks"].split(",") if n.strip()]
        row["token_addresses"] = [v.strip() for v in (row.get("token_addresses") or "").splitlines() if v.strip()]
        row["birdeye_api_key"] = str(row.get("birdeye_api_key") or config_value("BIRDEYE_API_KEY", "") or "").strip()
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
    birdeye_api_key = str(settings.get("birdeye_api_key") or "").strip()
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO wallet_tracker_settings
               (id, enabled, networks, pools_per_network, min_trade_usd, min_net_usd,
                auto_track_net_usd, poll_interval_seconds, retention_days, api_delay_seconds,
                token_mode, token_addresses, birdeye_api_key)
               VALUES (1,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON DUPLICATE KEY UPDATE enabled=VALUES(enabled), networks=VALUES(networks),
                pools_per_network=VALUES(pools_per_network), min_trade_usd=VALUES(min_trade_usd),
                min_net_usd=VALUES(min_net_usd), auto_track_net_usd=VALUES(auto_track_net_usd),
                poll_interval_seconds=VALUES(poll_interval_seconds), retention_days=VALUES(retention_days),
                api_delay_seconds=VALUES(api_delay_seconds), token_mode=VALUES(token_mode),
                token_addresses=VALUES(token_addresses), birdeye_api_key=VALUES(birdeye_api_key)""",
            (int(settings["enabled"]), networks, settings["pools_per_network"], settings["min_trade_usd"],
             settings["min_net_usd"], settings["auto_track_net_usd"], settings["poll_interval_seconds"],
             settings["retention_days"], settings["api_delay_seconds"], settings["token_mode"], token_addresses,
             birdeye_api_key),
        )
        conn.commit()
        cur.close()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_active_pools(chain, limit=10):
    """Retrieve recently active pools from historical swaps as a discovery fallback."""
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """SELECT pool_address AS address, pair AS name,
                      COALESCE(SUM(usd_value), 0) AS volume_24h_usd
               FROM wallet_swaps
               WHERE chain = %s AND block_time >= UTC_TIMESTAMP() - INTERVAL 7 DAY
               GROUP BY pool_address, pair
               ORDER BY volume_24h_usd DESC
               LIMIT %s""",
            (chain, int(limit)),
        )
        rows = [_clean(r, wallet_chain_keys=False) for r in cur.fetchall()]
        cur.close()
        for r in rows:
            r["volume_24h_usd"] = float(r.get("volume_24h_usd") or 0.0)
        return rows
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
            if r.get("label") is None and r.get("tracked_label") is not None:
                r["label"] = r["tracked_label"]
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


def toggle_wallet_tracking(chain, wallet, label=None):
    """Atomically toggle a wallet's monitoring state and return its new state.

    A per-wallet MariaDB advisory lock serializes the complete operation,
    including the first toggle when the row does not exist yet. Locking only
    the row is insufficient for a missing key and INSERT IGNORE can deadlock
    when two transactions race to create/lock the same unique key.
    """
    import hashlib

    conn = get_pool().get_connection()
    lock_name = hashlib.sha256(f"{chain}:{wallet}".encode("utf-8")).hexdigest()
    lock_acquired = False
    cur = None
    try:
        cur = conn.cursor()
        cur.execute("SELECT GET_LOCK(%s, %s)", (lock_name, 10))
        lock_row = cur.fetchone()
        if not lock_row or lock_row[0] != 1:
            raise TimeoutError("Timed out waiting for wallet tracking lock")
        lock_acquired = True

        conn.start_transaction()
        cur.execute(
            "SELECT status FROM tracked_wallets WHERE chain = %s AND wallet = %s FOR UPDATE",
            (chain, wallet),
        )
        row = cur.fetchone()
        if not row:
            cur.execute(
                """INSERT INTO tracked_wallets (chain, wallet, label, source, status)
                   VALUES (%s,%s,%s,'manual','ACTIVE')""",
                (chain, wallet, label),
            )
            conn.commit()
            return True

        current_status = row[0]
        if current_status == "ACTIVE":
            cur.execute(
                "UPDATE tracked_wallets SET status = 'IGNORED', telegram_after_id=NULL WHERE chain = %s AND wallet = %s",
                (chain, wallet),
            )
            tracked = False
        else:
            cur.execute(
                """UPDATE tracked_wallets
                   SET status = 'ACTIVE', source = 'manual',
                       label = COALESCE(%s, label), last_seen_at = UTC_TIMESTAMP()
                   WHERE chain = %s AND wallet = %s""",
                (label, chain, wallet),
            )
            tracked = True
        conn.commit()
        return tracked
    except Exception:
        conn.rollback()
        raise
    finally:
        if lock_acquired:
            try:
                release_cur = conn.cursor()
                release_cur.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
                release_cur.fetchone()
                release_cur.close()
            except Exception:
                logger.exception("Failed to release wallet tracking advisory lock")
        if cur is not None:
            try:
                cur.close()
            except Exception:
                pass
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
        cur.execute("UPDATE tracked_wallets SET status='IGNORED', telegram_after_id=NULL WHERE id=%s", (int(tracked_id),))
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
        cur.execute("UPDATE tracked_wallets SET status='IGNORED', telegram_after_id=NULL WHERE chain=%s AND wallet=%s", (chain, wallet))
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


def wallet_performance(period_hours, metric, limit):
    from wallet_tracker.performance import rank_wallets

    conn = get_pool().get_connection()
    try:
        with conn.cursor(dictionary=True) as cur:
            cur.execute(
                """SELECT chain, wallet, pool_address, pair, block_time, side, token_amount, usd_value
                   FROM wallet_swaps
                   WHERE block_time BETWEEN UTC_TIMESTAMP() - INTERVAL %s HOUR AND UTC_TIMESTAMP()
                   ORDER BY chain, wallet, pool_address, block_time""", (period_hours,))
            return [_clean(row) for row in rank_wallets(cur, metric, limit)]
    finally:
        conn.close()


def set_wallet_label(tracked_id, label):
    conn = get_pool().get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM tracked_wallets WHERE id=%s AND status='ACTIVE' FOR UPDATE", (tracked_id,))
            if not cur.fetchone():
                return False
            cur.execute("UPDATE tracked_wallets SET label=%s WHERE id=%s", (label, tracked_id))
        conn.commit()
        return True
    finally:
        conn.close()


def set_wallet_telegram(tracked_id, enabled):
    conn = get_pool().get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM tracked_wallets WHERE id=%s AND status='ACTIVE' FOR UPDATE",
                        (tracked_id,))
            if not cur.fetchone():
                return False
            cur.execute(
                """UPDATE tracked_wallets SET telegram_after_id =
                   CASE WHEN %s THEN COALESCE(telegram_after_id, (SELECT COALESCE(MAX(id),0) FROM wallet_swaps))
                        ELSE NULL END WHERE id=%s""", (enabled, tracked_id))
        conn.commit()
        return True
    finally:
        conn.close()


def wallet_alert_selected(alert):
    """Recheck selection immediately before sending a previously fetched digest."""
    conn = get_pool().get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id FROM tracked_wallets WHERE chain=%s AND wallet=%s
                   AND status='ACTIVE' AND telegram_after_id < %s""",
                (alert["chain"], alert["wallet"], alert["swap_id"]))
            return cur.fetchone() is not None
    finally:
        conn.close()
