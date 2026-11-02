import unittest
from unittest.mock import MagicMock, patch

from wallet_tracker import store, worker
from wallet_tracker.telegram import WalletTelegramNotifier


class TestWalletTelegramNotifier(unittest.TestCase):
    def setUp(self):
        self.alert = {
            "swap_id": 12,
            "chain": "bsc",
            "chain_label": "BNB Chain",
            "wallet": "0x" + "a" * 40,
            "label": "ARK accumulator <candidate>",
            "pair": "ARK / USDT",
            "side": "buy",
            "usd_value": 20002.2,
            "token_amount": 3176.7014447,
            "price_usd": 6.2965325988,
            "tx_url": "https://bscscan.com/tx/" + "b" * 64,
            "explorer_url": "https://bscscan.com/address/" + "a" * 40,
            "block_time": "2026-10-13 07:53:10",
        }

    def test_format_message_summarizes_multi_swap_wallet_digest(self):
        alert = dict(
            self.alert,
            swap_count=4,
            pair="2 pairs",
            side="mixed",
            usd_value=1260000,
            buy_usd_value=900000,
            sell_usd_value=360000,
        )
        message = WalletTelegramNotifier.format_message(alert)
        self.assertIn("Followed Wallet ACTIVITY Digest", message)
        self.assertIn("<b>Swaps:</b> 4", message)
        self.assertIn("$1,260,000.00", message)
        self.assertIn("$900,000.00", message)
        self.assertIn("$360,000.00", message)
        self.assertNotIn("Token amount", message)

    def test_format_message_contains_trade_details_and_escapes_html(self):
        message = WalletTelegramNotifier.format_message(self.alert)
        self.assertIn("Followed Wallet BUY", message)
        self.assertIn("ARK / USDT", message)
        self.assertIn("$20,002.20", message)
        self.assertIn("3,176.701445", message)
        self.assertIn("&lt;candidate&gt;", message)
        self.assertIn("https://bscscan.com/tx/", message)

    def test_send_uses_existing_telegram_settings_and_api(self):
        session = MagicMock()
        response = MagicMock()
        response.json.return_value = {"ok": True, "result": {"message_id": 123}}
        session.post.return_value = response
        notifier = WalletTelegramNotifier(token="token", chat_id="chat", session=session)
        with patch("wallet_tracker.telegram.time.sleep"):
            self.assertTrue(notifier.send(self.alert))
        session.post.assert_called_once()
        args, kwargs = session.post.call_args
        self.assertIn("/bottoken/sendMessage", args[0])
        self.assertEqual(kwargs["json"]["chat_id"], "chat")
        self.assertEqual(kwargs["json"]["parse_mode"], "HTML")

    def test_send_failure_never_logs_token_bearing_request_url(self):
        import logging
        import requests

        token = "123456:secret-bot-token"
        session = MagicMock()
        session.post.side_effect = requests.ConnectionError(
            f"failed to connect to https://api.telegram.org/bot{token}/sendMessage"
        )
        notifier = WalletTelegramNotifier(token=token, chat_id="chat", session=session)
        with self.assertLogs("wallet_tracker.telegram", level="WARNING") as captured:
            with patch("wallet_tracker.telegram.time.sleep"):
                self.assertFalse(notifier.send(self.alert))
        self.assertNotIn(token, "\n".join(captured.output))
        self.assertNotIn("/bot" + token, "\n".join(captured.output))

    def test_send_retries_429_using_retry_after_then_succeeds(self):
        session = MagicMock()
        limited = MagicMock()
        limited.status_code = 429
        limited.json.return_value = {"ok": False, "parameters": {"retry_after": 4}}
        success = MagicMock()
        success.status_code = 200
        success.json.return_value = {"ok": True, "result": {"message_id": 123}}
        session.post.side_effect = [limited, success]
        notifier = WalletTelegramNotifier(token="token", chat_id="chat", session=session)
        with patch("wallet_tracker.telegram.time.sleep") as sleep:
            self.assertTrue(notifier.send(self.alert))
        self.assertEqual(session.post.call_count, 2)
        sleep.assert_any_call(4)

    def test_send_rejects_unsuccessful_telegram_payload(self):
        session = MagicMock()
        response = MagicMock()
        response.json.return_value = {"ok": False, "description": "bad chat"}
        session.post.return_value = response
        notifier = WalletTelegramNotifier(token="token", chat_id="chat", session=session)
        with patch("wallet_tracker.telegram.time.sleep"):
            self.assertFalse(notifier.send(self.alert))

    @patch.dict("os.environ", {"TELEGRAM_TOKEN": "retail-token", "TELEGRAM_CHAT_ID": "chat"}, clear=True)
    def test_reuses_retailbot_token_alias(self):
        notifier = WalletTelegramNotifier()
        self.assertEqual(notifier.token, "retail-token")
        self.assertEqual(notifier.chat_id, "chat")


