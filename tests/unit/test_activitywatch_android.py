import json
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from zoneinfo import ZoneInfo

from time_mirror.collectors.activitywatch_android import (
    AndroidExportError,
    read_android_export,
    read_synced_android,
    summarize_android_events,
    validate_android_export,
)
from time_mirror.core import TimeRange


ZONE = ZoneInfo("Asia/Shanghai")


class ActivityWatchAndroidTests(TestCase):
    def test_summarizes_apps_and_hides_system_entries(self) -> None:
        window = TimeRange(
            datetime(2026, 10, 21, 1, tzinfo=ZONE),
            datetime(2026, 10, 21, 2, tzinfo=ZONE),
        )
        events = [
            _event("2026-10-20T17:00:00Z", 60, "小红书", "com.xingin.xhs"),
            _event("2026-10-20T17:01:00.5Z", 59.5, "小红书", "com.xingin.xhs"),
            _event("2026-10-20T17:02:00Z", 120, "微信", "com.tencent.mm"),
            _event("2026-10-20T17:04:00Z", 60, "系统桌面", "com.miui.home"),
            _event("2026-10-20T17:05:00Z", 30, "小红书", "com.xingin.xhs"),
        ]

        usage = self._read(events, window)

        self.assertEqual(usage.total_seconds, 270)
        self.assertEqual(
            [(app.name, app.seconds, app.sessions, app.longest_session_seconds) for app in usage.apps],
            [("小红书", 150, 2, 120), ("微信", 120, 1, 120)],
        )
        self.assertEqual(usage.latest_event_at, datetime(2026, 10, 21, 1, 5, 30, tzinfo=ZONE))
        self.assertEqual(usage.latest_event_at.tzinfo, ZONE)

    def test_clips_events_and_unions_overlaps(self) -> None:
        window = TimeRange(
            datetime(2026, 10, 21, 1, tzinfo=ZONE),
            datetime(2026, 10, 21, 1, 10, tzinfo=ZONE),
        )
        events = [
            _event("2026-10-20T16:59:30Z", 60, "阅读器", "reader.app"),
            _event("2026-10-20T17:00:20Z", 60, "阅读器", "reader.app"),
            _event("2026-10-20T17:09:30Z", 60, "阅读器", "reader.app"),
        ]

        usage = self._read(events, window)

        self.assertEqual(usage.total_seconds, 110)
        self.assertEqual(usage.apps[0].seconds, 110)

    def test_different_app_breaks_a_session_even_without_a_time_gap(self) -> None:
        window = TimeRange(
            datetime(2026, 10, 21, 1, tzinfo=ZONE),
            datetime(2026, 10, 21, 2, tzinfo=ZONE),
        )
        events = [
            _event("2026-10-20T17:00:00Z", 60, "阅读器", "reader.app"),
            _event("2026-10-20T17:01:00Z", 30, "微信", "com.tencent.mm"),
            _event("2026-10-20T17:01:30Z", 30, "阅读器", "reader.app"),
        ]

        usage = self._read(events, window)

        reader = next(app for app in usage.apps if app.package == "reader.app")
        self.assertEqual(reader.sessions, 2)
        self.assertEqual(reader.longest_session_seconds, 60)

    def test_hidden_app_still_breaks_a_visible_app_session(self) -> None:
        window = TimeRange(
            datetime(2026, 10, 21, 1, tzinfo=ZONE),
            datetime(2026, 10, 21, 2, tzinfo=ZONE),
        )
        events = [
            _event("2026-10-20T17:00:00Z", 60, "阅读器", "reader.app"),
            _event("2026-10-20T17:01:00Z", 1, "系统桌面", "com.miui.home"),
            _event("2026-10-20T17:01:01Z", 60, "阅读器", "reader.app"),
        ]

        usage = self._read(events, window)

        self.assertEqual(usage.total_seconds, 120)
        self.assertEqual(usage.apps[0].sessions, 2)
        self.assertEqual(usage.apps[0].longest_session_seconds, 60)

    def test_axis_segments_use_package_identity_and_preserve_app_switches(self) -> None:
        window = TimeRange(datetime(2026, 10, 21, 1, tzinfo=ZONE), datetime(2026, 10, 21, 2, tzinfo=ZONE))
        events = [
            _event("2026-10-20T16:55:00Z", 900, "小红书", "com.xingin.xhs"),
            _event("2026-10-20T17:10:00Z", 1, "桌面", "com.miui.home"),
            _event("2026-10-20T17:10:01Z", 300, "rednote", "com.xingin.xhs"),
            _event("2026-10-20T17:20:00Z", 600, "哔哩哔哩", "tv.danmaku.bili"),
            _event("2026-10-20T17:50:00Z", 600, "微信", "com.tencent.mm"),
        ]
        usage = self._read(events, window)
        self.assertEqual([(s.name, s.interval.minutes) for s in usage.segments],
                         [("rednote", 10), ("rednote", 5), ("bilibili", 10)])
        self.assertEqual(usage.segments[0].interval.start, window.start)
        self.assertFalse(any(hasattr(s, "package") for s in usage.segments))

        ignored = summarize_android_events(events, window, ("com.xingin.xhs",))
        self.assertEqual([s.name for s in ignored.segments], ["bilibili"])

    def test_activity_segments_include_all_visible_apps_and_identify_weread(self) -> None:
        window = TimeRange(
            datetime(2026, 10, 21, 1, tzinfo=ZONE),
            datetime(2026, 10, 21, 2, tzinfo=ZONE),
        )
        events = [
            _event("2026-10-20T16:59:59.5Z", 600.75, "WeRead", "com.tencent.weread"),
            _event("2026-10-20T17:10:00.25Z", 1, "桌面", "com.miui.home"),
            _event("2026-10-20T17:10:01.25Z", 60.25, "微信阅读", "com.tencent.weread"),
            _event("2026-10-20T17:20:00Z", 30.5, "聊天", "private.chat.app"),
            _event("2026-10-20T17:30:00Z", 60, "小红书", "com.xingin.xhs"),
        ]

        usage = self._read(events, window)

        self.assertEqual([(s.name, s.reading) for s in usage.activity_segments], [
            ("微信读书", True), ("微信读书", True), ("聊天", False), ("小红书", False),
        ])
        self.assertEqual(usage.activity_segments[0].interval.start, window.start)
        self.assertEqual(
            (usage.activity_segments[0].interval.end - usage.activity_segments[0].interval.start).total_seconds(),
            600.25,
        )
        self.assertEqual(
            (usage.activity_segments[1].interval.end - usage.activity_segments[1].interval.start).total_seconds(),
            60.25,
        )
        self.assertTrue(all(not hasattr(s, "package") for s in usage.activity_segments))
        self.assertEqual([s.name for s in usage.segments], ["rednote"])

        ignored = summarize_android_events(events, window, ("com.tencent.weread", "private.chat.app"))
        self.assertEqual([s.name for s in ignored.activity_segments], ["小红书"])

    def test_study_apps_are_identified_by_package(self) -> None:
        window = TimeRange(datetime(2026, 10, 21, 1, tzinfo=ZONE), datetime(2026, 10, 21, 2, tzinfo=ZONE))
        events = [
            _event("2026-10-20T17:00:00Z", 600, "other", "com.linkedin.android"),
            _event("2026-10-20T17:10:00Z", 600, "other", "com.openai.chatgpt"),
        ]
        usage = self._read(events, window)
        self.assertEqual([item.name for item in usage.activity_segments], ["LinkedIn", "ChatGPT"])

    def test_skips_malformed_app_fields_but_rejects_bad_timestamp(self) -> None:
        window = TimeRange(
            datetime(2026, 10, 21, 1, tzinfo=ZONE),
            datetime(2026, 10, 21, 2, tzinfo=ZONE),
        )
        malformed = [
            {"timestamp": "2026-10-20T17:00:00Z", "duration": 60, "data": {}},
            _event("2026-10-20T17:01:00Z", 30, "微信", "com.tencent.mm"),
        ]

        usage = self._read(malformed, window)

        self.assertEqual(usage.total_seconds, 30)
        invalid_timestamp = [_event("not-a-time", 30, "微信", "com.tencent.mm")]
        with self.assertRaises(AndroidExportError):
            self._read(invalid_timestamp, window)

    def test_supports_private_ignore_packages_without_leaking_them(self) -> None:
        window = TimeRange(
            datetime(2026, 10, 21, 1, tzinfo=ZONE),
            datetime(2026, 10, 21, 2, tzinfo=ZONE),
        )
        events = [
            _event("2026-10-20T17:00:00Z", 60, "私人 App", "private.app"),
            _event("2026-10-20T17:01:00Z", 30, "阅读器", "reader.app"),
        ]

        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "android.json"
            path.write_text(
                json.dumps({"buckets": {"aw-watcher-android": {"type": "currentwindow", "events": events}}}),
                encoding="utf-8",
            )
            usage = read_android_export(path, window, ["private.app"])

        self.assertEqual(usage.total_seconds, 30)
        self.assertEqual([app.name for app in usage.apps], ["阅读器"])

    def test_rejects_unlock_bucket(self) -> None:
        document = {
            "buckets": {
                "aw-watcher-android-unlock": {
                    "type": "os.lockscreen.unlocks",
                    "events": [],
                }
            }
        }
        window = TimeRange(
            datetime(2026, 10, 21, 1, tzinfo=ZONE),
            datetime(2026, 10, 21, 2, tzinfo=ZONE),
        )

        with self.assertRaises(AndroidExportError):
            self._read_document(document, window)

    def test_full_export_validation_rejects_invalid_timestamp_outside_today(self) -> None:
        document = {
            "buckets": {
                "aw-watcher-android": {
                    "type": "currentwindow",
                    "events": [_event("not-a-time", 30, "微信", "com.tencent.mm")],
                }
            }
        }

        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "android.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaises(AndroidExportError):
                validate_android_export(path)

    def test_reads_latest_synced_android_bucket(self) -> None:
        window = TimeRange(
            datetime(2026, 10, 21, 1, tzinfo=ZONE),
            datetime(2026, 10, 21, 2, tzinfo=ZONE),
        )
        buckets = {
            "aw-watcher-android_old-synced-from-phone": {
                "type": "currentwindow",
                "last_updated": "2026-10-20T16:00:00Z",
            },
            "aw-watcher-android-synced-from-phone": {
                "type": "currentwindow",
                "last_updated": "2026-10-20T17:30:00Z",
            },
            "aw-watcher-android-unlock_synced-from-phone": {
                "type": "os.lockscreen.unlocks",
                "last_updated": "2026-10-20T18:00:00Z",
            },
        }
        events = [_event("2026-10-20T17:10:00Z", 60, "阅读器", "reader.app")]

        with patch(
            "time_mirror.collectors.activitywatch_android._request_json",
            side_effect=[buckets, events],
        ) as request:
            synced = read_synced_android("http://127.0.0.1:5600", window)

        self.assertIsNotNone(synced)
        assert synced is not None
        self.assertEqual(synced.usage.total_seconds, 60)
        self.assertEqual(synced.collected_at, datetime(2026, 10, 21, 1, 30, tzinfo=ZONE))
        self.assertIn("aw-watcher-android-synced-from-phone", request.call_args_list[1].args[0])

    def test_uses_event_range_end_when_synced_bucket_has_no_last_updated(self) -> None:
        window = TimeRange(
            datetime(2026, 10, 21, 1, tzinfo=ZONE),
            datetime(2026, 10, 21, 2, tzinfo=ZONE),
        )
        buckets = {
            "aw-watcher-android-synced-from-phone": {
                "type": "currentwindow",
                "created": "2026-10-20T16:00:00Z",
                "last_updated": None,
                "metadata": {"end": "2026-10-20T17:30:00Z"},
            }
        }
        events = [_event("2026-10-20T17:10:00Z", 60, "阅读器", "reader.app")]

        with patch(
            "time_mirror.collectors.activitywatch_android._request_json",
            side_effect=[buckets, events],
        ):
            synced = read_synced_android("http://127.0.0.1:5600", window)

        self.assertIsNotNone(synced)
        assert synced is not None
        self.assertEqual(synced.collected_at, datetime(2026, 10, 21, 1, 30, tzinfo=ZONE))

    def _read(self, events: list[dict[str, object]], window: TimeRange):
        document = {
            "buckets": {
                "aw-watcher-android": {
                    "type": "currentwindow",
                    "events": events,
                }
            }
        }
        return self._read_document(document, window)

    def _read_document(self, document: dict[str, object], window: TimeRange):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "android.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            return read_android_export(path, window)


def _event(timestamp: str, duration: float, app: str, package: str) -> dict[str, object]:
    return {
        "timestamp": timestamp,
        "duration": duration,
        "data": {"app": app, "package": package, "classname": "ignored"},
    }

