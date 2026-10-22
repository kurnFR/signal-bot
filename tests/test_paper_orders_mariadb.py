"""Real MariaDB tests for the durable paper-order ledger.

Only runs when PAPER_ORDERS_MARIADB_TESTS=1 is explicitly enabled. CI supplies
an isolated disposable database; never point this suite at a production DB.
"""
import os
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from db.migrate import split_statements
from paper.orders import (
    OrderTransitionConflict,
    cancel_pending_order,
    create_pending_order,
    fill_pending_order,
    list_orders,
)

ENABLED = os.getenv("PAPER_ORDERS_MARIADB_TESTS") == "1"
ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(ENABLED, "requires disposable MariaDB test database")
class TestPaperOrdersMariaDB(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import db.db as db_module

        # The CI job supplies MYSQL_* before Python starts. Reset the shared
        # pool singleton so these tests use the isolated database configuration.
        # Reuse the isolated pool if the Wallet Tracker integration class
        # already initialized it in this same pytest process; creating a second
        # pool with the same connector pool_name raises PoolError.
        cls.pool = db_module._pool or db_module.get_pool()
        cls.migration_sql = (ROOT / "db" / "migrate_paper_orders.sql").read_text(encoding="utf-8")
        cls._apply_migration()
        conn = cls.pool.get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS paper_order_test_positions (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                    order_id BIGINT UNSIGNED NOT NULL UNIQUE,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB
                """
            )
            conn.commit()
            cur.close()
        finally:
            conn.close()

    @classmethod
    def _apply_migration(cls):
        conn = cls.pool.get_connection()
        try:
            cur = conn.cursor()
            for statement in split_statements(cls.migration_sql):
                cur.execute(statement)
            conn.commit()
            cur.close()
        finally:
            conn.close()

    def setUp(self):
        conn = self.pool.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM paper_order_test_positions")
            cur.execute("DELETE FROM paper_orders")
            conn.commit()
            cur.close()
        finally:
            conn.close()

    def _order(self, **overrides):
        entry_time = int(time.time() * 1000) - 1000
        order = {
            "config_id": 42,
            "symbol": "BTCUSDT",
            "market": "spot",
            "timeframe": "1h",
            "strategy_name": "trend_ema_v1",
            "signal_open_time": entry_time - 3_600_000,
            "entry_time": entry_time,
            "direction": "LONG",
            "requested_entry_price": 100.0,
            "stop_loss": 95.0,
            "take_profit": 110.0,
            "atr_at_signal": 5.0,
            "ml_model_id": None,
            "ml_probability": None,
        }
        order.update(overrides)
        return order

    def _insert_test_position(self, conn, order_id):
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO paper_order_test_positions (order_id) VALUES (%s)",
            (int(order_id),),
        )
        position_id = int(cur.lastrowid)
        cur.close()
        return position_id

    def _order_status(self, order_id):
        conn = self.pool.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT status FROM paper_orders WHERE id=%s", (int(order_id),))
            row = cur.fetchone()
            cur.close()
            return row[0] if row else None
        finally:
            conn.close()

    def _position_count(self, order_id):
        conn = self.pool.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM paper_order_test_positions WHERE order_id=%s", (int(order_id),))
            count = int(cur.fetchone()[0])
            cur.close()
            return count
        finally:
            conn.close()

    def test_migration_is_repeatable_and_pending_creation_is_idempotent(self):
        self._apply_migration()
        order = self._order()
        first = create_pending_order(order)
        duplicate = create_pending_order({**order, "requested_entry_price": 101.0, "stop_loss": 96.0, "take_profit": 111.0})
        self.assertEqual(int(first["id"]), int(duplicate["id"]))
        self.assertEqual(duplicate["status"], "PENDING")
        self.assertEqual(float(duplicate["requested_entry_price"]), 100.0)

    def test_concurrent_fill_attempts_create_exactly_one_position(self):
        order = create_pending_order(self._order())
        order_id = int(order["id"])
        barrier = threading.Barrier(2)

        def attempt_fill():
            barrier.wait(timeout=10)
            return fill_pending_order(
                order_id,
                100.0,
                lambda conn: self._insert_test_position(conn, order_id),
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(attempt_fill) for _ in range(2)]
            results = [future.result(timeout=20) for future in futures]

        self.assertCountEqual([result["status"] for result in results], ["filled", "conflict"])
        self.assertEqual(self._order_status(order_id), "FILLED")
        self.assertEqual(self._position_count(order_id), 1)

    def test_position_insert_failure_rolls_back_and_order_can_be_recovered(self):
        order = create_pending_order(self._order())
        order_id = int(order["id"])

        def fail_after_insert(conn):
            self._insert_test_position(conn, order_id)
            raise RuntimeError("simulated crash before order state update")

        with self.assertRaisesRegex(RuntimeError, "simulated crash"):
            fill_pending_order(order_id, 100.0, fail_after_insert)
        self.assertEqual(self._order_status(order_id), "PENDING")
        self.assertEqual(self._position_count(order_id), 0)

        result = fill_pending_order(
            order_id, 100.0, lambda conn: self._insert_test_position(conn, order_id)
        )
        self.assertEqual(result["status"], "filled")
        self.assertEqual(self._order_status(order_id), "FILLED")
        self.assertEqual(self._position_count(order_id), 1)

    def test_concurrent_cancel_and_fill_have_one_terminal_winner(self):
        order = create_pending_order(self._order())
        order_id = int(order["id"])
        barrier = threading.Barrier(2)

        def attempt_fill():
            barrier.wait(timeout=10)
            return fill_pending_order(
                order_id, 100.0, lambda conn: self._insert_test_position(conn, order_id)
            )

        def attempt_cancel():
            barrier.wait(timeout=10)
            try:
                cancel_pending_order(order_id, actor_user_id=42)
                return "cancelled"
            except OrderTransitionConflict:
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as executor:
            fill_future = executor.submit(attempt_fill)
            cancel_future = executor.submit(attempt_cancel)
            fill_result = fill_future.result(timeout=20)
            cancel_result = cancel_future.result(timeout=20)

        statuses = {fill_result["status"], cancel_result}
        self.assertIn(statuses, ({"filled", "conflict"}, {"conflict", "cancelled"}))
        final_status = self._order_status(order_id)
        self.assertIn(final_status, {"FILLED", "CANCELLED"})
        self.assertEqual(self._position_count(order_id), 1 if final_status == "FILLED" else 0)


if __name__ == "__main__":
    unittest.main()
