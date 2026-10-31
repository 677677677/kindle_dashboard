import json
import sqlite3
from contextlib import closing
from datetime import datetime
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from urllib.error import URLError
from zoneinfo import ZoneInfo

from time_mirror.collectors.activitywatch import (
    ActivityWatchUnavailable, ActivityWatchResponseError, read_app_intervals, summarize_apps,
)
from time_mirror.core import TimeRange


ZONE = ZoneInfo("Asia/Shanghai")


class FakeResponse(BytesIO):
    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


class ActivityWatchTests(TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.path = Path(self.temporary.name) / "sqlite.db"
        self.window = TimeRange(datetime(2026, 10, 21, 9, tzinfo=ZONE), datetime(2026, 10, 21, 12, tzinfo=ZONE))
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("CREATE TABLE buckets (id INTEGER, name TEXT, hostname TEXT)")
            db.execute("CREATE TABLE events (bucketrow INTEGER, starttime INTEGER, endtime INTEGER, data TEXT)")
            db.executemany("INSERT INTO buckets VALUES (?,?,?)", [(1,"aw-watcher-window_本机","local"), (2,"aw-watcher-afk_本机","local")])
            for name in ("Code.exe", "msedge.exe", "too-short.exe"):
                db.execute("INSERT INTO events VALUES (?,?,?,?)", (1, int(self.window.start.timestamp())*10**9,
                    int(self.window.end.timestamp())*10**9, json.dumps({"app": name, "title": "PRIVATE_TEST_TITLE"})))
            db.commit()

    def tearDown(self):
        self.temporary.cleanup()

    def response(self, extra=None):
        return [{name: [{"timestamp": self.window.start.isoformat(), "duration": duration,
                        "data": extra or {"status": "not-afk"}}]
                 for name, duration in (("Code.exe",4200.4), ("msedge.exe",900.2), ("too-short.exe",0.4))}]

    def test_returns_only_app_names_and_durations_from_safe_intersections(self):
        before = self.path.read_bytes()
        with patch("time_mirror.collectors.activitywatch.urlopen", return_value=FakeResponse(json.dumps(self.response()).encode())) as mocked:
            apps = summarize_apps(self.window, database_path=self.path)
        body = json.loads(mocked.call_args.args[0].data)
        self.assertEqual([(item.name,item.seconds) for item in apps], [("Code.exe",4200),("msedge.exe",900)])
        self.assertNotIn("title", json.dumps(body))
        self.assertIn("本机", body["query"][0])
        self.assertNotIn("\\u", body["query"][0])
        self.assertIn("filter_period_intersect(not_afk", json.dumps(body))
        self.assertEqual(before, self.path.read_bytes())

    def test_reports_stopped_local_server_as_unavailable(self):
        with patch("time_mirror.collectors.activitywatch.urlopen", side_effect=URLError("stopped")):
            with self.assertRaises(ActivityWatchUnavailable):
                read_app_intervals(self.window, database_path=self.path)

    def test_rejects_response_carrying_non_afk_fields(self):
        with patch("time_mirror.collectors.activitywatch.urlopen", return_value=FakeResponse(json.dumps(self.response({"status":"not-afk","title":"PRIVATE_TEST_TITLE"})).encode())):
            with self.assertRaises(ActivityWatchResponseError):
                read_app_intervals(self.window, database_path=self.path)

    def test_intervals_keep_subminute_precision_and_are_clipped(self):
        response = self.response()
        response[0]["Code.exe"][0]["duration"] = 86400
        with patch("time_mirror.collectors.activitywatch.urlopen", return_value=FakeResponse(json.dumps(response).encode())):
            intervals = read_app_intervals(self.window, database_path=self.path)
        self.assertEqual(next(item.interval for item in intervals if item.name=="Code.exe"), self.window)
        short = next(item.interval for item in intervals if item.name=="too-short.exe")
        self.assertAlmostEqual((short.end-short.start).total_seconds(),0.4)

