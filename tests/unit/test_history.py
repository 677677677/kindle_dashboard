import json
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from zoneinfo import ZoneInfo

from time_mirror.history import update_history


ZONE = ZoneInfo("Asia/Shanghai")


def write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class HistoryTests(TestCase):
    now = datetime(2026, 10, 27, 0, 30, tzinfo=ZONE)

    def record(self, phone=0, reading=0):
        return {"phoneSeconds": phone, "readingSeconds": reading,
                "inputsAvailable": {"phone": True, "computer": True, "koreader": True}}

    def test_unknown_zero_is_not_complete_and_seven_dates_do_not_skip_missing(self):
        with TemporaryDirectory() as temporary:
            result = update_history(Path(temporary), self.now, {"2026-10-25": self.record()}, write)
        self.assertEqual(len(result["days"]), 7)
        self.assertEqual(result["days"][0]["date"], "2026-10-20")
        self.assertEqual(result["days"][-1]["date"], "2026-10-26")
        self.assertIsNone(result["days"][-2]["phonePercent"])
        self.assertEqual(result["days"][-2]["status"], "partial")

    def test_recorded_values_are_visible_as_partial_without_fabricating_zero(self):
        with TemporaryDirectory() as temporary:
            result = update_history(Path(temporary), self.now, {"2026-10-25": self.record(3600, 0)}, write)
        day = result["days"][-2]
        self.assertEqual(day["phoneMinutes"], 60)
        self.assertEqual(day["phonePercent"], round(3600 / 86400 * 100, 4))
        self.assertFalse(day["phoneComplete"])
        self.assertIsNone(day["readingPercent"])
        self.assertEqual(day["status"], "partial")

    def test_recorded_reading_is_visible_as_partial_when_coverage_is_unknown(self):
        with TemporaryDirectory() as temporary:
            result = update_history(Path(temporary), self.now, {"2026-10-25": self.record(0, 1200)}, write)
        day = result["days"][-2]
        self.assertEqual(day["readingMinutes"], 20)
        self.assertFalse(day["readingComplete"])
        self.assertIsNone(day["phonePercent"])

    def test_creates_history_directory_for_atomic_writer_and_keeps_data_on_write_failure(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            def strict_writer(path, text):
                path.write_text(text, encoding="utf-8")
            update_history(root, self.now, {"2026-10-25": self.record(3600)}, strict_writer)
            self.assertTrue((root / "history" / "daily-summary.json").exists())
            def failing_writer(path, text):
                raise OSError("write failed")
            result = update_history(root, self.now, {"2026-10-25": self.record(7200)}, failing_writer)
            self.assertEqual(result["days"][-2]["phoneMinutes"], 120)

    def test_proven_zero_and_independent_series(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            proof = {"startAt": "2026-10-25T00:00:00+08:00", "endAt": "2026-10-26T00:00:00+08:00", "method": "user-confirmed"}
            write(root / "history" / "coverage.json", json.dumps({"schemaVersion": "history-coverage-v1", "days": {"2026-10-25": {"phone": proof}}}))
            result = update_history(root, self.now, {"2026-10-25": self.record()}, write)
            self.assertEqual(result["days"][-2]["phonePercent"], 0)
            self.assertIsNone(result["days"][-2]["readingPercent"])
            write(root / "history" / "coverage.json", json.dumps({"schemaVersion": "history-coverage-v1", "days": {"2026-10-25": dict.fromkeys(("phone", "computer", "koreader"), proof)}}))
            complete = update_history(root, self.now, {}, write)
            self.assertEqual(complete["days"][-2]["readingPercent"], 0)
            self.assertEqual(complete["days"][-2]["status"], "complete")

    def test_failed_refresh_preserves_candidate_but_changed_rules_drop_cache(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            update_history(root, self.now, {"2026-10-25": self.record(3600, 1200)}, write, "old")
            failed = {"phoneSeconds": None, "readingSeconds": None, "inputsAvailable": {}}
            update_history(root, self.now, {"2026-10-25": failed}, write, "old")
            persisted = json.loads((root / "history" / "daily-summary.json").read_text(encoding="utf-8"))
            self.assertEqual(persisted["days"]["2026-10-25"]["phoneSeconds"], 3600)
            updated = update_history(root, self.now, {}, write, "new")
            self.assertEqual(updated["days"][-1]["status"], "missing")

    def test_corrupt_cache_and_invalid_attestation_are_missing(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            write(root / "history" / "daily-summary.json", "not json")
            write(root / "history" / "coverage.json", '{"schemaVersion":"history-coverage-v1","days":{"2026-10-25":{"phone":{"method":"query-success"}}}}')
            result = update_history(root, self.now, {"2026-10-25": self.record()}, write)
            self.assertIsNone(result["days"][-1]["phonePercent"])

