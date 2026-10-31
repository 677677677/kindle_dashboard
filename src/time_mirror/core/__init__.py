"""时间区间、统计日与可支配时间计算。"""

from .time_model import (
    Availability,
    ReserveRule,
    TimeRange,
    calculate_availability,
    clip_range,
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

__all__ = [
    "Availability",
    "ReserveRule",
    "TimeRange",
    "calculate_availability",
    "clip_range",
    "is_stale",
    "merge_ranges",
    "pending_reserves",
    "reserve_window",
    "reserve_windows",
    "next_bedtime",
    "statistical_day_bounds",
    "statistical_date_for",
    "union_minutes",
]

