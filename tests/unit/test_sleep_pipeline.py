import json
import sqlite3
from contextlib import closing
from datetime import datetime, time, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from zoneinfo import ZoneInfo

from time_mirror.core import statistical_day_bounds, statistical_date_for
from time_mirror.core import ReserveRule, TimeRange
from time_mirror.runtime import _read_health, load_config
from time_mirror.snapshot import build_snapshot, validate_snapshot


class SleepPipelineTests(TestCase):
    def test_measured_wakeup_matches_remaining_and_allocation(self):
        now = datetime(2026, 10, 27, 7, tzinfo=ZoneInfo('Asia/Shanghai'))
        snapshot = build_snapshot(now, None, 'unavailable', None, 0,
                                  reserve_plan=(ReserveRule('晚餐', 60, time(20)),),
                                  sleep_ranges=(TimeRange(now.replace(hour=2), now.replace(hour=6)),),
                                  health_status='ok', health_latest_event_at=now.replace(hour=6))
        self.assertEqual(snapshot['remaining']['totalMinutes'], 16 * 60)
        self.assertEqual(snapshot['allocation']['availableMinutes'], 16 * 60)

    def test_empty_then_real_sleep_reaches_snapshot_without_private_fields(self):
        now = datetime(2026, 10, 27, 16, tzinfo=ZoneInfo('Asia/Shanghai'))
        day = statistical_day_bounds(statistical_date_for(now))
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / 'sleep.db'
            config = load_config(root / 'missing.json')
            config['gadgetbridge'] = {'enabled': True, 'databasePath': str(path)}
            self.assertEqual(_read_health(config, root, now, day).error_code, 'missing_export')
            path.write_bytes(b'')
            self.assertEqual(_read_health(config, root, now, day).status, 'error')
            with closing(sqlite3.connect(path)) as database:
                database.execute('CREATE TABLE HUAWEI_ACTIVITY_SAMPLE (TIMESTAMP INTEGER, '
                                 'OTHER_TIMESTAMP INTEGER, DEVICE_ID INTEGER, USER_ID INTEGER, '
                                 'SOURCE INTEGER, RAW_KIND INTEGER)')
                database.commit()
                empty = _read_health(config, root, now, day)
                self.assertEqual(empty.error_code, 'no_sleep_record')
                self.assertEqual(empty.intervals, ())
                start = int(now.replace(hour=2).timestamp())
                end = int(now.replace(hour=9).timestamp())
                database.execute('INSERT INTO HUAWEI_ACTIVITY_SAMPLE VALUES (?, ?, 987654321, 123456789, 10, 6)',
                                 (start, end))
                database.commit()
            health = _read_health(config, root, now, day)
            snapshot = build_snapshot(now, None, 'unavailable', None, 0,
                                      sleep_ranges=health.intervals, health_status=health.status,
                                      health_collected_at=health.collected_at,
                                      health_latest_event_at=health.latest_event_at)
            validate_snapshot(snapshot)
            sleep = next(item for item in snapshot['allocation']['categories'] if item['id'] == 'sleep')
            self.assertEqual(sleep['seconds'], 7 * 3600)
            self.assertEqual(sleep['estimatedSeconds'], 0)
            self.assertFalse(snapshot['sources']['health']['sleepEstimated'])
            self.assertNotIn('987654321', json.dumps(snapshot))
            self.assertNotIn('123456789', json.dumps(snapshot))
            path.unlink()

    def test_missing_health_never_invents_sleep(self):
        now = datetime(2026, 10, 27, 16, tzinfo=ZoneInfo('Asia/Shanghai'))
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = load_config(root / 'missing.json')
            self.assertEqual(_read_health(config, root, now, statistical_day_bounds(now.date())).status,
                             'not_connected')
        snapshot = build_snapshot(now, None, 'unavailable', None, 0)
        self.assertFalse(snapshot['sources']['health']['sleepEstimated'])
        self.assertIsNone(snapshot['sleepSummary'])
        sleep = next(item for item in snapshot['allocation']['categories'] if item['id'] == 'sleep')
        self.assertEqual(sleep['seconds'], 0)
        self.assertEqual(sleep['estimatedSeconds'], 0)

    def test_full_session_fallback_is_split_at_midnight_and_keeps_wakeup_summary(self):
        now = datetime(2026, 10, 30, 12, tzinfo=ZoneInfo('Asia/Shanghai'))
        today = statistical_day_bounds(now.date())
        yesterday = statistical_day_bounds(now.date() - timedelta(days=1))
        begin, end = today.start - timedelta(hours=1), today.start + timedelta(hours=7)
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / 'sleep.db'
            with closing(sqlite3.connect(path)) as database:
                database.execute('CREATE TABLE HUAWEI_ACTIVITY_SAMPLE (TIMESTAMP INTEGER, '
                                 'OTHER_TIMESTAMP INTEGER, DEVICE_ID INTEGER, USER_ID INTEGER, '
                                 'SOURCE INTEGER, RAW_KIND INTEGER)')
                database.execute('CREATE TABLE HUAWEI_SLEEP_STATS_SAMPLE (TIMESTAMP INTEGER, '
                                 'WAKEUP_TIME INTEGER, DEVICE_ID INTEGER, USER_ID INTEGER)')
                database.execute('INSERT INTO HUAWEI_SLEEP_STATS_SAMPLE VALUES (?, ?, 1, 1)',
                                 (int(begin.timestamp() * 1000), int(end.timestamp() * 1000)))
                database.commit()
            config = load_config(root / 'missing.json')
            config['gadgetbridge'] = {'enabled': True, 'databasePath': str(path)}
            for day, minutes in ((yesterday, 60), (today, 420)):
                health = _read_health(config, root, now, day)
                self.assertEqual(health.error_code, 'sleep_session_span')
                snapshot = build_snapshot(now if day == today else day.end - timedelta(microseconds=1),
                                          None, 'unavailable', None, 0,
                                          sleep_ranges=health.intervals, sleep_sessions=health.sessions,
                                          health_status=health.status, health_error_code=health.error_code)
                sleep = next(item for item in snapshot['allocation']['categories'] if item['id'] == 'sleep')
                self.assertEqual(sleep['minutes'], minutes)
                self.assertEqual(sleep['estimatedSeconds'], 0)
                if day == yesterday:
                    self.assertIsNone(snapshot['sleepSummary'])
                else:
                    self.assertEqual(snapshot['sleepSummary']['minutes'], 480)
                    self.assertEqual(snapshot['sources']['health']['errorCode'], 'sleep_session_span')

    def test_full_session_does_not_mask_invalid_activity_schema(self):
        now = datetime(2026, 10, 30, 12, tzinfo=ZoneInfo('Asia/Shanghai'))
        day = statistical_day_bounds(now.date())
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / 'sleep.db'
            with closing(sqlite3.connect(path)) as database:
                database.execute('CREATE TABLE HUAWEI_SLEEP_STATS_SAMPLE (TIMESTAMP INTEGER, '
                                 'WAKEUP_TIME INTEGER, DEVICE_ID INTEGER, USER_ID INTEGER)')
                database.execute('INSERT INTO HUAWEI_SLEEP_STATS_SAMPLE VALUES (?, ?, 1, 1)',
                                 (int(day.start.timestamp() * 1000), int(now.timestamp() * 1000)))
                database.commit()
            config = load_config(root / 'missing.json')
            config['gadgetbridge'] = {'enabled': True, 'databasePath': str(path)}
            result = _read_health(config, root, now, day)
            self.assertEqual(result.error_code, 'invalid_sleep_export')
            self.assertEqual(result.intervals, ())

    def test_recognized_sleep_stages_take_priority_over_full_session(self):
        now = datetime(2026, 10, 30, 12, tzinfo=ZoneInfo('Asia/Shanghai'))
        day = statistical_day_bounds(now.date())
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / 'sleep.db'
            with closing(sqlite3.connect(path)) as database:
                database.execute('CREATE TABLE HUAWEI_ACTIVITY_SAMPLE (TIMESTAMP INTEGER, '
                                 'OTHER_TIMESTAMP INTEGER, DEVICE_ID INTEGER, USER_ID INTEGER, '
                                 'SOURCE INTEGER, RAW_KIND INTEGER)')
                database.execute('CREATE TABLE HUAWEI_SLEEP_STATS_SAMPLE (TIMESTAMP INTEGER, '
                                 'WAKEUP_TIME INTEGER, DEVICE_ID INTEGER, USER_ID INTEGER)')
                begin = day.start + timedelta(hours=1)
                database.execute('INSERT INTO HUAWEI_SLEEP_STATS_SAMPLE VALUES (?, ?, 1, 1)',
                                 (int(begin.timestamp() * 1000), int((begin + timedelta(hours=8)).timestamp() * 1000)))
                database.execute('INSERT INTO HUAWEI_ACTIVITY_SAMPLE VALUES (?, ?, 1, 1, 10, 6)',
                                 (int(begin.timestamp()), int((begin + timedelta(hours=7)).timestamp())))
                database.commit()
            config = load_config(root / 'missing.json')
            config['gadgetbridge'] = {'enabled': True, 'databasePath': str(path)}
            result = _read_health(config, root, now, day)
            self.assertEqual(result.intervals[0].minutes, 420)
            self.assertNotEqual(result.error_code, 'sleep_session_span')
            self.assertEqual(result.sessions[0].minutes, 480)

