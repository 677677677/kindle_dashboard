"""把真实采集结果转换成前端唯一消费的 snapshot-v1。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, TYPE_CHECKING
import re
from collections import defaultdict

from time_mirror.collectors.activitywatch import AppSummary
from time_mirror.collectors.activitywatch_android import PhoneUsage
from time_mirror.collectors.koreader import BookSummary, KoreaderDatabase, ReadingEvent
from time_mirror.collectors.outlook_calendar import OutlookEvent
from time_mirror.core import (
    ReserveRule,
    TimeRange,
    calculate_availability,
    merge_ranges,
    reserve_window,
    reserve_windows,
    next_bedtime,
    statistical_date_for,
    statistical_day_bounds,
    union_minutes,
)
from time_mirror.core.allocation import ActivityFact, calculate_allocation, estimated_sleep_window, range_seconds

if TYPE_CHECKING:
    from time_mirror.collectors.activitywatch import AppInterval


Snapshot = dict[str, Any]
MINIMUM_AXIS_SEGMENT_MINUTES = 10


def build_snapshot(
    now: datetime,
    koreader_path: Path | None,
    koreader_status: str,
    koreader_collected_at: datetime | None,
    unplaced_reserve_minutes: int,
    *,
    reserve_breakdown: tuple[ReserveRule, ...] = (),
    reserve_plan: tuple[ReserveRule, ...] = (),
    computer_status: str = "not_connected",
    computer_collected_at: datetime | None = None,
    computer_apps: tuple[AppSummary, ...] = (),
    computer_segments: tuple[AppInterval, ...] = (),
    phone_status: str = "not_connected",
    phone_collected_at: datetime | None = None,
    phone_error_code: str | None = None,
    phone_usage: PhoneUsage | None = None,
    calendar_status: str = "not_connected",
    calendar_collected_at: datetime | None = None,
    calendar_events: tuple[OutlookEvent, ...] = (),
    calendar_error_code: str | None = None,
    sleep_minutes: int = 0,
    game_apps: tuple[str, ...] = ("tlou-ii.exe",),
    course_keywords: tuple[str, ...] = ("课程", "上课", "lecture", "tutorial", "sectional teaching"),
    sleep_ranges: tuple[TimeRange, ...] = (),
    health_status: str = "not_connected",
    health_collected_at: datetime | None = None,
    health_latest_event_at: datetime | None = None,
    health_error_code: str | None = None,
    sleep_sessions: tuple[TimeRange, ...] = (),
) -> Snapshot:
    statistical_date = statistical_date_for(now)
    day = statistical_day_bounds(statistical_date, str(now.tzinfo))
    computer_segments = computer_segments if computer_status == "ok" else ()
    phone_usage = phone_usage if phone_status in {"ok", "cached"} else None
    calendar_events = calendar_events if calendar_status in {"ok", "cached"} else ()
    elapsed = TimeRange(day.start, now) if now > day.start else None
    future = TimeRange(now, next_bedtime(now))
    events, books, latest = _read_koreader(koreader_path, day, now)
    reading_ranges = merge_ranges([event.interval for event in events])
    reading_minutes = union_minutes(list(reading_ranges), elapsed) if elapsed else 0
    elapsed_minutes = elapsed.minutes if elapsed else 0
    blocking_calendar = tuple(event.interval for event in calendar_events if event.blocks_time)
    availability = calculate_availability(future, blocking_calendar, unplaced_reserve_minutes)
    allocation = allocation_for_day(day, now, computer_segments, phone_usage, events,
                                    calendar_events, reserve_plan, sleep_minutes, game_apps, course_keywords, sleep_ranges)
    if reserve_plan:
        daily = tuple(window for rule in reserve_plan for window in reserve_windows(rule, future))
        sleep = estimated_sleep_window(day, sleep_minutes, sleep_ranges)
        occupied = blocking_calendar + daily + ((sleep,) if sleep else ())
        availability = calculate_availability(future, occupied)
    allocation["availableMinutes"] = availability.total_minutes
    allocation["futureReservedMinutes"] = round((future.end - now).total_seconds() / 60 - availability.total_minutes, 2)
    calendar_minutes = union_minutes(blocking_calendar, future)
    current, next_event = _calendar_navigation(now, calendar_events)

    sources = {
        "koreader": {
            "status": koreader_status,
            "collectedAt": _iso(koreader_collected_at),
            "latestEventAt": _iso(latest.interval.end if latest else None),
            "errorCode": None if koreader_status == "ok" else _source_error(koreader_status),
        },
        "computer": {
            "status": computer_status,
            "collectedAt": _iso(computer_collected_at),
            "errorCode": None if computer_status == "ok" else _source_error(computer_status),
        },
        "phone": {
            "status": phone_status,
            "collectedAt": _iso(phone_collected_at),
            "latestEventAt": _iso(phone_usage.latest_event_at if phone_usage else None),
            "errorCode": phone_error_code or _phone_source_error(phone_status),
        },
        "calendar": {
            "status": calendar_status,
            "collectedAt": _iso(calendar_collected_at),
            "errorCode": calendar_error_code,
        },
        "health": {"status": health_status, "collectedAt": _iso(health_collected_at),
                   "latestEventAt": _iso(health_latest_event_at), "errorCode": health_error_code,
                   "sleepEstimated": estimated_sleep_window(day, sleep_minutes, sleep_ranges) is not None},
    }

    return {
        "schemaVersion": "snapshot-v1",
        "timezone": str(now.tzinfo),
        "statisticalDate": statistical_date.isoformat(),
        "dayStart": day.start.isoformat(),
        "dayEnd": day.end.isoformat(),
        "generatedAt": now.isoformat(),
        "overallStatus": "partial",
        "allocation": allocation,
        "sources": sources,
        "current": current,
        "next": next_event,
        "remaining": {
            "untilAt": future.end.isoformat(),
            "countdownMinutes": round((future.end - now).total_seconds() / 60),
            "status": "estimate",
            "totalMinutes": availability.total_minutes,
            "scheduledFreeMinutes": availability.scheduled_free_minutes,
            "unplacedReserveMinutes": availability.unplaced_reserve_minutes,
            "reserveBreakdown": [
                {"label": item.label, "minutes": item.minutes} for item in reserve_breakdown
            ],
            "largestScheduledGapMinutes": availability.largest_scheduled_gap_minutes,
            "largestBlockMinutes": availability.largest_block_minutes,
            "note": _remaining_note(reserve_breakdown, unplaced_reserve_minutes, calendar_minutes),
        },
        "coverage": {
            "elapsedMinutes": elapsed_minutes,
            "classifiedMinutes": reading_minutes,
            "accountedPercent": round(reading_minutes * 100 / elapsed_minutes) if elapsed_minutes else 0,
        },
        "timeline": _build_timeline(day, now, reading_ranges, blocking_calendar),
        "dayAxis": _build_day_axis(day, now, reserve_plan, phone_status, phone_usage, computer_segments),
        "sleepSummary": _sleep_summary(sleep_sessions),
        "highlights": _build_highlights(events, calendar_events, now),
        "calendarEvents": [_calendar_event_document(event) for event in calendar_events],
        "devices": [
            _kindle_device(koreader_status, events, books, latest, elapsed),
            _computer_device(computer_status, computer_apps),
            _phone_device(phone_status, phone_usage),
        ],
        "prompt": {
            "kind": "source_status",
            "text": _prompt_text(computer_status, phone_status, calendar_status),
        },
    }


def validate_snapshot(snapshot: Snapshot) -> None:
    required = {
        "schemaVersion",
        "timezone",
        "statisticalDate",
        "dayStart",
        "dayEnd",
        "generatedAt",
        "sources",
        "remaining",
        "coverage",
        "timeline",
        "calendarEvents",
        "devices",
    }
    missing = required - snapshot.keys()
    if missing:
        raise ValueError(f"snapshot-v1 缺少字段：{', '.join(sorted(missing))}")
    if snapshot["schemaVersion"] != "snapshot-v1":
        raise ValueError("不支持的快照版本")
    for field in ("dayStart", "dayEnd", "generatedAt"):
        if datetime.fromisoformat(snapshot[field]).tzinfo is None:
            raise ValueError(f"{field} 必须包含时区")
    if not isinstance(snapshot["timeline"], list) or not isinstance(snapshot["devices"], list):
        raise ValueError("timeline 和 devices 必须为数组")
    if allocation := snapshot.get("allocation"):
        total = sum(item["seconds"] for item in allocation["categories"])
        if abs(total - allocation["totalSeconds"]) > 0.0001:
            raise ValueError("饼图时长未闭合")


def allocation_for_day(
    day: TimeRange, now: datetime, computer: tuple[AppInterval, ...],
    phone: PhoneUsage | None, reading: tuple[ReadingEvent, ...],
    calendar: tuple[OutlookEvent, ...], rules: tuple[ReserveRule, ...],
    sleep_minutes: int = 0, game_apps: tuple[str, ...] = ("tlou-ii.exe",),
    course_keywords: tuple[str, ...] = ("课程", "上课", "lecture", "tutorial", "sectional teaching"),
    sleep_ranges: tuple[TimeRange, ...] = (),
) -> dict[str, Any]:
    facts = _device_facts(computer, phone, reading, game_apps)
    facts += tuple(ActivityFact("course", event.interval) for event in calendar
                   if event.blocks_time and _is_course(event.subject, course_keywords))
    daily = tuple(window for rule in rules for window in reserve_windows(rule, day))
    fixed = daily + tuple(event.interval for event in calendar if event.blocks_time)
    return calculate_allocation(day, now, facts, fixed, daily, sleep_minutes, sleep_ranges)


def _device_facts(
    computer: tuple[AppInterval, ...], phone: PhoneUsage | None,
    reading: tuple[ReadingEvent, ...], game_apps: tuple[str, ...],
) -> tuple[ActivityFact, ...]:
    games = {name.casefold() for name in game_apps}
    facts = [ActivityFact("computer", item.interval, item.name, "computer") for item in computer]
    facts.extend(ActivityFact("game", item.interval, item.name, "computer") for item in computer if item.name.casefold() in games)
    for item in (phone.activity_segments if phone else ()):
        facts.append(ActivityFact("phone", item.interval, item.name, "phone"))
        kind = "phone_study" if item.name.strip().casefold() in {"linkedin", "领英", "chatgpt"} else (
            "phone_reading" if item.reading else "phone_other")
        facts.append(ActivityFact(kind, item.interval, item.name, "phone"))
    books: dict[int, list[TimeRange]] = defaultdict(list)
    for item in reading:
        books[item.book_id].append(item.interval)
    facts.extend(ActivityFact("reading", interval) for ranges in books.values()
                 for interval in merge_ranges(ranges))
    return tuple(facts)


def _is_course(subject: str, keywords: tuple[str, ...]) -> bool:
    return bool(re.search(r"\b[A-Z]{2,5}\d{4}\b", subject, re.IGNORECASE)) or any(
        keyword.casefold() in subject.casefold() for keyword in keywords if keyword
    )


def _sleep_summary(sessions: tuple[TimeRange, ...]) -> dict | None:
    """仅展示手环完整寝起记录；不拿估算或活动分类后的碎片替代。"""
    if not sessions:
        return None
    main = max(sessions, key=lambda item: ((item.end - item.start).total_seconds(), item.end))
    return {"minutes": main.minutes, "estimated": False,
            "startAt": main.start.isoformat(), "endAt": main.end.isoformat(),
            "intervals": [{**_axis_interval(main), "estimated": False}]}


def _read_koreader(
    path: Path | None,
    day: TimeRange,
    now: datetime,
) -> tuple[tuple[ReadingEvent, ...], tuple[BookSummary, ...], ReadingEvent | None]:
    if path is None or not path.exists():
        return (), (), None
    database = KoreaderDatabase(path, str(now.tzinfo))
    latest = database.latest_event(now)
    if latest is not None and latest.interval.end > now:
        latest = _clip_event(latest, TimeRange(latest.interval.start, now))
    if now <= day.start:
        return (), (), latest

    observed = TimeRange(day.start, min(now, day.end))
    events = tuple(_clip_event(event, observed) for event in database.reading_events(observed))
    return events, database.summarize(observed), latest


def _clip_event(event: ReadingEvent, window: TimeRange) -> ReadingEvent:
    return ReadingEvent(
        event.book_id,
        event.title,
        event.authors,
        TimeRange(max(event.interval.start, window.start), min(event.interval.end, window.end)),
    )


def _build_timeline(
    day: TimeRange,
    now: datetime,
    reading_ranges: tuple[TimeRange, ...],
    calendar_ranges: tuple[TimeRange, ...],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    cursor = day.start
    for index, item in enumerate(reading_ranges):
        start = max(item.start, day.start)
        end = min(item.end, now)
        if cursor < start:
            items.append(_timeline_item(f"unknown-{index}", cursor, start, "尚未识别", "unknown"))
        if start < end:
            items.append(_timeline_item(f"reading-{index}", start, end, "Kindle 阅读", "observed"))
        cursor = max(cursor, end)
    if cursor < now:
        items.append(_timeline_item("unknown-now", cursor, now, "尚未识别", "unknown"))
    future = TimeRange(now, day.end) if now < day.end else None
    cursor = now
    planned = merge_ranges(
        [clipped for value in calendar_ranges if future and (clipped := _clip(value, future))]
    )
    for index, item in enumerate(planned):
        if cursor < item.start:
            items.append(_timeline_item(f"open-{index}", cursor, item.start, "尚待安排", "open"))
        items.append(_timeline_item(f"calendar-{index}", item.start, item.end, "日历安排", "planned"))
        cursor = item.end
    if cursor < day.end:
        items.append(_timeline_item("open", cursor, day.end, "尚待安排", "open"))
    return items


def _clip(item: TimeRange, window: TimeRange) -> TimeRange | None:
    start = max(item.start, window.start)
    end = min(item.end, window.end)
    return TimeRange(start, end) if start < end else None


def _timeline_item(item_id: str, start: datetime, end: datetime, label: str, state: str) -> dict[str, Any]:
    return {
        "id": item_id,
        "startAt": start.isoformat(),
        "endAt": end.isoformat(),
        "minutes": round((end - start).total_seconds() / 60),
        "label": label,
        "state": state,
    }


def _build_day_axis(
    day: TimeRange,
    now: datetime,
    rules: tuple[ReserveRule, ...],
    phone_status: str,
    usage: PhoneUsage | None,
    computer: tuple[AppInterval, ...] = (),
) -> dict[str, Any]:
    windows = [window for rule in rules for window in reserve_windows(rule, day)]
    future = TimeRange(max(day.start, now), day.end) if now < day.end else None
    reserves = [_axis_interval(clipped) for window in merge_ranges(windows)
                if future and (clipped := _clip(window, future))]
    phone = _phone_axis_segments(day, now, phone_status, usage)
    return {"minimumPhoneMinutes": MINIMUM_AXIS_SEGMENT_MINUTES, "reserves": reserves, "phone": phone,
            "appSegments": _app_axis_segments(day, now, usage, computer)}


def _app_axis_segments(day: TimeRange, now: datetime, usage: PhoneUsage | None,
                       computer: tuple[AppInterval, ...]) -> list[dict]:
    if now <= day.start:
        return []
    observed = TimeRange(day.start, min(now, day.end))
    grouped: dict[str, list[tuple[str, str, TimeRange]]] = defaultdict(list)
    for segment in computer:
        grouped[segment.name.casefold().removesuffix('.exe')].append(("computer", segment.name, segment.interval))
    for segment in usage.activity_segments if usage else ():
        grouped[segment.name.casefold()].append(("phone", segment.name, segment.interval))
    ranked = sorted(grouped, key=lambda key: (-range_seconds(tuple(row[2] for row in grouped[key]), observed), key))[:5]
    candidates = []
    labels = {}
    for rank, key in enumerate(ranked):
        rows = grouped[key]
        label = next((name for device, name, _ in rows if device == 'phone'), rows[0][1])
        devices = {row[0] for row in rows}
        labels[key] = (label, 'combined' if len(devices) > 1 else rows[0][0], rank)
        for device in devices:
            for interval in merge_ranges([row[2] for row in rows if row[0] == device]):
                clipped = _clip(interval, observed)
                if clipped and (clipped.end - clipped.start).total_seconds() >= 600:
                    candidates.append((device, key, clipped))
    return _single_axis_intervals(candidates, labels)


def _single_axis_intervals(candidates: list, labels: dict) -> list[dict]:
    """显示重叠时优先手机；切分仅影响横轴，不改App累计或饼图分类。"""
    boundaries = sorted({at for _, _, item in candidates for at in (item.start, item.end)})
    visible = []
    for start, end in zip(boundaries, boundaries[1:]):
        active = [(device, key) for device, key, item in candidates if item.start <= start < item.end]
        if not active:
            continue
        _, key = min(active, key=lambda item: (item[0] != 'phone', labels[item[1]][2]))
        if visible and visible[-1][0] == key and visible[-1][2] == start:
            visible[-1] = (key, visible[-1][1], end)
        else:
            visible.append((key, start, end))
    result = []
    for key, start, end in visible:
        if (end - start).total_seconds() < 600:
            continue
        app, device, rank = labels[key]
        result.append({'app': app, 'device': device, 'rank': rank,
                       'startAt': start.isoformat(), 'endAt': end.isoformat(),
                       'minutes': round((end - start).total_seconds() / 60)})
    return sorted(result, key=lambda item: (item['rank'], item['startAt']))


def _phone_axis_segments(
    day: TimeRange, now: datetime, status: str, usage: PhoneUsage | None,
) -> list[dict[str, Any]]:
    if status not in {"ok", "cached"} or usage is None or now <= day.start:
        return []
    observed = TimeRange(day.start, min(now, day.end))
    segments = []
    for segment in usage.segments:
        clipped = _clip(segment.interval, observed)
        if segment.name not in {"rednote", "bilibili"} or clipped is None:
            continue
        if (clipped.end - clipped.start).total_seconds() >= MINIMUM_AXIS_SEGMENT_MINUTES * 60:
            segments.append({"app": segment.name, **_axis_interval(clipped)})
    return sorted(segments, key=lambda item: item["startAt"])


def _axis_interval(interval: TimeRange) -> dict[str, Any]:
    return {"startAt": interval.start.isoformat(), "endAt": interval.end.isoformat()}


def _event_to_highlight(event: ReadingEvent) -> dict[str, Any]:
    return {
        "startAt": event.interval.start.isoformat(),
        "endAt": event.interval.end.isoformat(),
        "label": "Kindle 阅读",
        "detail": event.title,
        "source": "koreader",
    }


def _calendar_navigation(
    now: datetime,
    events: tuple[OutlookEvent, ...],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    active = tuple(event for event in events if event.interval.start <= now < event.interval.end)
    upcoming = tuple(event for event in events if event.interval.start > now)
    current = None
    if active:
        label = active[0].subject if len(active) == 1 else f"{len(active)} 项日程重叠"
        current = {
            "label": label,
            "startedAt": min(event.interval.start for event in active).isoformat(),
            "detail": "日历计划 · 尚未确认实际活动",
        }
    next_event = None
    if upcoming:
        event = min(upcoming, key=lambda item: item.interval.start)
        next_event = {"label": event.subject, "startAt": event.interval.start.isoformat()}
    return current, next_event


def _build_highlights(
    reading: tuple[ReadingEvent, ...],
    calendar: tuple[OutlookEvent, ...],
    now: datetime,
) -> list[dict[str, Any]]:
    values = [_event_to_highlight(event) for event in reading[-2:]]
    values.extend(_calendar_event_to_highlight(event) for event in calendar)
    future = sorted((item for item in values if datetime.fromisoformat(item["endAt"]) > now), key=lambda item: item["startAt"])
    past = sorted((item for item in values if datetime.fromisoformat(item["endAt"]) <= now), key=lambda item: item["startAt"], reverse=True)
    selected = (future[:3] + past[: max(0, 4 - len(future[:3]))])[:4]
    return sorted(selected, key=lambda item: item["startAt"])


def _calendar_event_to_highlight(event: OutlookEvent) -> dict[str, Any]:
    return {
        "startAt": event.interval.start.isoformat(),
        "endAt": event.interval.end.isoformat(),
        "label": "日历安排",
        "detail": event.subject,
        "source": "outlook",
    }


def _calendar_event_document(event: OutlookEvent) -> dict[str, Any]:
    return {
        "id": event.event_id,
        "subject": event.subject,
        "startAt": event.interval.start.isoformat(),
        "endAt": event.interval.end.isoformat(),
        "showAs": event.show_as,
        "isAllDay": event.is_all_day,
        "blocksTime": event.blocks_time,
    }


def _kindle_device(
    status: str,
    events: tuple[ReadingEvent, ...],
    books: tuple[BookSummary, ...],
    latest: ReadingEvent | None,
    elapsed: TimeRange | None,
) -> dict[str, Any]:
    return {
        "id": "kindle",
        "name": "Kindle",
        "status": status,
        "totalMinutes": union_minutes([event.interval for event in events], elapsed) if elapsed else 0,
        "books": [
            {
                "title": book.title,
                "authors": book.authors,
                "minutes": max(1, round(book.seconds / 60)),
                "lastReadAt": book.last_read_at.isoformat(),
            }
            for book in books
        ],
        "recent": None
        if latest is None
        else {
            "title": latest.title,
            "authors": latest.authors,
            "lastReadAt": latest.interval.end.isoformat(),
        },
    }


def _computer_device(status: str, apps: tuple[AppSummary, ...]) -> dict[str, Any]:
    if status != "ok":
        return {"id": "computer", "name": "电脑", "status": status, "totalMinutes": None, "apps": []}
    total_seconds = sum(app.seconds for app in apps)
    return {
        "id": "computer",
        "name": "电脑",
        "status": status,
        "totalMinutes": max(1, round(total_seconds / 60)) if total_seconds else 0,
        "apps": [
            {"name": app.name, "minutes": max(1, round(app.seconds / 60))}
            for app in apps[:5]
        ],
    }


def _phone_device(status: str, usage: PhoneUsage | None) -> dict[str, Any]:
    if status not in {"ok", "cached"} or usage is None:
        return {"id": "phone", "name": "手机", "status": status, "totalMinutes": None, "apps": []}
    apps = usage.apps[:5]
    longest = max(usage.apps, key=lambda item: item.longest_session_seconds, default=None)
    frequent = sorted(usage.apps, key=lambda item: (-item.sessions, -item.seconds, item.name.casefold()))[:3]
    return {
        "id": "phone",
        "name": "手机",
        "status": status,
        "totalMinutes": max(1, round(usage.total_seconds / 60)) if usage.total_seconds else 0,
        "apps": [
            {
                "name": app.name,
                "minutes": max(1, round(app.seconds / 60)),
                "sessions": app.sessions,
                "longestSessionMinutes": max(1, round(app.longest_session_seconds / 60)),
            }
            for app in apps
        ],
        "longestSegment": None
        if longest is None or longest.longest_session is None
        else {
            "app": longest.name,
            "startAt": longest.longest_session.start.isoformat(),
            "endAt": longest.longest_session.end.isoformat(),
            "minutes": max(1, round(longest.longest_session_seconds / 60)),
        },
        "frequentApps": [
            {
                "name": app.name,
                "sessions": app.sessions,
                "minutes": max(1, round(app.seconds / 60)),
            }
            for app in frequent
        ],
    }


def _source_error(status: str) -> str | None:
    return {
        "cached": "device_unreachable",
        "unavailable": "no_snapshot",
        "error": "invalid_snapshot",
        "not_connected": "collector_not_running",
    }.get(status)


def _remaining_note(
    reserves: tuple[ReserveRule, ...],
    fallback_minutes: int,
    calendar_minutes: int = 0,
) -> str:
    calendar_note = f"，并扣除日历安排 {calendar_minutes} 分钟" if calendar_minutes else ""
    if reserves:
        labels = "、".join(item.label for item in reserves)
        return f"按 01:00 入睡估算；已扣除尚待完成的{labels}{calendar_note}"
    if fallback_minutes:
        return f"按 01:00 入睡和未定位预留估算{calendar_note}"
    return f"按 01:00 入睡估算；今天的默认预留已完成{calendar_note}"


def _phone_source_error(status: str) -> str | None:
    return {
        "cached": "stale_export",
        "unavailable": "no_export",
        "error": "invalid_export",
        "not_connected": "collector_disabled",
    }.get(status)


def _prompt_text(computer_status: str, phone_status: str, calendar_status: str) -> str:
    missing = ["手环"]
    if calendar_status not in {"ok", "cached"}:
        missing.insert(0, "日历")
    if phone_status not in {"ok", "cached"}:
        missing.insert(0, "手机")
    if computer_status != "ok":
        missing.insert(0, "电脑")
    prefix = "手机为手动缓存；" if phone_status == "cached" else ""
    return f"{prefix}{'、'.join(missing)}尚未接入；当前剩余时间是保守估算"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None

