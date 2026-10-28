"""News AI heartbeat serialization must preserve missing timestamps as JSON null."""
import unittest
from datetime import datetime

from db.db import _serialize_news_heartbeat_rows


class TestNewsHeartbeatSerialization(unittest.TestCase):
    def test_missing_timestamp_stays_none(self):
        rows = [{"collector_name": "news_poller", "last_beat_time": None}]

        self.assertEqual(
            _serialize_news_heartbeat_rows(rows),
            [{"collector_name": "news_poller", "last_beat_time": None}],
        )

    def test_datetime_timestamp_is_serialized(self):
        timestamp = datetime(2026, 10, 28, 3, 30, 0)
        rows = [{"collector_name": "news_poller", "last_beat_time": timestamp}]

        self.assertEqual(
            _serialize_news_heartbeat_rows(rows)[0]["last_beat_time"],
            "2026-10-28 03:30:00",
        )

    def test_existing_string_timestamp_is_preserved(self):
        rows = [{"last_beat_time": "2026-10-28 03:30:00"}]

        self.assertEqual(
            _serialize_news_heartbeat_rows(rows)[0]["last_beat_time"],
            "2026-10-28 03:30:00",
        )


if __name__ == "__main__":
    unittest.main()
