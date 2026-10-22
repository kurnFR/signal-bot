import unittest
from unittest.mock import patch

from paper.orders import (
    OrderTransitionConflict,
    OrderValidationError,
    cancel_pending_order,
    create_pending_order,
    transition_order,
    validate_order,
)


def _valid_order(**overrides):
    order = {
        "config_id": 3,
        "symbol": "BTCUSDT",
        "market": "spot",
        "timeframe": "1h",
        "strategy_name": "trend_ema_v1",
        "signal_open_time": 1_760_000_000_000,
        "entry_time": 1_760_000_003_600_000,
        "direction": "LONG",
        "requested_entry_price": 100.0,
        "stop_loss": 95.0,
        "take_profit": 110.0,
        "atr_at_signal": 5.0,
        "ml_probability": 0.8,
    }
    order.update(overrides)
    return order


class _Cursor:
    def __init__(self, rowcount=1, lastrowid=17, row=None):
        self.rowcount = rowcount
        self.lastrowid = lastrowid
        self.row = row or {"id": 17, "status": "PENDING"}
        self.executed = []
        self.closed = False

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self.row

    def fetchall(self):
        return [self.row]

    def close(self):
        self.closed = True


class _Conn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self, dictionary=False):
        return self._cursor

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


class TestValidateOrder(unittest.TestCase):
    def test_valid_order_is_normalized(self):
        order = validate_order(_valid_order(direction="long"))
        self.assertEqual(order["direction"], "LONG")
        self.assertEqual(order["config_id"], 3)

    def test_rejects_invalid_direction_and_wrong_stop_geometry(self):
        with self.assertRaises(OrderValidationError):
            validate_order(_valid_order(direction="SIDEWAYS"))
        with self.assertRaises(OrderValidationError):
            validate_order(_valid_order(stop_loss=105.0))

    def test_rejects_nonfinite_prices_and_probability(self):
        with self.assertRaises(OrderValidationError):
            validate_order(_valid_order(requested_entry_price=float("nan")))
        with self.assertRaises(OrderValidationError):
            validate_order(_valid_order(ml_probability=1.1))


class TestPendingOrderPersistence(unittest.TestCase):
    def test_create_pending_order_persists_and_returns_canonical_row(self):
        cur = _Cursor(lastrowid=17, row={"id": 17, "status": "PENDING"})
        conn = _Conn(cur)
        with patch("paper.orders.get_pool", return_value=type("Pool", (), {"get_connection": lambda self: conn})()):
            row = create_pending_order(_valid_order())
        self.assertEqual(row["id"], 17)
        self.assertEqual(row["status"], "PENDING")
        self.assertEqual(conn.commits, 1)
        self.assertTrue(any("INSERT IGNORE INTO paper_orders" in sql for sql, _ in cur.executed))

    def test_duplicate_signal_returns_existing_order_without_overwriting(self):
        cur = _Cursor(lastrowid=0, row={"id": 17, "status": "FILLED"})
        conn = _Conn(cur)
        with patch("paper.orders.get_pool", return_value=type("Pool", (), {"get_connection": lambda self: conn})()):
            row = create_pending_order(_valid_order(requested_entry_price=101.0))
        self.assertEqual(row["id"], 17)
        self.assertEqual(row["status"], "FILLED")
        self.assertTrue(any("SELECT id FROM paper_orders" in sql for sql, _ in cur.executed))
        self.assertTrue(any("SELECT * FROM paper_orders" in sql for sql, _ in cur.executed))


