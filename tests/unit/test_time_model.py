from datetime import date, datetime, time, timedelta
from unittest import TestCase
from zoneinfo import ZoneInfo

from time_mirror.core import (
    TimeRange,
    ReserveRule,
    calculate_availability,
    is_stale,
    merge_ranges,
    pending_reserves,
    reserve_window,
    reserve_windows,
    next_bedtime,
    statistical_day_bounds,
    statistical_date_for,
    union_minutes,
)


ZONE = ZoneInfo("Asia/Shanghai")


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, day, hour, minute, tzinfo=ZONE)


class StatisticalDayTests(TestCase):
    def test_statistical_day_runs_from_midnight_to_midnight(self) -> None:
        window = statistical_day_bounds(date(2026, 8, 28))
        self.assertEqual(window.start, at(28, 0))
        self.assertEqual(window.end, at(29, 0))

    def test_reading_before_midnight_stays_in_same_statistical_day(self) -> None:
        window = statistical_day_bounds(date(2026, 8, 28))
        reading = TimeRange(at(28, 23, 30), at(29, 0, 30))
        self.assertEqual(union_minutes([reading], window), 30)

    def test_interval_across_one_is_split_between_days(self) -> None:
        reading = TimeRange(at(28, 23, 30), at(29, 0, 30))
        first = statistical_day_bounds(date(2026, 8, 28))
        second = statistical_day_bounds(date(2026, 8, 29))
        self.assertEqual(union_minutes([reading], first), 30)
        self.assertEqual(union_minutes([reading], second), 30)

    def test_statistical_date_changes_at_midnight(self) -> None:
        self.assertEqual(statistical_date_for(at(28, 0, 59)), date(2026, 8, 28))
        self.assertEqual(statistical_date_for(at(28, 1)), date(2026, 8, 28))
        self.assertEqual(statistical_date_for(at(28, 23, 59)), date(2026, 8, 28))
        self.assertEqual(statistical_date_for(at(29, 0)), date(2026, 8, 29))


class IntervalUnionTests(TestCase):
    def test_overlapping_meetings_are_counted_once(self) -> None:
        window = TimeRange(at(28, 9), at(28, 12))
        meetings = [
            TimeRange(at(28, 9), at(28, 10)),
            TimeRange(at(28, 9, 30), at(28, 10, 30)),
        ]
        self.assertEqual(union_minutes(meetings, window), 90)

    def test_touching_ranges_are_merged(self) -> None:
        items = [TimeRange(at(28, 9), at(28, 10)), TimeRange(at(28, 10), at(28, 11))]
        self.assertEqual(merge_ranges(items), (TimeRange(at(28, 9), at(28, 11)),))

    def test_phone_and_computer_overlap_is_not_double_counted(self) -> None:
        window = TimeRange(at(28, 14), at(28, 16))
        devices = [TimeRange(at(28, 14), at(28, 15)), TimeRange(at(28, 14, 30), at(28, 15, 30))]
        self.assertEqual(union_minutes(devices, window), 90)


