"""Durable order intent and guarded state transitions for paper execution.

This module does not place exchange orders. A paper order is an auditable record
that a signal is awaiting its simulated next-open fill or has reached a terminal
state. Engine integration is deliberately separate so state transitions can be
tested and reviewed before they control position creation.
"""
from __future__ import annotations

import math
from typing import Any

from db.db import get_pool

ORDER_STATUSES = {"PENDING", "FILLED", "EXPIRED", "CANCELLED", "REJECTED"}
TERMINAL_STATUSES = {"FILLED", "EXPIRED", "CANCELLED", "REJECTED"}
ALLOWED_TRANSITIONS = {
    "PENDING": TERMINAL_STATUSES,
}


class OrderValidationError(ValueError):
    """Order intent is invalid and must not be persisted."""


class OrderTransitionConflict(RuntimeError):
    """The order is no longer pending or another worker changed it first."""


def _finite_positive(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise OrderValidationError(f"{field} must be a positive finite number") from exc
    if not math.isfinite(number) or number <= 0:
        raise OrderValidationError(f"{field} must be a positive finite number")
    return number


def validate_order(order: dict[str, Any]) -> dict[str, Any]:
    """Normalize and validate the fields needed to persist one signal intent."""
    required = (
        "config_id", "symbol", "market", "timeframe", "strategy_name",
        "signal_open_time", "entry_time", "direction", "requested_entry_price",
        "stop_loss", "take_profit", "atr_at_signal",
    )
    missing = [name for name in required if order.get(name) is None or order.get(name) == ""]
    if missing:
        raise OrderValidationError("missing required order fields: " + ", ".join(missing))
    try:
        config_id = int(order["config_id"])
        signal_time = int(order["signal_open_time"])
        entry_time = int(order["entry_time"])
    except (TypeError, ValueError) as exc:
        raise OrderValidationError("config_id and candle times must be integers") from exc
    if config_id <= 0 or signal_time <= 0 or entry_time <= signal_time:
        raise OrderValidationError("config_id and candle times are invalid")
    direction = str(order["direction"]).upper()
    if direction not in {"LONG", "SHORT"}:
        raise OrderValidationError("direction must be LONG or SHORT")
    entry = _finite_positive(order["requested_entry_price"], "requested_entry_price")
    stop = _finite_positive(order["stop_loss"], "stop_loss")
    target = _finite_positive(order["take_profit"], "take_profit")
    atr = _finite_positive(order["atr_at_signal"], "atr_at_signal")
    if direction == "LONG" and not stop < entry < target:
        raise OrderValidationError("LONG order requires stop_loss < entry < take_profit")
    if direction == "SHORT" and not target < entry < stop:
        raise OrderValidationError("SHORT order requires take_profit < entry < stop_loss")
    probability = order.get("ml_probability")
    if probability is not None:
        try:
            probability = float(probability)
        except (TypeError, ValueError) as exc:
            raise OrderValidationError("ml_probability must be between 0 and 1") from exc
        if not math.isfinite(probability) or not 0 <= probability <= 1:
            raise OrderValidationError("ml_probability must be between 0 and 1")
    normalized = dict(order)
    normalized.update({
        "config_id": config_id,
        "signal_open_time": signal_time,
        "entry_time": entry_time,
        "direction": direction,
        "requested_entry_price": entry,
        "stop_loss": stop,
        "take_profit": target,
        "atr_at_signal": atr,
        "ml_probability": probability,
    })
    for name in ("symbol", "market", "timeframe", "strategy_name"):
        normalized[name] = str(order[name]).strip()
        if not normalized[name]:
            raise OrderValidationError(f"{name} must not be empty")
    return normalized


def create_pending_order(order: dict[str, Any]) -> dict[str, Any]:
    """Insert one pending order idempotently and return the canonical stored row.

    A duplicate config/signal key returns the existing row without mutating its
    state or prices, including when that row is already terminal.
    """
    o = validate_order(order)
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            INSERT IGNORE INTO paper_orders (
                config_id, symbol, market, timeframe, strategy_name,
                signal_open_time, entry_time, direction, requested_entry_price,
                stop_loss, take_profit, atr_at_signal, ml_model_id, ml_probability
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                o["config_id"], o["symbol"], o["market"], o["timeframe"], o["strategy_name"],
                o["signal_open_time"], o["entry_time"], o["direction"], o["requested_entry_price"],
                o["stop_loss"], o["take_profit"], o["atr_at_signal"], o.get("ml_model_id"),
                o.get("ml_probability"),
            ),
        )
        order_id = int(cur.lastrowid or 0)
        conn.commit()
        if not order_id:
            cur.execute(
                "SELECT id FROM paper_orders WHERE config_id=%s AND signal_open_time=%s",
                (o["config_id"], o["signal_open_time"]),
            )
            existing = cur.fetchone()
            if not existing:
                raise RuntimeError("paper order insert was ignored but its idempotency row could not be found")
            order_id = int(existing["id"])
        cur.execute("SELECT * FROM paper_orders WHERE id=%s", (order_id,))
        row = cur.fetchone()
        if not row:
            raise RuntimeError("paper order was persisted but could not be reloaded")
        cur.close()
        return row
    finally:
        conn.close()


