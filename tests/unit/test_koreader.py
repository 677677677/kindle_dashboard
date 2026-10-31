import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from time_mirror.collectors.koreader import DatabaseSchemaError, KoreaderDatabase, validate_database, copy_snapshot
from time_mirror.core import statistical_day_bounds


class KoreaderDatabaseTests(TestCase):
    def test_ssh_backup_transfers_binary_and_preserves_host_identity(self):
        self._create_database()
        payload = self.path.read_bytes()
        target = self.path.parent / 'received.sqlite3'
        def receive(command, **kwargs):
            self.assertEqual(command[0], 'ssh')
            self.assertIn('StrictHostKeyChecking=yes', command)
            self.assertIn('HostKeyAlias=trusted-kindle', command)
            self.assertIn('sqlite3 -readonly', command[-1])
            self.assertIn('.backup ', command[-1])
            self.assertIn('trap ', command[-1])
            kwargs['stdout'].write(payload)
        with patch('time_mirror.collectors.koreader.subprocess.run', side_effect=receive):
            copy_snapshot('example.invalid', 2222, 'root', Path('key'),
                          "/mnt/us/a 'quoted'.sqlite3", target, host_key_alias='trusted-kindle')
        self.assertEqual(target.read_bytes(), payload)
        self.assertEqual(list(target.parent.glob('*.partial')), [])

    def test_failed_download_does_not_replace_trusted_database(self):
        self._create_database()
        original = self.path.read_bytes()
        def corrupted(command, **kwargs):
            kwargs['stdout'].write(b'not sqlite')
        with patch('time_mirror.collectors.koreader.subprocess.run', side_effect=corrupted):
            with self.assertRaises(sqlite3.DatabaseError):
                copy_snapshot('example.invalid', 2222, 'root', Path('key'), '/stats', self.path)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.glob('*.partial')), [])

    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.path = Path(self.temporary.name) / "statistics.sqlite3"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_reads_and_summarizes_synthetic_book_events(self) -> None:
        window = statistical_day_bounds(date(2026, 8, 28))
        self._create_database()
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("INSERT INTO book VALUES (1, '测试书', '作者')")
            connection.executemany(
                "INSERT INTO page_stat_data VALUES (?, ?, ?, ?)",
                [
                    (1, 10, int(window.start.timestamp()) + 60, 120),
                    (1, 11, int(window.start.timestamp()) + 240, 180),
                ],
            )
            connection.commit()

        summaries = KoreaderDatabase(self.path).summarize(window)
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0].title, "测试书")
        self.assertEqual(summaries[0].seconds, 300)

    def test_rejects_unknown_schema(self) -> None:
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("CREATE TABLE book (id INTEGER, title TEXT)")
            connection.commit()
        with self.assertRaises(DatabaseSchemaError):
            KoreaderDatabase(self.path).reading_events(statistical_day_bounds(date(2026, 8, 28)))

    def test_ignores_non_positive_duration_and_unions_overlap(self) -> None:
        window = statistical_day_bounds(date(2026, 8, 28))
        self._create_database()
        start = int(window.start.timestamp())
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("INSERT INTO book VALUES (1, '测试书', '作者')")
            connection.executemany(
                "INSERT INTO page_stat_data VALUES (?, ?, ?, ?)",
                [
                    (1, 10, start + 60, 180),
                    (1, 11, start + 120, 180),
                    (1, 12, start + 500, 0),
                    (1, 13, start + 600, -10),
                ],
            )
            connection.commit()

        summary = KoreaderDatabase(self.path).summarize(window)[0]
        self.assertEqual(summary.seconds, 240)

    def test_integrity_validation_rejects_non_database(self) -> None:
        self.path.write_bytes(b"not sqlite")
        with self.assertRaises(sqlite3.DatabaseError):
            validate_database(self.path)

    def _create_database(self) -> None:
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("CREATE TABLE book (id INTEGER PRIMARY KEY, title TEXT, authors TEXT)")
            connection.execute(
                "CREATE TABLE page_stat_data (id_book INTEGER, page INTEGER, start_time INTEGER, duration INTEGER)"
            )
            connection.commit()

