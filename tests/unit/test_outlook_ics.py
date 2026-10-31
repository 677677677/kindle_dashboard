from datetime import datetime
from unittest import TestCase
from zoneinfo import ZoneInfo

from time_mirror.core import TimeRange
from time_mirror.outlook_ics import read_ics


ZONE = ZoneInfo("Asia/Shanghai")


class OutlookIcsTests(TestCase):
    def test_expands_recurrence_and_discards_location(self) -> None:
        payload = b"""BEGIN:VCALENDAR\r
VERSION:2.0\r
BEGIN:VEVENT\r
UID:weekly-course\r
DTSTART;TZID=Asia/Shanghai:20261023T183000\r
DTEND;TZID=Asia/Shanghai:20261023T213000\r
RRULE:FREQ=WEEKLY;COUNT=2\r
SUMMARY:ECA5307 Sectional Teaching\r
LOCATION:LT9\r
X-MICROSOFT-CDO-BUSYSTATUS:BUSY\r
END:VEVENT\r
END:VCALENDAR\r
"""
        coverage = TimeRange(
            datetime(2026, 10, 23, 1, tzinfo=ZONE),
            datetime(2026, 10, 25, 1, tzinfo=ZONE),
        )

        calendar = read_ics(payload, coverage, "Asia/Shanghai")

        self.assertEqual(len(calendar.events), 1)
        self.assertEqual(calendar.events[0].subject, "ECA5307 Sectional Teaching")
        self.assertTrue(calendar.events[0].blocks_time)
        self.assertNotIn("LT9", repr(calendar.events[0]))

    def test_redacts_private_event_and_preserves_tentative_status(self) -> None:
        payload = b"""BEGIN:VCALENDAR\r
VERSION:2.0\r
BEGIN:VEVENT\r
UID:private-event\r
DTSTART:20261024T020000Z\r
DTEND:20261024T030000Z\r
SUMMARY:Private title\r
CLASS:PRIVATE\r
X-MICROSOFT-CDO-BUSYSTATUS:TENTATIVE\r
END:VEVENT\r
END:VCALENDAR\r
"""
        coverage = TimeRange(
            datetime(2026, 10, 23, 1, tzinfo=ZONE),
            datetime(2026, 10, 25, 1, tzinfo=ZONE),
        )

        event = read_ics(payload, coverage, "Asia/Shanghai").events[0]

        self.assertEqual(event.subject, "私人日程")
        self.assertEqual(event.show_as, "tentative")
        self.assertFalse(event.blocks_time)

