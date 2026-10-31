"""不依赖采集器的时间核算规则。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True, slots=True)
class TimeRange:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("时间区间必须包含时区")
        if self.end <= self.start:
            raise ValueError("结束时间必须晚于开始时间")

    @property
    def minutes(self) -> int:
        return round((self.end - self.start).total_seconds() / 60)


@dataclass(frozen=True, slots=True)
class Availability:
    total_minutes: int
    scheduled_free_minutes: int
    unplaced_reserve_minutes: int
    largest_scheduled_gap_minutes: int
    largest_block_minutes: int | None
    free_ranges: tuple[TimeRange, ...]


@dataclass(frozen=True, slots=True)
class ReserveRule:
    label: str
    minutes: int
    assumed_completed_by: time

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise ValueError("预留名称不能为空")
        if self.minutes < 0:
            raise ValueError("预留时间不能为负数")


def statistical_day_bounds(
    day: date,
    timezone: str = "Asia/Shanghai",
    starts_at: time = time(0, 0),
) -> TimeRange:
    zone = ZoneInfo(timezone)
    start = datetime.combine(day, starts_at, zone)
    return TimeRange(start, start + timedelta(days=1))


def statistical_date_for(now: datetime, starts_at: time = time(0, 0)) -> date:
    """默认按本地零点切分自然日；允许显式指定旧日界用于迁移。"""
    if now.tzinfo is None:
        raise ValueError("当前时间必须包含时区")
    boundary = datetime.combine(now.date(), starts_at, now.tzinfo)
    return now.date() if now >= boundary else now.date() - timedelta(days=1)


def clip_range(item: TimeRange, window: TimeRange) -> TimeRange | None:
    start = max(item.start, window.start)
    end = min(item.end, window.end)
    return TimeRange(start, end) if start < end else None


def merge_ranges(items: list[TimeRange] | tuple[TimeRange, ...]) -> tuple[TimeRange, ...]:
    ordered = sorted(items, key=lambda item: item.start)
    if not ordered:
        return ()

    merged = [ordered[0]]
    for item in ordered[1:]:
        previous = merged[-1]
        if item.start <= previous.end:
            merged[-1] = TimeRange(previous.start, max(previous.end, item.end))
        else:
            merged.append(item)
    return tuple(merged)


def union_minutes(items: list[TimeRange] | tuple[TimeRange, ...], window: TimeRange) -> int:
    clipped = tuple(item for value in items if (item := clip_range(value, window)))
    return sum(item.minutes for item in merge_ranges(clipped))


def _free_ranges(window: TimeRange, occupied: tuple[TimeRange, ...]) -> tuple[TimeRange, ...]:
    cursor = window.start
    free: list[TimeRange] = []
    for item in occupied:
        if cursor < item.start:
            free.append(TimeRange(cursor, item.start))
        cursor = max(cursor, item.end)
    if cursor < window.end:
        free.append(TimeRange(cursor, window.end))
    return tuple(free)


def calculate_availability(
    window: TimeRange,
    reserved: list[TimeRange] | tuple[TimeRange, ...],
    unplaced_reserve_minutes: int = 0,
) -> Availability:
    if unplaced_reserve_minutes < 0:
        raise ValueError("未定位预留时间不能为负数")

    clipped = tuple(item for value in reserved if (item := clip_range(value, window)))
    free = _free_ranges(window, merge_ranges(clipped))
    scheduled_free = sum(item.minutes for item in free)
    total = max(0, scheduled_free - unplaced_reserve_minutes)
    largest_scheduled_gap = max((item.minutes for item in free), default=0)
    largest_block = None if unplaced_reserve_minutes else largest_scheduled_gap
    return Availability(
        total,
        scheduled_free,
        unplaced_reserve_minutes,
        largest_scheduled_gap,
        largest_block,
        free,
    )


def pending_reserves(
    now: datetime,
    day: TimeRange,
    rules: list[ReserveRule] | tuple[ReserveRule, ...],
) -> tuple[ReserveRule, ...]:
    """返回从现在到入睡目标的预留；目标独立于统计日零点。"""
    if now.tzinfo is None:
        raise ValueError("当前时间必须包含时区")
    if not day.start <= now <= day.end:
        raise ValueError("当前时间必须位于统计日内")

    pending: list[ReserveRule] = []
    remaining = TimeRange(now, next_bedtime(now))
    for rule in rules:
        seconds = sum((item.end - item.start).total_seconds() for item in reserve_windows(rule, remaining))
        minutes = int((seconds + 59) // 60)
        if minutes:
            pending.append(ReserveRule(rule.label, minutes, rule.assumed_completed_by))
    return tuple(pending)


def _reserve_deadline(rule: ReserveRule, day: TimeRange) -> datetime:
    deadline = datetime.combine(day.start.date(), rule.assumed_completed_by, day.start.tzinfo)
    return deadline + timedelta(days=1) if rule.assumed_completed_by == time(0) else deadline


def next_bedtime(now: datetime) -> datetime:
    """可安排时间截止于下一本地零点，与默认入睡目标一致。"""
    if now.tzinfo is None:
        raise ValueError("当前时间必须包含时区")
    return datetime.combine(now.date() + timedelta(days=1), time(0), now.tzinfo)


def reserve_windows(rule: ReserveRule, window: TimeRange) -> tuple[TimeRange, ...]:
    """展开每日预留并裁剪窗口，包括零点两侧的睡前准备。"""
    if not rule.minutes:
        return ()
    result = []
    date = window.start.date()
    while date <= window.end.date() + timedelta(days=1):
        deadline = datetime.combine(date, rule.assumed_completed_by, window.start.tzinfo)
        item = clip_range(TimeRange(deadline - timedelta(minutes=rule.minutes), deadline), window)
        if item:
            result.append(item)
        date += timedelta(days=1)
    return merge_ranges(result)


def reserve_window(rule: ReserveRule, day: TimeRange) -> TimeRange | None:
    """用完整时长和预计截止时间构造展示窗口，不代表已发生事实。"""
    if not rule.minutes:
        return None
    deadline = _reserve_deadline(rule, day)
    return clip_range(TimeRange(deadline - timedelta(minutes=rule.minutes), deadline), day)


def _remaining_reserve_minutes(rule: ReserveRule, now: datetime, deadline: datetime) -> int:
    """按预计窗口裁剪预留，不把临近截止的整段时间重复扣除。"""
    if not rule.minutes or now >= deadline:
        return 0
    remaining_seconds = (deadline - now).total_seconds()
    return min(rule.minutes, int((remaining_seconds + 59) // 60))


def is_stale(data_until: datetime, now: datetime, threshold_minutes: int = 30) -> bool:
    if data_until.tzinfo is None or now.tzinfo is None:
        raise ValueError("更新时间和当前时间必须包含时区")
    if threshold_minutes < 0:
        raise ValueError("过期阈值不能为负数")
    return now - data_until > timedelta(minutes=threshold_minutes)