class TestAtomicOrderFill(unittest.TestCase):
    def test_position_and_order_fill_share_one_transaction(self):
        cur = _Cursor(rowcount=1, row={"id": 17, "status": "PENDING", "entry_time": 1, "timeframe": "1h"})
        conn = _Conn(cur)
        with patch("paper.orders.time.time", return_value=1_760_000_000), \
             patch("paper.orders.get_pool", return_value=type("Pool", (), {"get_connection": lambda self: conn})()):
            result = fill_pending_order(17, 100.0, lambda transaction: 23)
        self.assertEqual(result, {"status": "filled", "position_id": 23})
        self.assertEqual(conn.commits, 1)
        self.assertEqual(conn.rollbacks, 0)
        self.assertTrue(any("FOR UPDATE" in sql for sql, _ in cur.executed))
        self.assertTrue(any("SET status='FILLED'" in sql for sql, _ in cur.executed))

    def test_fill_rechecks_entry_window_under_lock(self):
        cur = _Cursor(rowcount=1, row={"id": 17, "status": "PENDING", "entry_time": 1_700_000_000_000, "timeframe": "1h"})
        conn = _Conn(cur)
        writer_called = []
        with patch("paper.orders.time.time", return_value=1_800_000_000), \
             patch("paper.orders.get_pool", return_value=type("Pool", (), {"get_connection": lambda self: conn})()):
            result = fill_pending_order(17, 100.0, lambda transaction: writer_called.append(True) or 23)
        self.assertEqual(result["status"], "expired")
        self.assertEqual(writer_called, [])
        self.assertEqual(conn.commits, 1)

    def test_terminal_or_concurrently_changed_order_is_not_filled(self):
        cur = _Cursor(rowcount=1, row={"id": 17, "status": "CANCELLED", "entry_time": 1_760_000_000_000, "timeframe": "1h"})
        conn = _Conn(cur)
        writer_called = []
        with patch("paper.orders.get_pool", return_value=type("Pool", (), {"get_connection": lambda self: conn})()):
            result = fill_pending_order(17, 100.0, lambda transaction: writer_called.append(True) or 23)
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(writer_called, [])
        self.assertEqual(conn.rollbacks, 1)


class TestOrderTransitions(unittest.TestCase):
    def test_fill_requires_price_and_position_link(self):
        with self.assertRaises(OrderValidationError):
            transition_order(17, "FILLED")
        with self.assertRaises(OrderValidationError):
            transition_order(17, "FILLED", fill_price=100.0)

    def test_transition_is_compare_and_set_and_commits(self):
        cur = _Cursor(rowcount=1)
        conn = _Conn(cur)
        with patch("paper.orders.get_pool", return_value=type("Pool", (), {"get_connection": lambda self: conn})()):
            self.assertTrue(transition_order(17, "FILLED", fill_price=100.0, position_id=23))
        self.assertEqual(conn.commits, 1)
        sql, params = cur.executed[0]
        self.assertIn("WHERE id=%s AND status='PENDING'", sql)
        self.assertEqual(params[0], "FILLED")
        self.assertEqual(params[-1], 17)

    def test_concurrent_or_terminal_transition_does_not_overwrite(self):
        cur = _Cursor(rowcount=0)
        conn = _Conn(cur)
        with patch("paper.orders.get_pool", return_value=type("Pool", (), {"get_connection": lambda self: conn})()):
            with self.assertRaises(OrderTransitionConflict):
                cancel_pending_order(17, actor_user_id=9)
        self.assertEqual(conn.rollbacks, 1)
        self.assertEqual(conn.commits, 0)

    def test_terminal_states_are_only_reachable_from_pending(self):
        for target in ("EXPIRED", "CANCELLED", "REJECTED"):
            cur = _Cursor(rowcount=1)
            conn = _Conn(cur)
            with patch("paper.orders.get_pool", return_value=type("Pool", (), {"get_connection": lambda self: conn})()):
                self.assertTrue(transition_order(17, target, reason="test"))
            self.assertEqual(conn.commits, 1)

    def test_non_fill_terminal_states_cannot_have_position_metadata(self):
        with self.assertRaises(OrderValidationError):
            transition_order(17, "EXPIRED", fill_price=100.0)


if __name__ == "__main__":
    unittest.main()
