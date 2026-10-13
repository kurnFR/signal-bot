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
    def test_queue_query_limits_alerts_to_one_per_wallet_and_applies_cooldown(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.fetchone.side_effect = [{"last_swap_id": 10}, {"max_id": 20}]
        cursor.rowcount = 1
        pool = MagicMock()
        pool.get_connection.return_value = connection
        with patch("wallet_tracker.store.get_pool", return_value=pool):
            queued = store.queue_new_wallet_alerts(10000, max_age_minutes=30, cooldown_minutes=10)

        self.assertEqual(queued, 1)
        sql, params = cursor.execute.call_args_list[2].args
        self.assertIn("NOT EXISTS", sql)
        self.assertIn("MIN(s2.id)", sql)
        self.assertIn("delivery_status = 'PENDING'", sql)
        self.assertIn("delivery_status = 'SENT'", sql)
        self.assertEqual(params, (10, 20, 10000.0, 30, 10, 10, 20, 10000.0, 30))
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
    @patch.object(worker.store, "queue_new_wallet_alerts", return_value=1)
    @patch.object(worker.store, "get_pending_wallet_alerts", return_value=[
        {"swap_id": 10}, {"swap_id": 11}
    ])
    @patch.object(worker.store, "mark_wallet_alert_attempt")
    def test_dispatch_persists_success_and_failure(self, mark, pending, queued, settings):
        notifier = MagicMock()
        notifier.configured = True
        notifier.send.side_effect = [True, False]
        sent = worker.dispatch_telegram_alerts(notifier)
        self.assertEqual(sent, 1)
        queued.assert_called_once_with(10000, max_age_minutes=30, cooldown_minutes=10)
        self.assertEqual(pending.call_count, 1)
        self.assertEqual(mark.call_count, 2)
        self.assertEqual(mark.call_args_list[0].args, (10,))
        self.assertTrue(mark.call_args_list[0].kwargs["success"])
        self.assertFalse(mark.call_args_list[1].kwargs["success"])

    @patch.object(worker.store, "queue_new_wallet_alerts")
    def test_missing_telegram_config_does_not_advance_cursor(self, queue):
        notifier = MagicMock()
        notifier.configured = False
        self.assertEqual(worker.dispatch_telegram_alerts(notifier), 0)
        queue.assert_not_called()


if __name__ == "__main__":
    unittest.main()
