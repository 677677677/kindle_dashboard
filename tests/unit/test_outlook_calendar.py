import json
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from zoneinfo import ZoneInfo

from time_mirror.collectors.outlook_calendar import (
    OutlookCalendar,
    OutlookResponseError,
    cache_covers,
    read_calendar_cache,
    synced_on,
    write_calendar_cache,
)
from time_mirror.core import TimeRange


ZONE = ZoneInfo("Asia/Shanghai")


class OutlookCalendarTests(TestCase):
    def test_round_trips_validated_cache_and_checks_daily_coverage(self) -> None:
        calendar = OutlookCalendar(
            _at(7),
            TimeRange(_at(1), _at(49)),
            (fetch_event := _calendar_event(),),
        )
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "outlook-calendar.json"
            write_calendar_cache(path, calendar)
            loaded = read_calendar_cache(path, "Asia/Shanghai")
            persisted = path.read_text(encoding="utf-8")

        self.assertEqual(loaded, calendar)
        self.assertTrue(cache_covers(loaded, TimeRange(_at(1), _at(25))))
        self.assertTrue(synced_on(loaded, _at(7).date()))
        self.assertNotIn("graph-id", persisted)
        self.assertEqual(fetch_event.subject, "课程")

    def test_rejects_cache_event_outside_declared_coverage(self) -> None:
        document = {
            "schemaVersion": "outlook-calendar-v1",
            "collectedAt": _at(7).isoformat(),
            "coverageStart": _at(1).isoformat(),
            "coverageEnd": _at(25).isoformat(),
            "events": [
                {
                    "id": "opaque",
                    "subject": "越界",
                    "startAt": _at(24).isoformat(),
                    "endAt": _at(26).isoformat(),
                    "showAs": "busy",
                    "isAllDay": False,
                }
            ],
        }
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "outlook-calendar.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaises(OutlookResponseError):
                read_calendar_cache(path, "Asia/Shanghai")


def _at(hour: int) -> datetime:
    day = 28 + hour // 24
    return datetime(2026, 8, day, hour % 24, tzinfo=ZONE)


def _calendar_event():
    from time_mirror.collectors.outlook_calendar import OutlookEvent

    return OutlookEvent("opaque", "课程", TimeRange(_at(9), _at(10)), "busy", False)

