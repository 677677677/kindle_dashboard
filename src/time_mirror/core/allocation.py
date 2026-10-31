"""把设备事实和计划切成互斥区间，生成完整的一天时间账本。"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from .time_model import TimeRange, clip_range, merge_ranges


ALLOCATION_VERSION = "allocation-v6"
CATEGORIES = (
    ("sleep", "睡眠"), ("study", "学习"), ("entertainment", "娱乐"),
    ("reading", "阅读"), ("daily", "日常"), ("other", "其他"),
    ("future", "尚未度过"),
)


@dataclass(frozen=True, slots=True)
class ActivityFact:
    kind: str
    interval: TimeRange
    name: str | None = None
    device: str | None = None


def range_seconds(ranges: tuple[TimeRange, ...] | list[TimeRange], window: TimeRange) -> float:
    clipped = [item for value in ranges if (item := clip_range(value, window))]
    return sum((item.end - item.start).total_seconds() for item in merge_ranges(clipped))


def sleep_window(day: TimeRange, minutes: int = 0) -> TimeRange | None:
    if not 0 <= minutes <= 1440:
        raise ValueError("预计睡眠分钟必须在0至1440之间")
    if not minutes:
        return None
    start = datetime.combine(day.start.date(), time(0), day.start.tzinfo)
    return clip_range(TimeRange(start, start + timedelta(minutes=minutes)), day)


def calculate_allocation(
    day: TimeRange,
    now: datetime,
    facts: tuple[ActivityFact, ...] = (),
    fixed_ranges: tuple[TimeRange, ...] = (),
    daily_ranges: tuple[TimeRange, ...] = (),
    sleep_minutes: int = 0,
    sleep_ranges: tuple[TimeRange, ...] = (),
) -> dict:
    """过去每秒归一类，未来全部单列；默认不推测睡眠，日常只填补证据空白。"""
    if not day.start <= now <= day.end:
        raise ValueError("归类时刻必须位于统计日内")
    changes: dict[datetime, Counter] = defaultdict(Counter)
    changes[day.start]
    changes[now]
    changes[day.end]
    observed = TimeRange(day.start, now) if now > day.start else None
    if observed:
        _add_facts(changes, facts, observed)
        _add_ranges(changes, daily_ranges, "daily", observed)
        _add_ranges(changes, sleep_ranges, "measured_sleep", observed)
        sleep = estimated_sleep_window(day, sleep_minutes, sleep_ranges)
        _add_ranges(changes, (sleep,) if sleep else (), "sleep", observed)
    seconds, estimates, observed_seconds, unknown_seconds, entertainment, sleep_intervals = _sweep(changes, now)
    total = (day.end - day.start).total_seconds()
    if abs(sum(seconds.values()) - total) > 0.0001:
        raise ValueError("全天归类时长未闭合")
    future = TimeRange(now, day.end) if now < day.end else None
    sleep = estimated_sleep_window(day, sleep_minutes, sleep_ranges)
    occupied = fixed_ranges + ((sleep,) if sleep else ())
    pending = range_seconds(occupied, future) if future else 0
    fixed = range_seconds(fixed_ranges, day)
    categories = _category_documents(seconds, estimates, total)
    entertainment_minutes = next(item["minutes"] for item in categories if item["id"] == "entertainment")
    return {
        "version": ALLOCATION_VERSION,
        "totalSeconds": total,
        "fixedMinutes": round(fixed / 60, 2),
        "otherPlanMinutes": round((total - fixed) / 60, 2),
        "futureReservedMinutes": round(pending / 60, 2),
        "availableMinutes": max(0, round(((day.end - now).total_seconds() - pending) / 60)),
        "observedSeconds": observed_seconds,
        "unknownSeconds": unknown_seconds,
        "categories": categories,
        "entertainmentBreakdown": _entertainment_documents(entertainment, entertainment_minutes),
        "sleepIntervals": sleep_intervals,
    }


def _add_facts(changes: dict, facts: tuple[ActivityFact, ...], window: TimeRange) -> None:
    for fact in facts:
        clipped = clip_range(fact.interval, window)
        if clipped is None:
            continue
        kind = fact.kind
        if kind in {"reading", "phone_reading"} and (clipped.end - clipped.start).total_seconds() < 600:
            kind = "short_reading"
        _add_ranges(changes, (clipped,), kind, window)
        if fact.kind in {"phone", "phone_other", "computer", "game"} and fact.name:
            device = fact.device or ("phone" if fact.kind.startswith("phone") else "computer")
            _add_ranges(changes, (clipped,), ("app", fact.kind, device, fact.name), window)


def estimated_sleep_window(day: TimeRange, minutes: int, measured: tuple[TimeRange, ...]) -> TimeRange | None:
    """夜间实测替代夜间估算；只有白天小睡时保留夜间估算。"""
    planned = sleep_window(day, minutes)
    if planned and any(clip_range(item, planned) for item in measured):
        return None
    return planned


def _add_ranges(changes: dict, ranges: tuple[TimeRange, ...], kind: str | tuple, window: TimeRange) -> None:
    for value in ranges:
        if item := clip_range(value, window):
            changes[item.start][kind] += 1
            changes[item.end][kind] -= 1


def _sweep(changes: dict, now: datetime) -> tuple[Counter, Counter, float, float, Counter, list[dict]]:
    active: Counter = Counter()
    seconds: Counter = Counter()
    estimates: Counter = Counter()
    entertainment: Counter = Counter()
    sleep_intervals: list[dict] = []
    observed = unknown = 0.0
    ordered = sorted(changes)
    for start, end in zip(ordered, ordered[1:]):
        active.update(changes[start])
        duration = (end - start).total_seconds()
        category, evidence = ("future", "future") if start >= now else _classify(active)
        seconds[category] += duration
        if category == "entertainment":
            entertainment[_entertainment_owner(active)] += duration
        if category == "sleep":
            _append_sleep_interval(sleep_intervals, start, end, evidence == "estimated")
        if evidence == "estimated":
            estimates[category] += duration
        observed += duration if evidence == "observed" else 0
        unknown += duration if evidence == "unknown" else 0
    return seconds, estimates, observed, unknown, entertainment, sleep_intervals


def _append_sleep_interval(intervals: list[dict], start: datetime, end: datetime, estimated: bool) -> None:
    """仅合并最终睡眠分类中相邻且证据相同的区间，不跨设备活动补空。"""
    if intervals and intervals[-1]["endAt"] == start.isoformat() and intervals[-1]["estimated"] == estimated:
        intervals[-1]["endAt"] = end.isoformat()
    else:
        intervals.append({"startAt": start.isoformat(), "endAt": end.isoformat(), "estimated": estimated})


def _entertainment_owner(active: Counter) -> tuple[str, str]:
    """只为已归入娱乐的秒数选择单一App归属，不重复累加设备时间。"""
    phone = active["phone_other"] > 0 or (active["phone"] > 0 and active["computer"] > 0
                                         and active["phone_study"] <= 0
                                         and not (active["phone_reading"] > 0 and active["game"] > 0))
    device, kinds, fallback = ("phone", ("phone_other", "phone"), "手机") if phone else (
        "computer", ("game",), "电脑游戏")
    for kind in kinds:
        names = [key[3] for key, count in active.items() if count > 0 and isinstance(key, tuple)
                 and key[:3] == ("app", kind, device)]
        if names:
            return device, min(names)
    return device, fallback


def _entertainment_documents(seconds: Counter, target_minutes: int) -> list[dict]:
    ordered = sorted(seconds.items(), key=lambda item: (-item[1], item[0]))
    minutes = _rounded_parts([duration / 60 for _, duration in ordered], target_minutes)
    return [{"device": device, "name": name, "seconds": duration, "minutes": minutes[index]}
            for index, ((device, name), duration) in enumerate(ordered)]


def _classify(active: Counter) -> tuple[str, str]:
    rules = (
        ("study", "observed", active["phone_study"] > 0 and active["game"] <= 0 and active["phone_other"] <= 0),
        ("reading", "observed", active["phone_reading"] > 0 and active["game"] <= 0 and active["phone_other"] <= 0),
        ("entertainment", "observed", active["computer"] > 0 and active["phone"] > 0),
        ("entertainment", "observed", active["game"] > 0 or active["phone_other"] > 0),
        ("reading", "observed", active["reading"] > 0),
        ("study", "observed", active["computer"] > 0),
        ("sleep", "observed", active["measured_sleep"] > 0 and active["short_reading"] <= 0),
        ("study", "estimated", active["course"] > 0),
        ("other", "observed", active["short_reading"] > 0),
        ("sleep", "estimated", active["sleep"] > 0),
        ("daily", "estimated", active["daily"] > 0),
    )
    for category, evidence, applies in rules:
        if applies:
            return category, evidence
    return "other", "unknown"


def _category_documents(seconds: Counter, estimates: Counter, total: float) -> list[dict]:
    # 最大余数法仅处理展示尾差，圆弧与历史均使用原始秒数。
    tenths = [seconds[key] / total * 1000 for key, _ in CATEGORIES]
    rounded = _rounded_parts(tenths, 1000)
    minutes = _rounded_parts([seconds[key] / 60 for key, _ in CATEGORIES], round(total / 60))
    return [
        {"id": key, "label": label, "seconds": seconds[key],
         "minutes": minutes[index], "percent": rounded[index] / 10,
         "estimatedSeconds": estimates[key]}
        for index, (key, label) in enumerate(CATEGORIES)
    ]


def _rounded_parts(tenths: list[float], target: int) -> list[int]:
    rounded = [int(value) for value in tenths]
    order = sorted(range(len(tenths)), key=lambda i: (-(tenths[i] - rounded[i]), i))
    for index in order[:target - sum(rounded)]:
        rounded[index] += 1
    return rounded

