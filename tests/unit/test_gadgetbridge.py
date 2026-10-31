import sqlite3
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from time_mirror.collectors.gadgetbridge import GadgetbridgeError, read_sleep, read_sleep_sessions
from time_mirror.core import statistical_day_bounds


class GadgetbridgeTests(TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.path = Path(self.temporary.name) / "Gadgetbridge.db"
        self.window = statistical_day_bounds(date(2026, 10, 27))
        self.now = self.window.start + timedelta(hours=14)
        self.start = int(self.window.start.timestamp())

    def tearDown(self):
        self.temporary.cleanup()

    def create(self, rows=()):
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute('CREATE TABLE HUAWEI_ACTIVITY_SAMPLE ('
                               'TIMESTAMP INTEGER, OTHER_TIMESTAMP INTEGER, DEVICE_ID INTEGER, '
                               'USER_ID INTEGER, SOURCE INTEGER, RAW_KIND INTEGER)')
            connection.executemany('INSERT INTO HUAWEI_ACTIVITY_SAMPLE VALUES (?, ?, ?, ?, ?, ?)', rows)
            connection.commit()

    def row(self, begin, end, kind=6, source=10, device=1, user=1):
        return (self.start + begin, self.start + end, device, user, source, kind)

    def test_seconds_paired_markers_overlap_and_awake_exclusion(self):
        self.create([self.row(0, 3600), self.row(3600, 0), self.row(1800, 5400, 7),
                     self.row(0, 5400, 0x5658), self.row(5400, 6000, 0x5656),
                     self.row(6000, 6600, 0x5659), self.row(6600, 7200, source=99)])
        result = read_sleep(self.path, self.window, self.now)
        self.assertEqual(len(result.intervals), 1)
        self.assertEqual((result.intervals[0].end - result.intervals[0].start).total_seconds(), 6600)
        self.assertEqual(result.latest_event_at.timestamp(), self.start + 6600)

    def test_cross_day_clips_and_does_not_hide_latest_end(self):
        self.create([self.row(-3600, 3600), self.row(-7200, -3600, source=13)])
        result = read_sleep(self.path, self.window, self.now)
        self.assertEqual(result.intervals[0].start, self.window.start)
        self.assertEqual(result.intervals[0].minutes, 60)
        self.assertEqual(result.latest_event_at.timestamp(), self.start + 3600)

    def test_historical_window_is_clipped_to_its_end(self):
        self.create([self.row(0, 7200)])
        short_window = type(self.window)(self.window.start, self.window.start + timedelta(hours=1))
        result = read_sleep(self.path, short_window, self.now)
        self.assertEqual(result.intervals[0].minutes, 60)

    def test_future_record_is_error(self):
        self.create([self.row(0, 15 * 3600)])
        with self.assertRaises(GadgetbridgeError):
            read_sleep(self.path, self.window, self.now)

    def test_empty_sleep_is_unknown_not_error_and_closes_connection(self):
        self.create([self.row(0, 60, kind=-1)])
        result = read_sleep(self.path, self.window, self.now)
        self.assertEqual(result.intervals, ())
        self.assertIsNone(result.latest_event_at)
        self.path.unlink()

    def test_multiple_sleep_identities_rejected(self):
        self.create([self.row(0, 3600), self.row(0, 3600, device=2)])
        with self.assertRaises(GadgetbridgeError):
            read_sleep(self.path, self.window, self.now)

    def test_invalid_timestamp_and_milliseconds_rejected(self):
        self.create([self.row(0, 0)])
        with self.assertRaises(GadgetbridgeError):
            read_sleep(self.path, self.window, self.now)
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute('UPDATE HUAWEI_ACTIVITY_SAMPLE SET TIMESTAMP=TIMESTAMP*1000, '
                               'OTHER_TIMESTAMP=(OTHER_TIMESTAMP+3600)*1000')
            connection.commit()
        with self.assertRaises(GadgetbridgeError):
            read_sleep(self.path, self.window, self.now)

    def test_new_stage_data_without_supported_intervals_rejected(self):
        self.create()
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute('CREATE TABLE HUAWEI_SLEEP_STAGE_SAMPLE (TIMESTAMP INTEGER, STAGE INTEGER)')
            connection.execute('INSERT INTO HUAWEI_SLEEP_STAGE_SAMPLE VALUES (1, 1)')
            connection.commit()
        with self.assertRaisesRegex(GadgetbridgeError, '阶段'):
            read_sleep(self.path, self.window, self.now)

    def test_old_legacy_sleep_does_not_hide_unsupported_current_stages(self):
        self.create([self.row(-7200, -3600)])
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute('CREATE TABLE HUAWEI_SLEEP_STATS_SAMPLE (TIMESTAMP INTEGER)')
            connection.execute('INSERT INTO HUAWEI_SLEEP_STATS_SAMPLE VALUES (?)', (self.start * 1000,))
            connection.commit()
        with self.assertRaisesRegex(GadgetbridgeError, '阶段'):
            read_sleep(self.path, self.window, self.now)

    def test_zero_byte_and_corrupt_files_rejected(self):
        for payload in (b'', b'not a sqlite database'):
            self.path.write_bytes(payload)
            with self.assertRaises(GadgetbridgeError):
                read_sleep(self.path, self.window, self.now)
            self.path.unlink()

    def test_unknown_schema_rejected(self):
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute('CREATE TABLE unrelated (value INTEGER)')
            connection.commit()
        with self.assertRaises(GadgetbridgeError):
            read_sleep(self.path, self.window, self.now)

    def create_sessions(self, rows=()):
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute('CREATE TABLE HUAWEI_SLEEP_STATS_SAMPLE ('
                               'TIMESTAMP INTEGER, WAKEUP_TIME INTEGER, DEVICE_ID INTEGER, USER_ID INTEGER)')
            connection.executemany('INSERT INTO HUAWEI_SLEEP_STATS_SAMPLE VALUES (?, ?, ?, ?)', rows)
            connection.commit()

    def session_row(self, begin, end, device=1):
        return ((self.start + begin) * 1000, (self.start + end) * 1000, device, 1)

    def test_full_session_ms_cross_night_is_not_clipped(self):
        self.create_sessions([self.session_row(-7200, 7 * 3600)])
        sessions = read_sleep_sessions(self.path, self.window, self.now)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].start.timestamp(), self.start - 7200)
        self.assertEqual(sessions[0].end.timestamp(), self.start + 7 * 3600)

    def test_full_sessions_drop_contained_records_but_do_not_join_gaps(self):
        self.create_sessions([self.session_row(0, 6 * 3600), self.session_row(3600, 3 * 3600),
                              self.session_row(0, 6 * 3600), self.session_row(10 * 3600, 11 * 3600)])
        sessions = read_sleep_sessions(self.path, self.window, self.now)
        self.assertEqual(len(sessions), 2)
        self.assertEqual(sessions[0].minutes, 360)
        self.assertEqual(sessions[1].minutes, 60)

    def test_full_session_belongs_to_wakeup_window(self):
        self.create_sessions([self.session_row(-7200, -3600)])
        self.assertEqual(read_sleep_sessions(self.path, self.window, self.now), ())

    def test_full_session_missing_or_empty_table_does_not_use_legacy(self):
        self.create([self.row(0, 6 * 3600)])
        self.assertEqual(read_sleep_sessions(self.path, self.window, self.now), ())
        self.create_sessions()
        self.assertEqual(read_sleep_sessions(self.path, self.window, self.now), ())
        self.path.unlink()

    def test_full_session_invalid_units_future_reversed_or_missing_end_rejected(self):
        self.create_sessions()
        invalid = [self.session_row(0, 15 * 3600), self.session_row(3600, 0),
                   (self.start, self.start + 3600, 1, 1), (self.start * 1000, None, 1, 1)]
        for row in invalid:
            with self.subTest(row=row):
                with closing(sqlite3.connect(self.path)) as connection:
                    connection.execute('DELETE FROM HUAWEI_SLEEP_STATS_SAMPLE')
                    connection.execute('INSERT INTO HUAWEI_SLEEP_STATS_SAMPLE VALUES (?, ?, ?, ?)', row)
                    connection.commit()
                with self.assertRaises(GadgetbridgeError):
                    read_sleep_sessions(self.path, self.window, self.now)

    def test_full_session_missing_columns_rejected(self):
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute('CREATE TABLE HUAWEI_SLEEP_STATS_SAMPLE (TIMESTAMP INTEGER)')
            connection.commit()
        with self.assertRaises(GadgetbridgeError):
            read_sleep_sessions(self.path, self.window, self.now)

    def test_full_session_ambiguous_device_rejected(self):
        self.create_sessions([self.session_row(0, 3600), self.session_row(0, 3600, device=2)])
        with self.assertRaises(GadgetbridgeError):
            read_sleep_sessions(self.path, self.window, self.now)