class TestWalletAlertQueueControls(unittest.TestCase):
    def test_queue_retains_every_fresh_qualifying_swap_for_later_digest(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.fetchone.side_effect = [{"last_swap_id": 10}, {"max_id": 20}]
        cursor.rowcount = 4
        pool = MagicMock()
        pool.get_connection.return_value = connection
        with patch("wallet_tracker.store.get_pool", return_value=pool):
            queued = store.queue_new_wallet_alerts(10000, max_age_minutes=30)

        self.assertEqual(queued, 4)
        sql, params = cursor.execute.call_args_list[2].args
        self.assertIn("INSERT IGNORE INTO wallet_telegram_alerts", sql)
        self.assertIn("s.id > %s AND s.id <= %s", sql)
        self.assertIn("s.block_time >= UTC_TIMESTAMP()", sql)
        self.assertNotIn("NOT EXISTS", sql)
        self.assertNotIn("MIN(s2.id)", sql)
        self.assertEqual(params, (10, 20, 10000.0, 30))
        connection.commit.assert_called_once()

    def test_pending_alerts_are_grouped_by_wallet_and_deferred_during_cooldown(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.fetchall.return_value = []
        pool = MagicMock()
        pool.get_connection.return_value = connection
        with patch("wallet_tracker.store.get_pool", return_value=pool):
            pending = store.get_pending_wallet_alerts(limit=50, cooldown_minutes=10)

        self.assertEqual(pending, [])
        sql, params = cursor.execute.call_args_list[1].args
        self.assertIn("GROUP BY s.chain, s.wallet, t.label", sql)
        self.assertIn("SUM(s.usd_value)", sql)
        self.assertIn("COUNT(*) AS swap_count", sql)
        self.assertIn("MAX(a.swap_id) AS max_swap_id", sql)
        self.assertIn("delivery_status = 'SENT'", sql)
        self.assertEqual(params, (5, 10, 50))
        connection.commit.assert_called_once()

    def test_digest_delivery_updates_all_included_swaps_with_individual_retry_counts(self):
        from datetime import datetime

        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.fetchall.return_value = [
            {"swap_id": 10, "attempts": 0},
            {"swap_id": 11, "attempts": 2},
        ]
        pool = MagicMock()
        pool.get_connection.return_value = connection
        alert = {
            "chain": "eth", "wallet": "0x" + "a" * 40,
            "max_swap_id": 11, "eligible_as_of": datetime(2026, 11, 2, 7, 0, 0),
        }
        with patch("wallet_tracker.store.get_pool", return_value=pool):
            updated = store.mark_wallet_alert_attempt(
                alert, success=False, error="Telegram failed", max_attempts=3,
            )

        self.assertTrue(updated)
        self.assertEqual(cursor.execute.call_count, 3)
        first_update = cursor.execute.call_args_list[1].args
        second_update = cursor.execute.call_args_list[2].args
        self.assertEqual(first_update[1][0:2], (1, "PENDING"))
        self.assertEqual(first_update[1][-1], 10)
        self.assertEqual(second_update[1][0:2], (3, "FAILED"))
        self.assertEqual(second_update[1][-1], 11)
        connection.commit.assert_called_once()

    def test_alert_retention_deletes_only_old_terminal_rows(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.rowcount = 7
        pool = MagicMock()
        pool.get_connection.return_value = connection
        with patch("wallet_tracker.store.get_pool", return_value=pool):
            deleted = store.prune_wallet_alerts(30)

        self.assertEqual(deleted, 7)
        sql, params = cursor.execute.call_args.args
        self.assertIn("'SENT','FAILED','CANCELLED'", sql)
        self.assertNotIn("'PENDING'", sql)
        self.assertEqual(params, (30,))
        connection.commit.assert_called_once()


class TestWalletTelegramDispatch(unittest.TestCase):
    @patch.object(worker.store, "get_settings", return_value={"min_trade_usd": 10000})
    @patch.object(worker.store, "queue_new_wallet_alerts", return_value=4)
    @patch.object(worker.store, "get_pending_wallet_alerts", return_value=[
        {"swap_id": 10, "max_swap_id": 11, "wallet": "wallet-a"},
        {"swap_id": 20, "max_swap_id": 20, "wallet": "wallet-b"},
    ])
    @patch.object(worker.store, "mark_wallet_alert_attempt")
    def test_dispatch_persists_one_delivery_per_wallet_digest(self, mark, pending, queued, settings):
        notifier = MagicMock()
        notifier.configured = True
        notifier.send.side_effect = [True, False]
        sent = worker.dispatch_telegram_alerts(notifier)
        self.assertEqual(sent, 1)
        queued.assert_called_once_with(10000, max_age_minutes=30)
        pending.assert_called_once_with(limit=50, cooldown_minutes=10)
        self.assertEqual(mark.call_count, 2)
        self.assertEqual(mark.call_args_list[0].args[0]["wallet"], "wallet-a")
        self.assertTrue(mark.call_args_list[0].kwargs["success"])
        self.assertEqual(mark.call_args_list[1].args[0]["wallet"], "wallet-b")
        self.assertFalse(mark.call_args_list[1].kwargs["success"])

    @patch.object(worker.store, "queue_new_wallet_alerts")
    def test_missing_telegram_config_does_not_advance_cursor(self, queue):
        notifier = MagicMock()
        notifier.configured = False
        self.assertEqual(worker.dispatch_telegram_alerts(notifier), 0)
        queue.assert_not_called()


if __name__ == "__main__":
    unittest.main()
