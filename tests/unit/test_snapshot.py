import json
import sqlite3
from contextlib import closing
from datetime import datetime, time, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from zoneinfo import ZoneInfo

from time_mirror.collectors.activitywatch import AppInterval, AppSummary
from time_mirror.collectors.activitywatch_android import PhoneAppSummary, PhoneSegment, PhoneUsage
from time_mirror.collectors.outlook_calendar import OutlookEvent
from time_mirror.core import ReserveRule, TimeRange
from time_mirror.snapshot import build_snapshot, validate_snapshot


ZONE = ZoneInfo("Asia/Shanghai")


class SnapshotTests(TestCase):
    def test_axis_uses_combined_top_five_and_single_visible_interval(self):
        now = _at(20)
        pc = (AppInterval('ChatGPT.exe', TimeRange(_at(8), _at(12))),
              AppInterval('Code.exe', TimeRange(_at(13), _at(15))),
              AppInterval('sixth.exe', TimeRange(_at(19), _at(19, 10))))
        phone = PhoneUsage(0, (), now, activity_segments=(
            PhoneSegment('ChatGPT', TimeRange(_at(9), _at(10))),
            PhoneSegment('A', TimeRange(_at(10), _at(11))),
            PhoneSegment('B', TimeRange(_at(15), _at(16))),
            PhoneSegment('C', TimeRange(_at(16), _at(17))),
        ))
        snapshot = build_snapshot(now, None, 'unavailable', None, 0,
                                  computer_status='ok', computer_segments=pc,
                                  phone_status='ok', phone_usage=phone)
        segments = snapshot['dayAxis']['appSegments']
        names = {item['app'] for item in segments}
        self.assertEqual(len(names), 5)
        self.assertNotIn('sixth.exe', names)
        self.assertIn('ChatGPT', names)
        self.assertTrue(all(item['minutes'] >= 10 for item in segments))
        ordered = sorted(segments, key=lambda item: item['startAt'])
        self.assertTrue(all(a['endAt'] <= b['startAt'] for a, b in zip(ordered, ordered[1:])))

    def test_linkedin_is_learning_and_does_not_enter_entertainment_details(self):
        now = _at(12)
        usage = PhoneUsage(3600, (), now, activity_segments=(
            PhoneSegment('LinkedIn', TimeRange(_at(10), _at(11))),))
        snapshot = build_snapshot(now, None, 'unavailable', None, 0,
                                  phone_status='ok', phone_usage=usage, computer_status='ok',
                                  computer_segments=(AppInterval('Code.exe', TimeRange(_at(10), _at(11))),))
        study = next(item for item in snapshot['allocation']['categories'] if item['id'] == 'study')
        self.assertEqual(study['seconds'], 3600)
        self.assertEqual(snapshot['allocation']['entertainmentBreakdown'], [])

    def test_phone_chatgpt_is_study_alone_and_with_computer_but_game_wins(self):
        now = _at(14)
        usage = PhoneUsage(10800, (), now, activity_segments=(
            PhoneSegment('ChatGPT', TimeRange(_at(10), _at(13))),))
        computer = (AppInterval('Code.exe', TimeRange(_at(11), _at(12))),
                    AppInterval('tlou-ii.exe', TimeRange(_at(12), _at(13))))
        snapshot = build_snapshot(now, None, 'unavailable', None, 0,
                                  phone_status='ok', phone_usage=usage,
                                  computer_status='ok', computer_segments=computer)
        categories = {item['id']: item['seconds'] for item in snapshot['allocation']['categories']}
        self.assertEqual(categories['study'], 7200)
        self.assertEqual(categories['entertainment'], 3600)
        self.assertEqual(snapshot['allocation']['entertainmentBreakdown'][0]['name'], 'tlou-ii.exe')

    def test_sleep_display_uses_only_complete_raw_session(self):
        now = _at(12)
        actual = TimeRange(_at(1), _at(7))
        empty = build_snapshot(now, None, 'unavailable', None, 0, sleep_ranges=(actual,))
        self.assertIsNone(empty['sleepSummary'])
        full = TimeRange(_at(0) - timedelta(hours=1), _at(6))
        recorded = build_snapshot(now, None, 'unavailable', None, 0,
                                  sleep_ranges=(actual,), sleep_sessions=(full,))
        self.assertEqual(recorded['sleepSummary']['startAt'], full.start.isoformat())
        self.assertEqual(recorded['sleepSummary']['endAt'], full.end.isoformat())
        self.assertEqual(recorded['sleepSummary']['minutes'], 420)
        self.assertFalse(recorded['sleepSummary']['estimated'])

    def test_remaining_and_bedtime_reserve_end_at_midnight(self):
        now = _at(23)
        snapshot = build_snapshot(now, None, 'unavailable', None, 0,
                                  reserve_plan=(ReserveRule('睡前准备', 90, time(0)),))
        self.assertEqual(snapshot['dayStart'], _at(0).isoformat())
        self.assertEqual(snapshot['dayEnd'], (_at(0) + timedelta(days=1)).isoformat())
        self.assertEqual(snapshot['remaining']['untilAt'], snapshot['dayEnd'])
        self.assertEqual(snapshot['remaining']['countdownMinutes'], 60)
        self.assertEqual(snapshot['remaining']['totalMinutes'], 0)
        self.assertEqual(snapshot['dayAxis']['reserves'], [
            {'startAt': now.isoformat(), 'endAt': snapshot['dayEnd']}])

    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.path = Path(self.temporary.name) / "statistics.sqlite3"
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("CREATE TABLE book (id INTEGER PRIMARY KEY, title TEXT, authors TEXT)")
            connection.execute(
                "CREATE TABLE page_stat_data (id_book INTEGER, page INTEGER, start_time INTEGER, duration INTEGER)"
            )
            connection.execute("INSERT INTO book VALUES (1, '测试书', '作者')")
            connection.commit()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_builds_honest_partial_snapshot_from_observed_reading(self) -> None:
        now = datetime(2026, 8, 28, 14, 0, tzinfo=ZONE)
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute(
                "INSERT INTO page_stat_data VALUES (?, ?, ?, ?)",
                (1, 10, int(datetime(2026, 8, 28, 13, 30, tzinfo=ZONE).timestamp()), 900),
            )
            connection.commit()

        snapshot = build_snapshot(now, self.path, "ok", now, 210)

        validate_snapshot(snapshot)
        self.assertEqual(snapshot["statisticalDate"], "2026-08-28")
        self.assertEqual(snapshot["coverage"]["classifiedMinutes"], 15)
        self.assertEqual(snapshot["devices"][0]["totalMinutes"], 15)
        self.assertEqual(snapshot["devices"][0]["books"][0]["title"], "测试书")
        self.assertEqual(snapshot["remaining"]["totalMinutes"], 390)
        self.assertIsNone(snapshot["remaining"]["largestBlockMinutes"])
        self.assertEqual(snapshot["sources"]["computer"]["status"], "not_connected")

    def test_does_not_count_reading_duration_beyond_now(self) -> None:
        now = datetime(2026, 8, 28, 14, 0, tzinfo=ZONE)
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute(
                "INSERT INTO page_stat_data VALUES (?, ?, ?, ?)",
                (1, 10, int(datetime(2026, 8, 28, 13, 50, tzinfo=ZONE).timestamp()), 1800),
            )
            connection.commit()

        snapshot = build_snapshot(now, self.path, "cached", now, 210)

        self.assertEqual(snapshot["coverage"]["classifiedMinutes"], 10)
        self.assertEqual(snapshot["devices"][0]["books"][0]["minutes"], 10)
        self.assertEqual(snapshot["highlights"][0]["endAt"], now.isoformat())
        self.assertEqual(snapshot["devices"][0]["recent"]["lastReadAt"], now.isoformat())

    def test_midnight_boundary_has_no_elapsed_timeline(self) -> None:
        now = datetime(2026, 8, 28, 0, 0, tzinfo=ZONE)

        snapshot = build_snapshot(now, self.path, "cached", now, 210)

        self.assertEqual(snapshot["coverage"]["elapsedMinutes"], 0)
        self.assertEqual(len(snapshot["timeline"]), 1)
        self.assertEqual(snapshot["timeline"][0]["state"], "open")

    def test_remaining_note_lists_only_pending_reserves(self) -> None:
        now = datetime(2026, 8, 28, 22, 39, tzinfo=ZONE)
        bedtime = ReserveRule("睡前准备", 90, time(0))

        snapshot = build_snapshot(
            now,
            self.path,
            "cached",
            now,
            90,
            reserve_breakdown=(bedtime,),
        )

        self.assertEqual(snapshot["remaining"]["totalMinutes"], 0)
        self.assertEqual(
            snapshot["remaining"]["reserveBreakdown"],
            [{"label": "睡前准备", "minutes": 90}],
        )
        self.assertIn("睡前准备", snapshot["remaining"]["note"])

    def test_missing_database_is_explicitly_unavailable(self) -> None:
        now = datetime(2026, 8, 28, 14, 0, tzinfo=ZONE)

        snapshot = build_snapshot(now, None, "unavailable", None, 210)

        self.assertEqual(snapshot["sources"]["koreader"]["status"], "unavailable")
        self.assertIsNone(snapshot["sources"]["koreader"]["collectedAt"])
        self.assertEqual(snapshot["devices"][0]["books"], [])

    def test_includes_only_aggregated_computer_apps_when_connected(self) -> None:
        now = datetime(2026, 8, 28, 14, 0, tzinfo=ZONE)

        snapshot = build_snapshot(
            now,
            None,
            "unavailable",
            None,
            210,
            computer_status="ok",
            computer_collected_at=now,
            computer_apps=(AppSummary("Code.exe", 3900), AppSummary("msedge.exe", 600)),
        )

        computer = snapshot["devices"][1]
        self.assertEqual(computer["status"], "ok")
        self.assertEqual(computer["totalMinutes"], 75)
        self.assertEqual(computer["apps"][0], {"name": "Code.exe", "minutes": 65})
        self.assertNotIn("电脑", snapshot["prompt"]["text"])

    def test_short_computer_activity_never_exceeds_displayed_total(self) -> None:
        now = datetime(2026, 8, 28, 14, 0, tzinfo=ZONE)

        snapshot = build_snapshot(
            now,
            None,
            "unavailable",
            None,
            210,
            computer_status="ok",
            computer_collected_at=now,
            computer_apps=(AppSummary("ChatGPT.exe", 20),),
        )

        self.assertEqual(snapshot["devices"][1]["totalMinutes"], 1)
        self.assertEqual(snapshot["devices"][1]["apps"][0]["minutes"], 1)

    def test_includes_aggregated_phone_context_without_private_identifiers(self) -> None:
        now = datetime(2026, 8, 28, 14, 0, tzinfo=ZONE)
        longest = TimeRange(
            datetime(2026, 8, 28, 12, 0, tzinfo=ZONE),
            datetime(2026, 8, 28, 12, 45, tzinfo=ZONE),
        )
        phone_usage = PhoneUsage(
            total_seconds=5400,
            apps=(
                PhoneAppSummary("阅读器", "private.reader.package", 3600, 5, longest),
                PhoneAppSummary("微信", "com.tencent.mm", 1800, 12, None),
            ),
            latest_event_at=datetime(2026, 8, 28, 13, 30, tzinfo=ZONE),
        )

        snapshot = build_snapshot(
            now,
            None,
            "unavailable",
            None,
            210,
            phone_status="ok",
            phone_collected_at=now,
            phone_usage=phone_usage,
        )

        phone = snapshot["devices"][2]
        self.assertEqual(phone["totalMinutes"], 90)
        self.assertEqual(phone["apps"][0]["sessions"], 5)
        self.assertEqual(phone["longestSegment"]["app"], "阅读器")
        self.assertEqual(phone["longestSegment"]["startAt"], longest.start.isoformat())
        self.assertEqual(phone["frequentApps"][0]["name"], "微信")
        serialized = json.dumps(snapshot, ensure_ascii=False)
        self.assertNotIn("private.reader.package", serialized)
        self.assertNotIn("classname", serialized)

    def test_marks_stale_phone_export_as_cached(self) -> None:
        now = datetime(2026, 8, 28, 14, 0, tzinfo=ZONE)
        usage = PhoneUsage(0, (), None)

        snapshot = build_snapshot(
            now,
            None,
            "unavailable",
            None,
            210,
            phone_status="cached",
            phone_collected_at=now,
            phone_usage=usage,
        )

        self.assertEqual(snapshot["sources"]["phone"]["errorCode"], "stale_export")
        self.assertIn("手动缓存", snapshot["prompt"]["text"])

    def test_day_axis_clips_phone_filters_short_segments_and_keeps_totals(self) -> None:
        now = _at(14)
        segments = (
            PhoneSegment("rednote", TimeRange(_at(12), _at(12) + timedelta(seconds=599.9))),
            PhoneSegment("rednote", TimeRange(_at(12, 20), _at(12, 30))),
            PhoneSegment("bilibili", TimeRange(_at(13, 55), _at(14, 20))),
            PhoneSegment("bilibili", TimeRange(_at(0, 50), _at(1, 15))),
        )
        usage = PhoneUsage(1800, (), now, segments)
        rules = (ReserveRule("午餐", 60, time(14)), ReserveRule("睡前准备", 90, time(0)))
        original = build_snapshot(now, None, "unavailable", None, 90,
                                  phone_status="ok", phone_usage=PhoneUsage(1800, (), now))
        snapshot = build_snapshot(now, None, "unavailable", None, 90,
                                 reserve_plan=rules, phone_status="cached", phone_usage=usage)
        axis = snapshot["dayAxis"]
        self.assertEqual(axis["minimumPhoneMinutes"], 10)
        self.assertEqual(axis["phone"], [
            {"app": "bilibili", "startAt": _at(0, 50).isoformat(), "endAt": _at(1, 15).isoformat()},
            {"app": "rednote", "startAt": _at(12, 20).isoformat(), "endAt": _at(12, 30).isoformat()},
        ])
        self.assertEqual(len(axis["reserves"]), 1)
        self.assertEqual(axis["reserves"][0]["endAt"], snapshot["dayEnd"])
        for key in ("coverage", "timeline"):
            self.assertEqual(snapshot[key], original[key])
        self.assertEqual(snapshot["remaining"]["totalMinutes"], original["remaining"]["totalMinutes"])
        self.assertEqual(snapshot["devices"][2]["totalMinutes"], original["devices"][2]["totalMinutes"])
        self.assertNotIn("午餐", json.dumps(axis, ensure_ascii=False))

    def test_error_phone_has_no_axis_segments(self) -> None:
        now = _at(14)
        usage = PhoneUsage(3600, (), now, (PhoneSegment("rednote", TimeRange(_at(12), _at(13))),))
        snapshot = build_snapshot(now, None, "unavailable", None, 0, phone_status="error", phone_usage=usage)
        self.assertEqual(snapshot["dayAxis"]["phone"], [])

    def test_allocation_integrates_game_phone_reading_and_course_without_duplication(self):
        now = _at(14)
        phone = PhoneUsage(1800, (), now, activity_segments=(
            PhoneSegment("微信读书", TimeRange(_at(10), _at(10, 20)), True),
            PhoneSegment("小红书", TimeRange(_at(11), _at(11, 10))),
        ))
        pc = (AppInterval("TLOU-II.EXE", TimeRange(_at(9), _at(10))),
              AppInterval("Code.exe", TimeRange(_at(10, 10), _at(10, 20))))
        course = (OutlookEvent("course", "ECA5102 Sectional Teaching", TimeRange(_at(10), _at(12)), "busy", False),)
        result = build_snapshot(now, None, "unavailable", None, 0,
                                phone_usage=phone, computer_segments=pc, calendar_events=course,
                                phone_status="ok", computer_status="ok", calendar_status="ok")
        values = {item["id"]: item for item in result["allocation"]["categories"]}
        self.assertEqual(values["entertainment"]["seconds"], 4200)
        self.assertEqual(values["reading"]["seconds"], 1200)
        self.assertEqual(values["study"]["seconds"], 5400)
        self.assertEqual(result["allocation"]["fixedMinutes"], 120)
        self.assertEqual(sum(item["seconds"] for item in values.values()), 86400)

    def test_weread_prioritizes_reading_over_normal_pc_but_not_games(self):
        phone = PhoneUsage(3600, (), _at(12), activity_segments=(
            PhoneSegment("微信读书", TimeRange(_at(10), _at(11)), True),
        ))
        pc = (AppInterval("Code.exe", TimeRange(_at(10), _at(10, 30))),
              AppInterval("tlou-ii.exe", TimeRange(_at(10, 30), _at(11))))
        result = build_snapshot(_at(12), None, "unavailable", None, 0,
                                phone_usage=phone, computer_segments=pc,
                                phone_status="ok", computer_status="ok")
        values = {item['id']: item['seconds'] for item in result['allocation']['categories']}
        self.assertEqual(values['reading'], 1800)
        self.assertEqual(values['entertainment'], 1800)
        self.assertEqual(result['allocation']['entertainmentBreakdown'][0]['name'], 'tlou-ii.exe')

    def test_short_weread_with_pc_does_not_qualify_for_reading(self):
        phone = PhoneUsage(540, (), _at(12), activity_segments=(
            PhoneSegment("微信读书", TimeRange(_at(10), _at(10, 9)), True),
        ))
        pc = (AppInterval("Code.exe", TimeRange(_at(10), _at(10, 9))),)
        result = build_snapshot(_at(12), None, "unavailable", None, 0,
                                phone_usage=phone, computer_segments=pc,
                                phone_status="ok", computer_status="ok")
        values = {item['id']: item['seconds'] for item in result['allocation']['categories']}
        self.assertEqual(values['reading'], 0)
        self.assertEqual(values['entertainment'], 540)

    def test_kindlereader_adjacent_pages_form_session(self):
        with closing(sqlite3.connect(self.path)) as connection:
            for index in range(10):
                connection.execute("INSERT INTO page_stat_data VALUES (?,?,?,?)", (
                    1, index, int((_at(10) + timedelta(minutes=index)).timestamp()), 60))
            connection.commit()
        result = build_snapshot(_at(12), self.path, "ok", _at(12), 0)
        reading = next(item for item in result["allocation"]["categories"] if item["id"] == "reading")
        self.assertEqual(reading["seconds"], 600)

    def test_kindlereader_different_short_books_do_not_qualify_together(self):
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("INSERT INTO book VALUES (2,'另一本合成书','测试作者')")
            connection.executemany("INSERT INTO page_stat_data VALUES (?,?,?,?)", [
                (1,1,int(_at(10).timestamp()),300), (2,1,int(_at(10,5).timestamp()),300)])
            connection.commit()
        result = build_snapshot(_at(12), self.path, "ok", _at(12), 0)
        reading = next(item for item in result["allocation"]["categories"] if item["id"] == "reading")
        self.assertEqual(reading["seconds"], 0)

    def test_remaining_uses_same_union_as_allocation_with_overlapping_reserves(self):
        rules = (ReserveRule("晚餐", 90, time(21)), ReserveRule("返程", 30, time(20)))
        result = build_snapshot(_at(19), None, "unavailable", None, 120, reserve_plan=rules)
        self.assertEqual(result["allocation"]["futureReservedMinutes"], 90)
        self.assertEqual(result["remaining"]["totalMinutes"], 210)

    def test_axis_reserve_only_keeps_future_part_of_current_window(self) -> None:
        now = _at(13, 59)
        snapshot = build_snapshot(now, None, "unavailable", None, 0,
                                  reserve_plan=(ReserveRule("午餐", 60, time(14)),))
        self.assertEqual(snapshot["dayAxis"]["reserves"], [
            {"startAt": now.isoformat(), "endAt": _at(14).isoformat()},
        ])

    def test_calendar_overlap_reduces_remaining_once_and_tentative_does_not(self) -> None:
        now = datetime(2026, 8, 28, 14, 0, tzinfo=ZONE)
        events = (
            OutlookEvent("one", "课程", TimeRange(_at(15), _at(16)), "busy", False),
            OutlookEvent("two", "讨论", TimeRange(_at(15, 30), _at(17)), "busy", False),
            OutlookEvent("three", "可能参加", TimeRange(_at(18), _at(19)), "tentative", False),
        )

        snapshot = build_snapshot(
            now,
            None,
            "unavailable",
            None,
            90,
            calendar_status="ok",
            calendar_collected_at=now,
            calendar_events=events,
        )

        self.assertEqual(snapshot["remaining"]["totalMinutes"], 390)
        self.assertEqual(snapshot["remaining"]["scheduledFreeMinutes"], 480)
        self.assertIn("120 分钟", snapshot["remaining"]["note"])
        self.assertEqual(snapshot["next"], {"label": "课程", "startAt": _at(15).isoformat()})
        self.assertEqual(snapshot["sources"]["calendar"]["status"], "ok")
        self.assertEqual(len(snapshot["calendarEvents"]), 3)
        self.assertEqual(
            sum(item["minutes"] for item in snapshot["timeline"] if item["state"] == "planned"),
            120,
        )

    def test_active_calendar_event_is_labeled_as_plan_not_observed_fact(self) -> None:
        now = datetime(2026, 8, 28, 14, 0, tzinfo=ZONE)
        event = OutlookEvent("one", "组会", TimeRange(_at(13, 30), _at(14, 30)), "busy", False)

        snapshot = build_snapshot(
            now,
            None,
            "unavailable",
            None,
            0,
            calendar_status="cached",
            calendar_collected_at=now,
            calendar_events=(event,),
            calendar_error_code="daily_cache",
        )

        self.assertEqual(snapshot["current"]["label"], "组会")
        self.assertIn("尚未确认", snapshot["current"]["detail"])
        self.assertEqual(snapshot["sources"]["calendar"]["errorCode"], "daily_cache")


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, 28, hour, minute, tzinfo=ZONE)

