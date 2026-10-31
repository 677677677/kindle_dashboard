import json
import threading
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from zoneinfo import ZoneInfo

from time_mirror.collectors.activitywatch_android import PhoneAppSummary, PhoneUsage, SyncedPhoneUsage
from time_mirror.collectors.outlook_calendar import OutlookCalendar, OutlookEvent, write_calendar_cache
from time_mirror.core import TimeRange
from time_mirror.runtime import (
    SyncResult,
    _atomic_write,
    _pull_android_sync,
    configure_outlook,
    load_config,
    outlook_sync_window,
    refresh_snapshot,
    _read_phone,
)


class RuntimeTests(TestCase):
    def test_yesterdays_empty_sync_is_pending_today_even_inside_24_hours(self):
        zone = ZoneInfo("Asia/Shanghai")
        now = datetime(2026, 10, 27, 12, tzinfo=zone)
        yesterday = datetime(2026, 10, 26, 19, 45, tzinfo=zone)
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = load_config(root / "missing.json")
            with patch("time_mirror.runtime.read_synced_android", return_value=SyncedPhoneUsage(PhoneUsage(0, (), None), yesterday.astimezone(ZoneInfo("UTC")))):
                result = _read_phone(config, root, now, zone)
                historical = _read_phone(config, root, now, zone,
                    TimeRange(now-timedelta(days=7), now.replace(hour=1)))
        self.assertEqual(result.status, "cached")
        self.assertEqual(result.error_code, "awaiting_today_sync")
        self.assertEqual(result.collected_at.isoformat(), yesterday.isoformat())
        self.assertEqual(historical.status, "ok")
        self.assertIsNone(historical.error_code)

    def test_fresh_phone_event_clears_pending_sync(self):
        zone = ZoneInfo("Asia/Shanghai")
        now = datetime(2026, 10, 27, 12, tzinfo=zone)
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = load_config(root / "missing.json")
            with patch("time_mirror.runtime.read_synced_android", return_value=SyncedPhoneUsage(PhoneUsage(60, (), now), now)):
                result = _read_phone(config, root, now, zone)
        self.assertEqual(result.status, "ok")
        self.assertIsNone(result.error_code)

    def test_private_config_overrides_only_supplied_values(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "remainingTime": {
                            "reservePlan": [
                                {
                                    "label": "睡前准备",
                                    "minutes": 60,
                                    "assumedCompletedBy": "01:00",
                                }
                            ]
                        },
                        "kindle": {"hosts": []},
                    }
                ),
                encoding="utf-8",
            )

            config = load_config(path)

        self.assertEqual(config["remainingTime"]["reservePlan"][0]["minutes"], 60)
        self.assertEqual(config["kindle"]["hosts"], [])
        self.assertEqual(config["kindle"]["port"], 2222)

    def test_refresh_writes_valid_json_and_executable_snapshot(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "kindle": {"hosts": []},
                        "activitywatch": {"enabled": False},
                        "android": {"enabled": False},
                    }
                ),
                encoding="utf-8",
            )

            snapshot = refresh_snapshot(config_path, root)

            json_path = root / "web" / "snapshot.json"
            script_path = root / "web" / "snapshot.js"
            persisted = json.loads(json_path.read_text(encoding="utf-8"))
            script = script_path.read_text(encoding="utf-8")

        self.assertEqual(snapshot, persisted)
        self.assertEqual(snapshot["sources"]["koreader"]["status"], "unavailable")
        self.assertTrue(script.startswith("window.__TIME_MIRROR_SNAPSHOT__="))
        self.assertTrue(script.endswith(";\n"))
        self.assertNotIn("privateKey", script)
        windows = snapshot["dayAxis"]["reserves"]
        self.assertTrue(all(item["startAt"] >= snapshot["generatedAt"] for item in windows))

    def test_refresh_reads_external_android_export_and_writes_only_aggregates(self) -> None:
        zone = ZoneInfo("Asia/Shanghai")
        now = datetime.now(zone)
        event_start = now - timedelta(minutes=1)
        document = {
            "buckets": {
                "aw-watcher-android": {
                    "type": "currentwindow",
                    "events": [
                        {
                            "timestamp": event_start.isoformat(),
                            "duration": 60,
                            "data": {
                                "app": "阅读器",
                                "package": "private.reader.package",
                                "classname": "private.activity.Name",
                            },
                        }
                    ],
                }
            }
        }

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            imports = root / "imports"
            imports.mkdir()
            (imports / "activitywatch-android.json").write_text(json.dumps(document), encoding="utf-8")
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "kindle": {"hosts": []},
                        "activitywatch": {"enabled": False},
                        "android": {"staleAfterMinutes": 1440},
                    }
                ),
                encoding="utf-8",
            )

            snapshot = refresh_snapshot(config_path, root)
            persisted = (root / "web" / "snapshot.json").read_text(encoding="utf-8")

        self.assertEqual(snapshot["sources"]["phone"]["status"], "ok")
        self.assertEqual(snapshot["devices"][2]["totalMinutes"], 1)
        self.assertEqual(snapshot["devices"][2]["apps"][0]["name"], "阅读器")
        self.assertNotIn("private.reader.package", persisted)
        self.assertNotIn("private.activity.Name", persisted)

    def test_koreader_failure_does_not_discard_valid_phone_data(self) -> None:
        zone = ZoneInfo("Asia/Shanghai")
        now = datetime.now(zone)
        document = {
            "buckets": {
                "aw-watcher-android": {
                    "type": "currentwindow",
                    "events": [
                        {
                            "timestamp": (now - timedelta(minutes=1)).isoformat(),
                            "duration": 60,
                            "data": {"app": "阅读器", "package": "reader.app"},
                        }
                    ],
                }
            }
        }

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            imports = root / "imports"
            imports.mkdir()
            bad_koreader = imports / "bad.sqlite3"
            bad_koreader.write_text("not sqlite", encoding="utf-8")
            (imports / "activitywatch-android.json").write_text(json.dumps(document), encoding="utf-8")
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps({"activitywatch": {"enabled": False}}),
                encoding="utf-8",
            )
            sync = SyncResult("cached", bad_koreader, now)

            with patch("time_mirror.runtime._sync_koreader", return_value=sync):
                snapshot = refresh_snapshot(config_path, root)

        self.assertEqual(snapshot["sources"]["koreader"]["status"], "error")
        self.assertEqual(snapshot["sources"]["phone"]["status"], "ok")
        self.assertEqual(snapshot["devices"][2]["apps"][0]["name"], "阅读器")

    def test_synced_android_bucket_takes_priority_over_manual_export(self) -> None:
        zone = ZoneInfo("Asia/Shanghai")
        now = datetime.now(zone)
        usage = PhoneUsage(
            total_seconds=120,
            apps=(
                PhoneAppSummary(
                    "微信",
                    "com.tencent.mm",
                    120,
                    1,
                    TimeRange(now - timedelta(minutes=2), now),
                ),
            ),
            latest_event_at=now,
        )
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_path = root / "config.json"
            config_path.write_text(json.dumps({"kindle": {"hosts": []}}), encoding="utf-8")

            with (
                patch("time_mirror.runtime.read_app_intervals", return_value=()),
                patch("time_mirror.runtime._pull_android_sync"),
                patch(
                    "time_mirror.runtime.read_synced_android",
                    return_value=SyncedPhoneUsage(usage, now),
                ),
            ):
                snapshot = refresh_snapshot(config_path, root)

        self.assertEqual(snapshot["sources"]["phone"]["status"], "ok")
        self.assertEqual(snapshot["devices"][2]["apps"][0]["name"], "微信")

    def test_android_sync_is_pull_only(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "aw-sync.exe"
            executable.touch()
            sync_directory = root / "ActivityWatchSync"
            sync_directory.mkdir()
            config = load_config(root / "missing.json")
            config["android"]["syncExecutable"] = str(executable)
            config["android"]["syncDirectory"] = str(sync_directory)

            with patch("time_mirror.runtime.subprocess.run") as run:
                _pull_android_sync(config, root)

        command = run.call_args.args[0]
        self.assertEqual(command[-3:], ["sync", "--mode", "pull"])
        self.assertNotIn("--buckets", command)

    def test_concurrent_atomic_writes_do_not_share_a_temporary_file(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "snapshot.json"
            errors: list[Exception] = []

            def write(content: str) -> None:
                try:
                    _atomic_write(path, content)
                except Exception as error:
                    errors.append(error)

            writers = [
                threading.Thread(target=write, args=(content,))
                for content in ('{"writer":1}\n', '{"writer":2}\n')
            ]

            for writer in writers:
                writer.start()
            for writer in writers:
                writer.join()

            persisted = path.read_text(encoding="utf-8")
            leftovers = list(path.parent.glob(".snapshot.json.*.tmp"))

        self.assertEqual(errors, [])
        self.assertIn(persisted, {'{"writer":1}\n', '{"writer":2}\n'})
        self.assertEqual(leftovers, [])

    def test_configure_outlook_preserves_other_private_settings(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.json"
            path.write_text(json.dumps({"kindle": {"hosts": ["kindle.local"]}}), encoding="utf-8")

            configure_outlook(path)
            document = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(document["kindle"]["hosts"], ["kindle.local"])
        self.assertTrue(document["outlook"]["enabled"])
        self.assertEqual(document["outlook"]["syncAfter"], "07:00")

    def test_outlook_automatic_sync_runs_at_most_once_per_day(self) -> None:
        zone = ZoneInfo("Asia/Shanghai")
        now = datetime(2026, 10, 23, 7, 5, tzinfo=zone)
        event = OutlookEvent(
            "opaque",
            "课程",
            TimeRange(now + timedelta(hours=1), now + timedelta(hours=2)),
            "busy",
            False,
        )
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "kindle": {"hosts": []},
                        "outlook": {"enabled": True},
                    }
                ),
                encoding="utf-8",
            )
            calendar = OutlookCalendar(
                now,
                TimeRange(
                    now.replace(hour=1, minute=0),
                    now.replace(hour=1, minute=0) + timedelta(days=2),
                ),
                (event,),
            )
            with patch("time_mirror.runtime.datetime") as clock:
                clock.now.return_value = now
                first = outlook_sync_window(config_path, root)
                write_calendar_cache(root / "imports" / "outlook-calendar.json", calendar)
                repeated = outlook_sync_window(config_path, root)

        self.assertIsNotNone(first)
        self.assertIsNone(repeated)

