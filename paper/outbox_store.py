"""Standalone Telegram outbox store, gates, advisory lock, and formatters.

Extracted from ``paper/retailbot2.py`` so ``outbox_worker``, ``report_scheduler``,
``outbox_health``, ``outbox_retention``, and ``/api/telegram`` have zero runtime
dependency on the retired RetailBot2 worker. Uses only stdlib +
``mysql-connector-python`` so the minimal worker package stays free of heavy
scientific/exchange dependencies.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import escape
import json
import logging
import os
from typing import Any, Dict, Optional, Tuple
import uuid

from mysql.connector import pooling

OUTBOX_LOCK_NAME = "telegram_outbox_worker_lock"
ALLOWED_OUTBOX_ENQUEUE_DATABASES: Tuple[str, ...] = ("signal_bot_test", "crypto_signals")


@dataclass
class OutboxConfig:
    """Lightweight DB configuration for standalone outbox callers."""

    db_host: str = field(default_factory=lambda: os.getenv("MYSQL_HOST", os.getenv("DB_HOST", "192.168.1.30")))
    db_port: int = field(default_factory=lambda: int(os.getenv("MYSQL_PORT", os.getenv("DB_PORT", "3306"))))
    db_name: str = field(default_factory=lambda: os.getenv("MYSQL_DATABASE", "crypto_signals"))
    db_user: str = field(default_factory=lambda: os.getenv("MYSQL_USER", os.getenv("DB_USER", "signal_bot")))
    db_password: str = field(default_factory=lambda: os.getenv("MYSQL_PASSWORD", os.getenv("DB_PASS", "")))
    observation_only: bool = False


def is_observation_only(config: Optional[Any] = None) -> bool:
    """Return True if read-only observation mode is explicitly configured."""
    if config is not None and getattr(config, "observation_only", False):
        return True
    return os.getenv("RETAILBOT2_OBSERVATION_ONLY", "").strip() == "1"


def get_connection_database(conn: Any) -> Optional[str]:
    """Execute SELECT DATABASE() on the active MariaDB/MySQL connection."""
    if conn is None:
        return None
    cursor = None
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT DATABASE()")
        row = cursor.fetchone()
        if row is None:
            return None
        if isinstance(row, (tuple, list)):
            db = row[0] if len(row) > 0 else None
        elif isinstance(row, dict):
            db = next(iter(row.values()), None)
        else:
            db = str(row)
        if db is None:
            return None
        db_str = str(db).strip()
        if not db_str or db_str.upper() in ("NULL", "NONE"):
            return None
        return db_str
    except Exception as exc:
        logging.getLogger("retailbot2.auth").warning(
            "Failed to determine active database identity via SELECT DATABASE(): %s", exc
        )
        return None
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass


def _resolve_configured_database(config: Optional[Any] = None) -> Optional[str]:
    """Resolve expected target database name strictly from configuration object."""
    if config is None:
        return None
    if isinstance(config, str) and config.strip():
        return config.strip()
    if isinstance(config, dict):
        raw_db = config.get("db_name") or config.get("database")
        if isinstance(raw_db, str) and raw_db.strip():
            return raw_db.strip()
        return None
    raw_db = getattr(config, "db_name", None) or getattr(config, "database", None)
    if isinstance(raw_db, str) and raw_db.strip():
        return raw_db.strip()
    if hasattr(config, "config"):
        inner = getattr(config, "config", None)
        if inner is not None and inner is not config:
            raw_inner_db = getattr(inner, "db_name", None) or getattr(inner, "database", None)
            if isinstance(raw_inner_db, str) and raw_inner_db.strip():
                return raw_inner_db.strip()
    return None


def _resolve_database_name(config: Optional[Any] = None, database: Optional[str] = None) -> Optional[str]:
    """Legacy helper preserved for backward compatibility."""
    if database is not None and isinstance(database, str) and database.strip():
        return database.strip()
    return _resolve_configured_database(config)


def is_outbox_enqueue_authorized(
    config: Optional[Any] = None,
    conn: Optional[Any] = None,
    database: Optional[str] = None,
) -> bool:
    """Validate outbox enqueue authorization against actual connection identity and configuration."""
    if is_observation_only(config):
        return False
    if os.getenv("RETAILBOT2_OUTBOX_ENQUEUE_ENABLED", "").strip() != "1":
        return False
    if config is None:
        logging.getLogger("retailbot2.auth").warning(
            "Outbox enqueue rejected: missing configuration (fails closed)"
        )
        return False
    expected_db = _resolve_configured_database(config)
    if not expected_db or expected_db not in ALLOWED_OUTBOX_ENQUEUE_DATABASES:
        logging.getLogger("retailbot2.auth").warning(
            "Outbox enqueue rejected: configured database %r is not in allowlist %r",
            expected_db,
            ALLOWED_OUTBOX_ENQUEUE_DATABASES,
        )
        return False
    if database is not None and isinstance(database, str) and database.strip():
        if database.strip() != expected_db:
            logging.getLogger("retailbot2.auth").warning(
                "Outbox enqueue rejected: caller-supplied database %r does not match configured database %r",
                database.strip(),
                expected_db,
            )
            return False
    if conn is None:
        logging.getLogger("retailbot2.auth").warning(
            "Outbox enqueue rejected: missing active database connection (fails closed)"
        )
        return False
    actual_db = get_connection_database(conn)
    if not actual_db or actual_db not in ALLOWED_OUTBOX_ENQUEUE_DATABASES:
        logging.getLogger("retailbot2.auth").warning(
            "Outbox enqueue rejected: active connection database %r is not in allowlist %r",
            actual_db,
            ALLOWED_OUTBOX_ENQUEUE_DATABASES,
        )
        return False
    if expected_db != actual_db:
        logging.getLogger("retailbot2.auth").warning(
            "Outbox enqueue rejected: configured database %r does not match active connection database %r",
            expected_db,
            actual_db,
        )
        return False
    if actual_db == "crypto_signals" or expected_db == "crypto_signals":
        if os.getenv("RETAILBOT2_PRODUCTION_ENQUEUE_ENABLED", "").strip() != "1":
            logging.getLogger("retailbot2.auth").warning(
                "Production outbox enqueue rejected: RETAILBOT2_PRODUCTION_ENQUEUE_ENABLED=1 is required for database 'crypto_signals'"
            )
            return False
    return True


def is_shadow_outbox_enqueue_authorized(
    config: Optional[Any] = None,
    conn: Optional[Any] = None,
    database: Optional[str] = None,
) -> bool:
    """Validate shadow outbox enqueue authorization against actual connection identity and configuration."""
    auth_logger = logging.getLogger("retailbot2.auth")
    if is_observation_only(config):
        return False
    if os.getenv("RETAILBOT2_SHADOW_ENQUEUE_ENABLED", "").strip() != "1":
        return False
    if config is None:
        auth_logger.warning("Shadow enqueue rejected: missing configuration (fails closed)")
        return False
    expected_db = _resolve_configured_database(config)
    if expected_db != "signal_bot_test":
        auth_logger.warning(
            "Shadow enqueue rejected: database %r is not permitted for shadow enqueue (strictly 'signal_bot_test' only)",
            expected_db,
        )
        return False
    if database is not None and isinstance(database, str) and database.strip():
        if database.strip() != "signal_bot_test":
            auth_logger.warning(
                "Shadow enqueue rejected: caller-supplied database %r does not match 'signal_bot_test'",
                database.strip(),
            )
            return False
    if conn is None:
        auth_logger.warning("Shadow enqueue rejected: missing active database connection (fails closed)")
        return False
    actual_db = get_connection_database(conn)
    if actual_db != "signal_bot_test":
        auth_logger.warning(
            "Shadow enqueue rejected: active connection database %r is not 'signal_bot_test'",
            actual_db,
        )
        return False
    if expected_db != actual_db:
        auth_logger.warning(
            "Shadow enqueue rejected: configured database %r does not match active connection database %r",
            expected_db,
            actual_db,
        )
        return False
    return True


def format_entry_message(payload: Dict, trade_id: int) -> str:
    signal = payload.get("signal") or {}
    side_emoji = "🟢 LONG" if signal.get("side") == "LONG" else "🔴 SHORT"
    mode = str(payload.get("mode", "shadow"))
    mode_tag = "🔄 INVERSE" if mode == "inverse" else "👁️ SHADOW"
    strategies = signal.get("strategies_triggered", [signal.get("strategy", "?")])
    confluence = signal.get("confluence_score", len(strategies))

    strat_lines = [f"  ├ {escape(str(s))}" for s in strategies]
    strat_text = "\n".join(strat_lines) if strat_lines else "  ├ N/A"

    reason = str(signal.get("reason", "N/A"))[:200]
    units = float(payload.get("units", 0.0))
    risk_amt = float(payload.get("risk_amt", 0.0))
    risk_pct = float(payload.get("risk_pct", 0.0))
    entry_val = float(signal.get("entry", 0.0))
    stop_val = float(signal.get("stop", 0.0))
    target_val = float(signal.get("target", 0.0))
    candle_time = escape(str(signal.get("candle_close_time", "N/A")))

    return (
        f"{side_emoji} <b>{mode_tag} #{trade_id}</b>\n"
        f"\n"
        f"<b>Symbol:</b> {escape(str(signal.get('symbol', 'N/A')))}\n"
        f"<b>Time:</b> {candle_time}\n"
        f"\n"
        f"<b>📊 PLAN:</b>\n"
        f"  ├ Entry:  <code>${entry_val:.6f}</code>\n"
        f"  ├ Stop:   <code>${stop_val:.6f}</code>\n"
        f"  └ Target: <code>${target_val:.6f}</code>\n"
        f"\n"
        f"<b>💰 RISK:</b>\n"
        f"  ├ Units: {units:.6f}\n"
        f"  ├ Risk:  ${risk_amt:.2f} ({risk_pct:.1f}%)\n"
        f"  └ R:R:   1:{signal.get('rr', 1.0):.1f}\n"
        f"\n"
        f"<b>🎯 Strategies ({confluence}/7):</b>\n"
        f"{strat_text}\n"
        f"\n"
        f"<b>Reason:</b> <i>{escape(reason)}</i>"
    )


def format_exit_message(payload: Dict) -> str:
    trade = payload.get("trade") or {}
    exit_price = float(payload.get("exit_price", 0.0))
    reason = str(payload.get("reason", "UNKNOWN"))
    pnl = float(payload.get("pnl", 0.0))
    pnl_pct = float(payload.get("pnl_pct", 0.0))
    emoji_map = {
        "TAKE_PROFIT": "🎯✅",
        "STOP_LOSS": "🛑❌",
    }
    emoji = emoji_map.get(reason, "⏹️")
    color = "🟢" if pnl >= 0 else "🔴"
    mode = str(trade.get("mode", "shadow")).upper()

    return (
        f"{emoji} <b>EXIT [{mode}] #{trade.get('id', '?')}</b>\n"
        f"\n"
        f"<b>Symbol:</b> {escape(str(trade.get('symbol', '?')))} "
        f"<b>Side:</b> {escape(str(trade.get('side', '?')))}\n"
        f"<b>Strategy:</b> {escape(str(trade.get('strategy', '?')))}\n"
        f"<b>Reason:</b> {escape(str(reason).replace('_', ' '))}\n"
        f"\n"
        f"{color} <b>P&L:</b> ${pnl:+.2f} ({pnl_pct:+.2f}%)\n"
        f"{color} <b>Exit:</b> <code>${exit_price:.6f}</code>\n"
        f"\n"
        f"⏰ {datetime.now(timezone.utc).replace(tzinfo=None).strftime('%Y-%m-%d %H:%M UTC')}"
    )


def build_entry_alert_payload(
    signal: Dict[str, Any],
    units: float,
    risk_amt: float,
    risk_pct: float,
    mode: str = "shadow",
) -> Dict[str, Any]:
    """Pure, deterministic payload builder for outbox entry notifications."""
    if not isinstance(signal, dict):
        raise ValueError("signal must be a dictionary")

    required_signal_fields = ("symbol", "side", "entry", "stop", "target")
    missing = [f for f in required_signal_fields if f not in signal or signal[f] is None]
    if missing:
        raise ValueError(f"Missing required signal fields: {', '.join(missing)}")

    side = str(signal["side"]).strip().upper()
    if side not in ("LONG", "SHORT"):
        raise ValueError(f"Invalid signal side: {side!r}; must be 'LONG' or 'SHORT'")

    try:
        entry_val = float(signal["entry"])
        stop_val = float(signal["stop"])
        target_val = float(signal["target"])
        units_val = float(units)
        risk_amt_val = float(risk_amt)
        risk_pct_val = float(risk_pct)
        rr_val = float(signal.get("rr", 1.0))
        confluence_val = int(signal.get("confluence_score", 1))
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Numeric validation failed for entry payload: {exc}") from exc

    if entry_val <= 0 or stop_val <= 0 or target_val <= 0:
        raise ValueError(f"Prices must be positive: entry={entry_val}, stop={stop_val}, target={target_val}")
    if units_val <= 0:
        raise ValueError(f"Units must be positive: {units_val}")
    if risk_amt_val < 0 or risk_pct_val < 0:
        raise ValueError(f"Risk amounts must be non-negative: amt={risk_amt_val}, pct={risk_pct_val}")

    mode_str = str(mode).strip().lower()
    if mode_str not in ("shadow", "inverse"):
        mode_str = "shadow"

    candle_time = signal.get("candle_close_time")
    candle_time_str = str(candle_time) if candle_time is not None else ""

    strategies = signal.get("strategies_triggered")
    if not strategies:
        strat = signal.get("strategy", "combined")
        strategies = [strat] if strat else ["combined"]
    strategies_clean = [str(s) for s in strategies]

    reason_clean = str(signal.get("reason", ""))[:500]

    return {
        "signal": {
            "symbol": str(signal["symbol"]).strip().upper(),
            "side": side,
            "candle_close_time": candle_time_str,
            "entry": entry_val,
            "stop": stop_val,
            "target": target_val,
            "rr": rr_val,
            "strategies_triggered": strategies_clean,
            "strategy": str(signal.get("strategy", strategies_clean[0])),
            "confluence_score": confluence_val,
            "reason": reason_clean,
        },
        "units": units_val,
        "risk_amt": risk_amt_val,
        "risk_pct": risk_pct_val,
        "mode": mode_str,
    }


def build_exit_alert_payload(
    trade: Dict[str, Any],
    exit_price: float,
    reason: str,
    pnl: float,
    pnl_pct: float,
) -> Dict[str, Any]:
    """Pure, deterministic payload builder for outbox exit notifications."""
    if not isinstance(trade, dict):
        raise ValueError("trade must be a dictionary")

    required_trade_fields = ("symbol", "side")
    missing = [f for f in required_trade_fields if f not in trade or trade[f] is None]
    if missing:
        raise ValueError(f"Missing required trade fields: {', '.join(missing)}")

    side = str(trade["side"]).strip().upper()
    if side not in ("LONG", "SHORT"):
        raise ValueError(f"Invalid trade side: {side!r}; must be 'LONG' or 'SHORT'")

    try:
        exit_val = float(exit_price)
        pnl_val = float(pnl)
        pnl_pct_val = float(pnl_pct)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Numeric validation failed for exit payload: {exc}") from exc

    if exit_val <= 0:
        raise ValueError(f"Exit price must be positive: {exit_val}")

    reason_str = str(reason).strip()
    if not reason_str:
        raise ValueError("Exit reason must be a non-empty string")

    trade_id = trade.get("id", 0)
    try:
        trade_id_int = int(trade_id)
    except (ValueError, TypeError):
        trade_id_int = 0

    mode_str = str(trade.get("mode", "shadow")).strip().lower()

    return {
        "trade": {
            "id": trade_id_int,
            "symbol": str(trade["symbol"]).strip().upper(),
            "side": side,
            "strategy": str(trade.get("strategy", "combined")),
            "mode": mode_str,
            "entry": float(trade.get("entry", 0.0)),
            "stop": float(trade.get("stop", 0.0)),
            "target": float(trade.get("target", 0.0)),
            "units": float(trade.get("units", 0.0)),
        },
        "exit_price": exit_val,
        "reason": reason_str,
        "pnl": pnl_val,
        "pnl_pct": pnl_pct_val,
    }


class OutboxStore:
    """MariaDB-backed outbox store with advisory lock ownership and claim-token fencing."""

    def __init__(self, config: Any, logger: logging.Logger):
        self.config = config
        self.logger = logger
        self.pool: Optional[pooling.MySQLConnectionPool] = None
        self._init_pool()

    def _init_pool(self):
        try:
            self.pool = pooling.MySQLConnectionPool(
                pool_name="outbox_store_pool",
                pool_size=3,
                pool_reset_session=True,
                host=self.config.db_host,
                port=self.config.db_port,
                database=self.config.db_name,
                user=self.config.db_user,
                password=self.config.db_password,
            )
            self._verify_schema()
            self.logger.info("✅ Outbox store database pool ready")
        except Exception as e:
            self.logger.error(f"❌ Outbox DB init error: {e}")
            self.pool = None

    def _verify_schema(self):
        """Verify migration-managed outbox schema without issuing DDL/DML."""
        conn = self.get_conn()
        if not conn:
            raise RuntimeError("Outbox database connection is unavailable")
        try:
            cur = conn.cursor(dictionary=True)
            cur.execute(
                """SELECT TABLE_NAME AS table_name
                   FROM information_schema.tables
                   WHERE TABLE_SCHEMA = DATABASE()
                     AND TABLE_NAME = %s""",
                ("telegram_outbox",),
            )
            if not cur.fetchone():
                raise RuntimeError(
                    "Outbox schema is missing table 'telegram_outbox'; "
                    "run 'python -m db.migrate' before starting the worker."
                )
            cur.execute(
                "SELECT COLUMN_NAME FROM information_schema.columns "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
                ("telegram_outbox", "claim_token"),
            )
            if not cur.fetchone():
                raise RuntimeError(
                    "Outbox schema is missing telegram_outbox.claim_token; "
                    "run python -m db.migrate before starting the worker."
                )
            cur.close()
        finally:
            conn.close()

    def get_conn(self):
        if not self.pool:
            return None
        try:
            conn = self.pool.get_connection()
            conn.ping(reconnect=True)
            return conn
        except Exception as e:
            self.logger.error(f"❌ DB conn error: {e}")
            return None

    def acquire_outbox_lock(self, conn, timeout_seconds: int = 0) -> bool:
        """Attempt to acquire MariaDB advisory lock for outbox delivery."""
        if not conn:
            return False
        cursor = None
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT GET_LOCK(%s, %s)", (OUTBOX_LOCK_NAME, int(timeout_seconds)))
            row = cursor.fetchone()
            return bool(row and row[0] == 1)
        except Exception as exc:
            self.logger.error("Failed to acquire advisory lock %s: %s", OUTBOX_LOCK_NAME, exc)
            return False
        finally:
            if cursor is not None:
                cursor.close()

    def is_outbox_lock_owned(self, conn) -> bool:
        """Check if this connection owns the MariaDB advisory lock."""
        if not conn:
            return False
        cursor = None
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT IS_USED_LOCK(%s) = CONNECTION_ID()", (OUTBOX_LOCK_NAME,))
            row = cursor.fetchone()
            return bool(row and row[0] == 1)
        except Exception as exc:
            self.logger.warning("Lock ownership check failed: %s", exc)
            return False
        finally:
            if cursor is not None:
                cursor.close()

    def release_outbox_lock(self, conn) -> bool:
        """Release the advisory lock on this connection."""
        if not conn:
            return False
        cursor = None
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT RELEASE_LOCK(%s)", (OUTBOX_LOCK_NAME,))
            row = cursor.fetchone()
            return bool(row and row[0] == 1)
        except Exception as exc:
            self.logger.error("Failed to release advisory lock %s: %s", OUTBOX_LOCK_NAME, exc)
            return False
        finally:
            if cursor is not None:
                cursor.close()

    def claim_telegram_outbox_event(self, lease_seconds: int = 90, conn=None) -> Optional[Dict]:
        """Atomically claim one due event; expired leases are recoverable after restart."""
        own_conn = conn is None
        active_conn = self.get_conn() if own_conn else conn
        if not active_conn:
            return None
        cursor = None
        try:
            cursor = active_conn.cursor(dictionary=True)
            cursor.execute("START TRANSACTION")
            cursor.execute(
                """UPDATE telegram_outbox
                   SET status='dead', locked_until=NULL, claim_token=NULL,
                       last_error=COALESCE(last_error, 'DELIVERY_UNKNOWN: delivery lease expired at max attempts')
                   WHERE status='processing' AND locked_until <= UTC_TIMESTAMP()
                     AND attempts >= 8"""
            )
            cursor.execute(
                """SELECT id, trade_id, event_type, payload_json, attempts
                   FROM telegram_outbox
                   WHERE attempts < 8 AND (
                       (status='pending' AND next_attempt_at <= UTC_TIMESTAMP())
                       OR (status='processing' AND locked_until <= UTC_TIMESTAMP())
                   )
                   ORDER BY next_attempt_at, id LIMIT 1 FOR UPDATE"""
            )
            event = cursor.fetchone()
            if not event:
                active_conn.commit()
                return None
            try:
                payload = json.loads(event.get("payload_json") or "{}")
            except (TypeError, ValueError) as exc:
                cursor.execute(
                    """UPDATE telegram_outbox
                       SET status='dead', locked_until=NULL, claim_token=NULL, last_error=%s
                       WHERE id=%s""",
                    (f"invalid payload JSON: {exc}"[:1000], event["id"]),
                )
                active_conn.commit()
                return None
            claim_token = uuid.uuid4().hex
            cursor.execute(
                """UPDATE telegram_outbox
                   SET status='processing', attempts=attempts+1,
                       locked_until=DATE_ADD(UTC_TIMESTAMP(), INTERVAL %s SECOND),
                       claim_token=%s, last_error=NULL WHERE id=%s""",
                (max(1, int(lease_seconds)), claim_token, event["id"]),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("outbox claim lost before update")
            active_conn.commit()
            event["attempts"] = int(event.get("attempts") or 0) + 1
            event["claim_token"] = claim_token
            event.pop("payload_json", None)
            event["payload"] = payload
            return event
        except Exception as exc:
            try:
                active_conn.rollback()
            except Exception:
                pass
            self.logger.error("Telegram outbox claim failed: %s", exc)
            return None
        finally:
            if cursor is not None:
                cursor.close()
            if own_conn and active_conn is not None:
                active_conn.close()

    def mark_telegram_outbox_sent(self, event_id: int, claim_token: str, conn=None) -> bool:
        """Acknowledge only the still-current, unexpired claim owner."""
        return self._update_telegram_outbox(
            """UPDATE telegram_outbox
               SET status='sent', sent_at=UTC_TIMESTAMP(), locked_until=NULL,
                   claim_token=NULL, last_error=NULL
               WHERE id=%s AND status='processing' AND claim_token=%s
                 AND locked_until > UTC_TIMESTAMP()""",
            (event_id, claim_token),
            conn=conn,
        )

    def retry_telegram_outbox_event(
        self,
        event_id: int,
        claim_token: str,
        attempts: int,
        error: str,
        max_attempts: int = 8,
        conn=None,
        delay: Optional[int] = None,
    ) -> bool:
        """Release/retry only if this worker still owns an unexpired claim."""
        attempts = max(1, int(attempts))
        dead = attempts >= max_attempts
        if delay is None:
            delay = min(3600, 15 * (2 ** min(attempts - 1, 8)))
        else:
            delay = max(1, int(delay))
        return self._update_telegram_outbox(
            """UPDATE telegram_outbox
               SET status=%s, next_attempt_at=DATE_ADD(UTC_TIMESTAMP(), INTERVAL %s SECOND),
                   locked_until=NULL, claim_token=NULL, last_error=%s
               WHERE id=%s AND status='processing' AND claim_token=%s
                 AND locked_until > UTC_TIMESTAMP()""",
            ("dead" if dead else "pending", delay, str(error)[:1000], event_id, claim_token),
            conn=conn,
        )

    def dead_letter_telegram_outbox_event(
        self,
        event_id: int,
        claim_token: str,
        error: str,
        conn=None,
    ) -> bool:
        """Permanently transition to dead state if this worker owns unexpired claim."""
        return self._update_telegram_outbox(
            """UPDATE telegram_outbox
               SET status='dead', locked_until=NULL,
                   claim_token=NULL, last_error=%s
               WHERE id=%s AND status='processing' AND claim_token=%s
                 AND locked_until > UTC_TIMESTAMP()""",
            (str(error)[:1000], event_id, claim_token),
            conn=conn,
        )

    def _update_telegram_outbox(self, sql: str, params: tuple, conn=None) -> bool:
        own_conn = conn is None
        active_conn = self.get_conn() if own_conn else conn
        if not active_conn:
            return False
        cursor = None
        try:
            cursor = active_conn.cursor()
            cursor.execute(sql, params)
            if cursor.rowcount != 1:
                active_conn.rollback()
                return False
            active_conn.commit()
            return True
        except Exception as exc:
            try:
                active_conn.rollback()
            except Exception:
                pass
            self.logger.error("Telegram outbox update failed: %s", exc)
            return False
        finally:
            if cursor is not None:
                cursor.close()
            if own_conn and active_conn is not None:
                active_conn.close()

    def enqueue_telegram_outbox_event(
        self,
        event_key: str,
        event_type: str,
        payload: Dict[str, Any],
        trade_id: int = 0,
        conn=None,
        *,
        deduplicate: bool = False,
    ) -> bool:
        """Enqueue an event into telegram_outbox for asynchronous delivery."""
        enqueue_cfg = getattr(self, "config", None)
        if enqueue_cfg is None or is_observation_only(enqueue_cfg):
            return False
        own_conn = conn is None
        active_conn = self.get_conn() if own_conn else conn
        if not active_conn:
            return False
        if not is_outbox_enqueue_authorized(enqueue_cfg, conn=active_conn):
            if own_conn:
                active_conn.close()
            return False
        cursor = None
        try:
            cursor = active_conn.cursor()
            statement = """INSERT INTO telegram_outbox
                   (trade_id, event_key, event_type, payload_json, status, next_attempt_at)
                   VALUES (%s, %s, %s, %s, 'pending', UTC_TIMESTAMP())"""
            if deduplicate:
                statement += " ON DUPLICATE KEY UPDATE id = id"
            cursor.execute(
                statement,
                (int(trade_id), str(event_key)[:128], str(event_type)[:16], json.dumps(payload)),
            )
            if own_conn:
                active_conn.commit()
            return True
        except Exception as exc:
            if own_conn and active_conn:
                try:
                    active_conn.rollback()
                except Exception:
                    pass
            self.logger.warning("Failed to enqueue outbox event %s: %s", event_key, exc)
            return False
        finally:
            if cursor is not None:
                cursor.close()
            if own_conn and active_conn is not None:
                active_conn.close()

    def enqueue_trade_signal_event(
        self,
        event_key: str,
        event_type: str,
        payload: Dict[str, Any],
        conn=None,
        trade_id: int = 0,
    ) -> bool:
        """Enqueue a notification-only trade signal event into telegram_outbox."""
        if not event_key or not isinstance(event_key, str):
            self.logger.warning("enqueue_trade_signal_event rejected: invalid or empty event_key")
            return False
        if event_type not in ("entry", "exit", "shadow_entry", "shadow_exit"):
            self.logger.warning("enqueue_trade_signal_event rejected: unsupported event_type %r", event_type)
            return False
        if not isinstance(payload, dict):
            self.logger.warning("enqueue_trade_signal_event rejected: payload must be a dict")
            return False

        enqueue_cfg = getattr(self, "config", None)
        if enqueue_cfg is None or is_observation_only(enqueue_cfg):
            return False

        own_conn = conn is None
        active_conn = self.get_conn() if own_conn else conn
        if not active_conn:
            return False

        if not is_shadow_outbox_enqueue_authorized(enqueue_cfg, conn=active_conn):
            if own_conn:
                active_conn.close()
            return False

        cursor = None
        try:
            cursor = active_conn.cursor()
            normalized_event_type = "entry" if "entry" in event_type else "exit"
            cursor.execute(
                """INSERT INTO telegram_outbox
                   (trade_id, event_key, event_type, payload_json, status, attempts, next_attempt_at)
                   VALUES (%s, %s, %s, %s, 'pending', 0, UTC_TIMESTAMP())
                   ON DUPLICATE KEY UPDATE event_key=VALUES(event_key)""",
                (
                    int(trade_id),
                    str(event_key)[:128],
                    normalized_event_type,
                    json.dumps(payload, default=str),
                ),
            )
            if own_conn:
                active_conn.commit()
            return True
        except Exception as exc:
            if own_conn and active_conn:
                try:
                    active_conn.rollback()
                except Exception:
                    pass
            self.logger.warning("Failed to enqueue trade signal event %s: %s", event_key, exc)
            return False
        finally:
            if cursor is not None:
                cursor.close()
            if own_conn and active_conn is not None:
                active_conn.close()


def get_outbox_counts() -> Dict[str, int]:
    """Return status -> count mapping from telegram_outbox via shared DB pool."""
    from db.db import get_pool, _owned_cursor

    conn = get_pool().get_connection()
    with _owned_cursor(conn) as cur:
        cur.execute("SELECT status, COUNT(*) FROM telegram_outbox GROUP BY status")
        return {status: int(count) for status, count in cur.fetchall()}