def transition_order(
    order_id: int,
    target_status: str,
    *,
    reason: str | None = None,
    fill_price: float | None = None,
    position_id: int | None = None,
    actor_user_id: int | None = None,
) -> bool:
    """Atomically transition PENDING to one terminal state using compare-and-set.

    Returns True when this worker performed the transition. A concurrent or
    repeated transition raises OrderTransitionConflict instead of overwriting
    the state that won the race.
    """
    target = str(target_status).upper()
    if target not in ORDER_STATUSES or target not in ALLOWED_TRANSITIONS["PENDING"]:
        raise OrderValidationError(f"invalid target order state: {target_status}")
    if target == "FILLED":
        if fill_price is None:
            raise OrderValidationError("FILLED requires fill_price")
        fill_price = _finite_positive(fill_price, "fill_price")
        if position_id is None or int(position_id) <= 0:
            raise OrderValidationError("FILLED requires a valid position_id")
    elif fill_price is not None or position_id is not None:
        raise OrderValidationError("only FILLED orders may include fill_price or position_id")
    if actor_user_id is not None and int(actor_user_id) <= 0:
        raise OrderValidationError("actor_user_id must be positive")

    conn = get_pool().get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE paper_orders
            SET status=%s, state_reason=%s, fill_price=%s, position_id=%s,
                state_changed_by_user_id=%s,
                filled_at=CASE WHEN %s='FILLED' THEN CURRENT_TIMESTAMP ELSE NULL END,
                terminal_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
            WHERE id=%s AND status='PENDING'
            """,
            (
                target, reason, fill_price, position_id, actor_user_id, target, int(order_id),
            ),
        )
        if cur.rowcount != 1:
            conn.rollback()
            raise OrderTransitionConflict(f"order {order_id} is missing or no longer PENDING")
        conn.commit()
        cur.close()
        return True
    finally:
        conn.close()


def list_orders(*, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """Return recent orders, optionally filtered by a validated state."""
    limit = max(1, min(int(limit), 200))
    if status is not None:
        status = str(status).upper()
        if status not in ORDER_STATUSES:
            raise OrderValidationError(f"invalid order state: {status}")
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        if status:
            cur.execute(
                "SELECT * FROM paper_orders WHERE status=%s ORDER BY created_at DESC, id DESC LIMIT %s",
                (status, limit),
            )
        else:
            cur.execute(
                "SELECT * FROM paper_orders ORDER BY created_at DESC, id DESC LIMIT %s",
                (limit,),
            )
        rows = cur.fetchall()
        cur.close()
        return rows
    finally:
        conn.close()


def cancel_pending_order(order_id: int, *, actor_user_id: int) -> bool:
    """Cancel only a pending order; terminal orders cannot be rewritten."""
    return transition_order(
        order_id, "CANCELLED", reason="Cancelled by user",
        actor_user_id=actor_user_id,
    )


def fill_pending_order(order_id: int, fill_price: float, position_writer) -> dict[str, Any]:
    """Atomically create/link a paper position and mark its order FILLED.

    position_writer(connection) must use the caller's transaction and return
    a position ID, or None when a risk/duplicate guard blocks the insert.
    """
    fill_price = _finite_positive(fill_price, "fill_price")
    conn = get_pool().get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM paper_orders WHERE id=%s FOR UPDATE", (int(order_id),))
        order = cur.fetchone()
        if not order or order["status"] != "PENDING":
            conn.rollback()
            return {"status": "conflict", "position_id": None}
        position_id = position_writer(conn)
        if position_id is None:
            cur.execute(
                """UPDATE paper_orders SET status='REJECTED', state_reason=%s,
                   terminal_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
                   WHERE id=%s AND status='PENDING'""",
                ("Position insert blocked by exposure or duplicate-position guard", int(order_id)),
            )
            if cur.rowcount != 1:
                conn.rollback()
                return {"status": "conflict", "position_id": None}
            conn.commit()
            return {"status": "rejected", "position_id": None}
        cur.execute(
            """UPDATE paper_orders SET status='FILLED', state_reason=NULL,
               fill_price=%s, position_id=%s, filled_at=CURRENT_TIMESTAMP,
               terminal_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
               WHERE id=%s AND status='PENDING'""",
            (fill_price, int(position_id), int(order_id)),
        )
        if cur.rowcount != 1:
            conn.rollback()
            return {"status": "conflict", "position_id": None}
        conn.commit()
        return {"status": "filled", "position_id": int(position_id)}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