class AvailabilityTests(TestCase):
    def test_reservations_are_unioned_before_subtraction(self) -> None:
        window = TimeRange(at(28, 14), at(28, 20))
        reserved = [TimeRange(at(28, 15), at(28, 16)), TimeRange(at(28, 15, 30), at(28, 17))]
        result = calculate_availability(window, reserved)
        self.assertEqual(result.total_minutes, 240)
        self.assertEqual(result.largest_block_minutes, 180)

    def test_unplaced_meals_and_commute_reduce_total_without_fake_position(self) -> None:
        window = TimeRange(at(28, 14), at(29, 1))
        result = calculate_availability(window, [], unplaced_reserve_minutes=210)
        self.assertEqual(result.scheduled_free_minutes, 660)
        self.assertEqual(result.total_minutes, 450)
        self.assertEqual(result.largest_scheduled_gap_minutes, 660)
        self.assertIsNone(result.largest_block_minutes)

    def test_reserve_cannot_make_remaining_time_negative(self) -> None:
        window = TimeRange(at(28, 23), at(29, 1))
        result = calculate_availability(window, [], unplaced_reserve_minutes=150)
        self.assertEqual(result.total_minutes, 0)
        self.assertIsNone(result.largest_block_minutes)

    def test_free_ranges_preserve_real_contiguous_blocks(self) -> None:
        window = TimeRange(at(28, 14), at(28, 20))
        reserved = [TimeRange(at(28, 15), at(28, 16)), TimeRange(at(28, 18), at(28, 18, 30))]
        result = calculate_availability(window, reserved)
        self.assertEqual([item.minutes for item in result.free_ranges], [60, 120, 90])
        self.assertEqual(result.largest_block_minutes, 120)

    def test_reserve_minutes_reduce_inside_the_expected_window(self) -> None:
        day = statistical_day_bounds(date(2026, 8, 28))
        rules = (
            ReserveRule("去程通勤", 30, time(10)),
            ReserveRule("午餐", 60, time(14)),
            ReserveRule("晚餐", 90, time(21)),
            ReserveRule("睡前准备", 90, time(0)),
        )

        self.assertEqual(
            [(item.label, item.minutes) for item in pending_reserves(at(28, 12, 59), day, rules)],
            [("午餐", 60), ("晚餐", 90), ("睡前准备", 90)],
        )
        self.assertEqual(
            [(item.label, item.minutes) for item in pending_reserves(at(28, 13, 30), day, rules)],
            [("午餐", 30), ("晚餐", 90), ("睡前准备", 90)],
        )
        self.assertEqual(
            [(item.label, item.minutes) for item in pending_reserves(at(28, 13, 59), day, rules)],
            [("午餐", 1), ("晚餐", 90), ("睡前准备", 90)],
        )

        result = pending_reserves(at(28, 22, 39), day, rules)
        self.assertEqual([(item.label, item.minutes) for item in result], [("睡前准备", 81)])

    def test_midnight_reserve_belongs_to_end_of_statistical_day(self) -> None:
        day = statistical_day_bounds(date(2026, 8, 28))
        rule = ReserveRule("睡前准备", 90, time(0))

        self.assertEqual(pending_reserves(at(28, 23, 30), day, (rule,))[0].minutes, 30)
        self.assertEqual(
            [(item.label, item.minutes) for item in pending_reserves(at(29, 0, 30), statistical_day_bounds(date(2026, 8, 29)), (rule,))],
            [("睡前准备", 90)],
        )
        self.assertEqual(next_bedtime(at(29, 0, 30)), at(30, 0))
        self.assertEqual(next_bedtime(at(28, 23, 30)), at(29, 0))

    def test_axis_reserve_uses_full_window_including_midnight(self) -> None:
        day = statistical_day_bounds(date(2026, 8, 28))
        lunch = ReserveRule("午餐", 60, time(14))
        self.assertEqual(reserve_window(lunch, day), TimeRange(at(28, 13), at(28, 14)))
        bedtime = ReserveRule("睡前准备", 90, time(1))
        self.assertEqual(reserve_windows(bedtime, day), (TimeRange(at(28, 0), at(28, 1)), TimeRange(at(28, 23, 30), at(29, 0))))
        self.assertIsNone(reserve_window(ReserveRule("空", 0, time(14)), day))
        early = ReserveRule("早间", 120, time(2))
        self.assertEqual(reserve_window(early, day), TimeRange(at(28, 0), at(28, 2)))


class FreshnessTests(TestCase):
    def test_exact_threshold_is_still_fresh(self) -> None:
        now = at(28, 14)
        self.assertFalse(is_stale(now - timedelta(minutes=30), now))

    def test_data_older_than_threshold_is_stale(self) -> None:
        now = at(28, 14)
        self.assertTrue(is_stale(now - timedelta(minutes=31), now))

