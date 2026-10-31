from datetime import date, datetime, time, timedelta
from unittest import TestCase
from zoneinfo import ZoneInfo

from time_mirror.core.allocation import ActivityFact, calculate_allocation, sleep_window
from time_mirror.core import TimeRange, statistical_day_bounds


ZONE = ZoneInfo("Asia/Shanghai")


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, 26, hour, minute, tzinfo=ZONE)


def fact(kind: str, hour: int, end: int) -> ActivityFact:
    return ActivityFact(kind, TimeRange(at(hour), at(end)))


class AllocationTests(TestCase):
    day = statistical_day_bounds(date(2026, 10, 26), starts_at=time(0))

    def values(self, result):
        return {item["id"]: item for item in result["categories"]}

    def test_full_day_closes_and_sleep_never_hides_device_evidence(self):
        result = calculate_allocation(self.day, at(20), (fact("phone", 2, 3), fact("phone_other", 2, 3)),
                                      sleep_minutes=450)
        values = self.values(result)
        self.assertEqual(sum(item["seconds"] for item in values.values()), 86400)
        self.assertAlmostEqual(sum(item["percent"] for item in values.values()), 100)
        self.assertEqual(sum(item["minutes"] for item in values.values()), 1440)
        self.assertEqual(values["sleep"]["seconds"], 390 * 60)
        self.assertEqual(values["entertainment"]["seconds"], 3600)
        self.assertEqual(values["future"]["seconds"], 4 * 3600)
        self.assertEqual(result["observedSeconds"], 3600)

    def test_overlaps_choose_one_category_and_course_is_estimate(self):
        facts = (fact("computer", 10, 12), fact("phone", 11, 12),
                 fact("reading", 11, 12), fact("course", 10, 13))
        result = calculate_allocation(self.day, at(14), facts, sleep_minutes=0)
        values = self.values(result)
        self.assertEqual(values["study"]["seconds"], 7200)
        self.assertEqual(values["study"]["estimatedSeconds"], 3600)
        self.assertEqual(values["entertainment"]["seconds"], 3600)
        self.assertEqual(values["reading"]["seconds"], 0)

    def test_reading_qualifies_before_overlap_but_short_reading_does_not_become_entertainment(self):
        reading = TimeRange(at(10), at(10, 20))
        facts = (ActivityFact("reading", reading),
                 ActivityFact("phone_other", TimeRange(at(10, 5), at(10, 15))),
                 ActivityFact("reading", TimeRange(at(11), at(11) + timedelta(seconds=599.9))))
        values = self.values(calculate_allocation(self.day, at(12), facts, sleep_minutes=0))
        self.assertEqual(values["reading"]["seconds"], 600)
        self.assertEqual(values["entertainment"]["seconds"], 600)
        self.assertAlmostEqual(values["other"]["seconds"], 11 * 3600 + 40 * 60)

    def test_reading_threshold_is_seconds_after_day_and_now_clipping(self):
        facts = (ActivityFact("reading", TimeRange(self.day.start - timedelta(minutes=5),
                                                 self.day.start + timedelta(minutes=5))),
                 ActivityFact("reading", TimeRange(at(10), at(10, 10))),
                 ActivityFact("reading", TimeRange(at(11, 55), at(12, 20))))
        values = self.values(calculate_allocation(self.day, at(12), facts, sleep_minutes=0))
        self.assertEqual(values["reading"]["seconds"], 600)

    def test_fixed_calendar_and_daily_union_is_shared_with_future_remaining(self):
        fixed = (TimeRange(at(19), at(21)), TimeRange(at(20), at(22)))
        result = calculate_allocation(self.day, at(20), fixed_ranges=fixed,
                                      daily_ranges=fixed, sleep_minutes=0)
        self.assertEqual(result["fixedMinutes"], 180)
        self.assertEqual(result["futureReservedMinutes"], 120)
        self.assertEqual(result["availableMinutes"], 120)
        self.assertEqual(self.values(result)["daily"]["seconds"], 3600)

    def test_statistical_day_start_end_and_future_sleep(self):
        beginning = calculate_allocation(self.day, self.day.start)
        self.assertEqual(self.values(beginning)["future"]["seconds"], 86400)
        self.assertEqual(beginning["availableMinutes"], 1440)
        ending = calculate_allocation(self.day, self.day.end)
        self.assertEqual(self.values(ending)["future"]["seconds"], 0)
        self.assertEqual(ending["availableMinutes"], 0)

    def test_measured_night_replaces_estimate_and_devices_still_win(self):
        measured = (TimeRange(at(2), at(9)), TimeRange(at(2), at(9)))
        facts = (fact("computer", 3, 4), fact("course", 8, 10))
        result = calculate_allocation(self.day, at(12), facts, sleep_minutes=450, sleep_ranges=measured)
        values = self.values(result)
        self.assertEqual(values["sleep"]["seconds"], 6 * 3600)
        self.assertEqual(values["sleep"]["estimatedSeconds"], 0)
        self.assertEqual(sum(item["seconds"] for item in values.values()), 86400)

    def test_nap_does_not_remove_night_estimate_and_future_is_not_sleep(self):
        nap = (TimeRange(at(13), at(14)),)
        values = self.values(calculate_allocation(self.day, at(13, 30), sleep_minutes=450, sleep_ranges=nap))
        self.assertEqual(values["sleep"]["seconds"], 8 * 3600)
        self.assertEqual(values["sleep"]["estimatedSeconds"], 450 * 60)

    def test_measured_wakeup_releases_remaining_estimated_sleep(self):
        result = calculate_allocation(self.day, at(7), sleep_minutes=450,
                                      sleep_ranges=(TimeRange(at(2), at(6)),))
        self.assertEqual(result['futureReservedMinutes'], 0)
        self.assertEqual(result['availableMinutes'], 17 * 60)

    def test_display_minutes_stay_closed_when_several_parts_would_round_up(self):
        now = self.day.start + timedelta(minutes=31, seconds=40)
        facts = (ActivityFact("computer", TimeRange(now-timedelta(minutes=6, seconds=40), now)),)
        result = calculate_allocation(self.day, now, facts)
        self.assertEqual(sum(item["minutes"] for item in result["categories"]), 1440)
        self.assertEqual(sum(item["seconds"] for item in result["categories"]), 86400)

    def test_midnight_starts_sleep_estimate_and_does_not_count_future_sleep(self):
        result = calculate_allocation(self.day, at(0, 30), sleep_minutes=450)
        values = self.values(result)
        self.assertEqual(values["sleep"]["seconds"], 1800)
        self.assertEqual(values["sleep"]["estimatedSeconds"], 1800)
        self.assertEqual(values["other"]["seconds"], 0)
        self.assertEqual(values["future"]["seconds"], 23.5 * 3600)

    def test_default_does_not_invent_sleep_when_sensor_has_no_records(self):
        self.assertIsNone(sleep_window(self.day))
        result = calculate_allocation(self.day, at(12))
        self.assertEqual(result['sleepIntervals'], [])
        self.assertEqual(self.values(result)['sleep']['seconds'], 0)
        self.assertEqual(self.values(result)['sleep']['estimatedSeconds'], 0)
        self.assertEqual(self.values(result)['other']['seconds'], 12 * 3600)
        self.assertEqual(self.values(result)['future']['seconds'], 12 * 3600)

    def test_entertainment_breakdown_attributes_overlap_to_phone(self):
        facts = (
            ActivityFact("computer", TimeRange(at(10), at(12)), "study.exe", "computer"),
            ActivityFact("game", TimeRange(at(10), at(11)), "game.exe", "computer"),
            ActivityFact("phone", TimeRange(at(10, 30), at(11, 30)), "微信读书", "phone"),
            ActivityFact("reading", TimeRange(at(10, 30), at(11, 30))),
            ActivityFact("phone", TimeRange(at(11, 30), at(12)), "rednote", "phone"),
            ActivityFact("phone_other", TimeRange(at(11, 30), at(12)), "rednote", "phone"),
        )
        result = calculate_allocation(self.day, at(13), facts, sleep_minutes=0)
        parts = {(item['device'], item['name']): item for item in result['entertainmentBreakdown']}
        self.assertEqual(parts[('computer', 'game.exe')]['seconds'], 1800)
        self.assertEqual(parts[('phone', '微信读书')]['seconds'], 3600)
        self.assertEqual(parts[('phone', 'rednote')]['seconds'], 1800)
        self.assertEqual(sum(item['seconds'] for item in parts.values()),
                         self.values(result)['entertainment']['seconds'])

    def test_entertainment_breakdown_excludes_study_and_reading_only(self):
        facts = (ActivityFact("computer", TimeRange(at(10), at(11)), "editor.exe"),
                 ActivityFact("reading", TimeRange(at(11), at(12)), "kindle"))
        result = calculate_allocation(self.day, at(13), facts, sleep_minutes=0)
        self.assertEqual(result['entertainmentBreakdown'], [])

    def test_entertainment_breakdown_keeps_short_sessions_and_rounds_closed(self):
        start = at(10)
        facts = tuple(ActivityFact("phone_other", TimeRange(start + timedelta(seconds=40 * index),
                                                            start + timedelta(seconds=40 * (index + 1))),
                                   name, "phone") for index, name in enumerate(('A', 'B', 'C')))
        result = calculate_allocation(self.day, at(11), facts, sleep_minutes=0)
        parts = result['entertainmentBreakdown']
        self.assertEqual(len(parts), 3)
        self.assertEqual(sum(item['seconds'] for item in parts), 120)
        self.assertEqual(sum(item['minutes'] for item in parts),
                         self.values(result)['entertainment']['minutes'])

    def test_entertainment_breakdown_is_stable_for_duplicate_and_parallel_phone_records(self):
        first = ActivityFact("phone_other", TimeRange(at(10), at(11)), "B", "phone")
        second = ActivityFact("phone_other", TimeRange(at(10), at(11)), "A", "phone")
        forward = calculate_allocation(self.day, at(12), (first, second, first))
        backward = calculate_allocation(self.day, at(12), (second, first))
        self.assertEqual(forward['entertainmentBreakdown'], backward['entertainmentBreakdown'])
        self.assertEqual(forward['entertainmentBreakdown'],
                         [{'device': 'phone', 'name': 'A', 'seconds': 3600, 'minutes': 60}])

    def test_entertainment_breakdown_has_label_fallback_and_excludes_future(self):
        result = calculate_allocation(self.day, at(11), (fact('game', 10, 12),))
        self.assertEqual(result['entertainmentBreakdown'],
                         [{'device': 'computer', 'name': '电脑游戏', 'seconds': 3600, 'minutes': 60}])

    def test_sleep_intervals_are_final_classification_not_uninterrupted_plan(self):
        facts = (ActivityFact('phone_other', TimeRange(at(3), at(4)), 'rednote', 'phone'),)
        result = calculate_allocation(self.day, at(12), facts, sleep_minutes=450)
        self.assertEqual(result['sleepIntervals'], [
            {'startAt': at(0).isoformat(), 'endAt': at(3).isoformat(), 'estimated': True},
            {'startAt': at(4).isoformat(), 'endAt': at(7, 30).isoformat(), 'estimated': True},
        ])
        seconds = sum((datetime.fromisoformat(item['endAt']) - datetime.fromisoformat(item['startAt'])).total_seconds()
                      for item in result['sleepIntervals'])
        self.assertEqual(seconds, self.values(result)['sleep']['seconds'])

    def test_measured_sleep_intervals_merge_only_adjacent_matching_evidence(self):
        measured = (TimeRange(at(2), at(9)), TimeRange(at(2), at(9)))
        facts = (ActivityFact('computer', TimeRange(at(4), at(5)), 'editor.exe'),
                 ActivityFact('course', TimeRange(at(7), at(8))))
        result = calculate_allocation(self.day, at(12), facts, sleep_ranges=measured)
        self.assertEqual(result['sleepIntervals'], [
            {'startAt': at(2).isoformat(), 'endAt': at(4).isoformat(), 'estimated': False},
            {'startAt': at(5).isoformat(), 'endAt': at(9).isoformat(), 'estimated': False},
        ])
        self.assertEqual(self.values(result)['sleep']['seconds'], 6 * 3600)

    def test_phone_study_is_study_alone_and_with_normal_computer(self):
        facts = (ActivityFact('phone', TimeRange(at(9), at(11)), 'LinkedIn', 'phone'),
                 ActivityFact('phone_study', TimeRange(at(9), at(11)), 'LinkedIn', 'phone'),
                 ActivityFact('computer', TimeRange(at(10), at(11)), 'editor.exe', 'computer'),
                 ActivityFact('phone', TimeRange(at(11), at(12)), 'rednote', 'phone'),
                 ActivityFact('phone_other', TimeRange(at(11), at(12)), 'rednote', 'phone'))
        result = calculate_allocation(self.day, at(13), facts, sleep_minutes=0)
        self.assertEqual(self.values(result)['study']['seconds'], 2 * 3600)
        self.assertEqual(self.values(result)['entertainment']['seconds'], 3600)
        self.assertEqual(result['entertainmentBreakdown'],
                         [{'device': 'phone', 'name': 'rednote', 'seconds': 3600, 'minutes': 60}])

    def test_phone_study_does_not_relabel_actual_game_as_learning_or_linkedin_entertainment(self):
        facts = (ActivityFact('phone', TimeRange(at(10), at(11)), 'LinkedIn', 'phone'),
                 ActivityFact('phone_study', TimeRange(at(10), at(11)), 'LinkedIn', 'phone'),
                 ActivityFact('computer', TimeRange(at(10), at(11)), 'game.exe', 'computer'),
                 ActivityFact('game', TimeRange(at(10), at(11)), 'game.exe', 'computer'))
        result = calculate_allocation(self.day, at(12), facts, sleep_minutes=0)
        self.assertEqual(self.values(result)['study']['seconds'], 0)
        self.assertEqual(result['entertainmentBreakdown'],
                         [{'device': 'computer', 'name': 'game.exe', 'seconds': 3600, 'minutes': 60}])

