"""Outlook 最小日历事实与可信缓存契约。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from time_mirror.core import TimeRange, clip_range


BLOCKING_SHOW_AS = frozenset({"busy", "oof", "workingElsewhere"})
ALLOWED_SHOW_AS = frozenset({"free", "tentative", "busy", "oof", "workingElsewhere", "unknown"})


class OutlookResponseError(ValueError):
    """日历输入或可信缓存不符合最小数据契约。"""


@dataclass(frozen=True, slots=True)
class OutlookEvent:
    event_id: str
    subject: str
    interval: TimeRange
    show_as: str
    is_all_day: bool

    @property
    def blocks_time(self) -> bool:
        return self.show_as in BLOCKING_SHOW_AS


@dataclass(frozen=True, slots=True)
class OutlookCalendar:
    collected_at: datetime
    coverage: TimeRange
    events: tuple[OutlookEvent, ...]


def write_calendar_cache(path: Path, calendar: OutlookCalendar) -> None:
    """完整校验成功后才原子替换可信缓存。"""
    document = {
        "schemaVersion": "outlook-calendar-v1",
        "collectedAt": calendar.collected_at.isoformat(),
        "coverageStart": calendar.coverage.start.isoformat(),
        "coverageEnd": calendar.coverage.end.isoformat(),
        "events": [_event_document(event) for event in calendar.events],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_calendar_cache(path: Path, timezone: str) -> OutlookCalendar:
    document = _read_document(path, "Outlook 日历缓存")
    if document.get("schemaVersion") != "outlook-calendar-v1":
        raise OutlookResponseError("Outlook 日历缓存版本不受支持")
    zone = ZoneInfo(timezone)
    coverage = _coverage(document, zone)
    raw_events = document.get("events")
    if not isinstance(raw_events, list):
        raise OutlookResponseError("Outlook 日历缓存缺少 events")
    events = tuple(_cached_event(item, coverage, zone) for item in raw_events)
    return OutlookCalendar(_timestamp(document.get("collectedAt"), zone), coverage, events)


def cache_covers(calendar: OutlookCalendar, window: TimeRange) -> bool:
    return calendar.coverage.start <= window.start and calendar.coverage.end >= window.end


def synced_on(calendar: OutlookCalendar, day: date) -> bool:
    return calendar.collected_at.date() == day


def _read_document(path: Path, label: str) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise OutlookResponseError(f"{label}不是有效 JSON") from error
    if not isinstance(document, dict):
        raise OutlookResponseError(f"{label}必须是 JSON 对象")
    return document


def _coverage(document: dict[str, Any], zone: ZoneInfo) -> TimeRange:
    return TimeRange(
        _timestamp(document.get("coverageStart"), zone),
        _timestamp(document.get("coverageEnd"), zone),
    )


def _cached_event(raw: Any, coverage: TimeRange, zone: ZoneInfo) -> OutlookEvent:
    if not isinstance(raw, dict):
        raise OutlookResponseError("Outlook 日历缓存事件不是对象")
    interval = TimeRange(_timestamp(raw.get("startAt"), zone), _timestamp(raw.get("endAt"), zone))
    if clip_range(interval, coverage) != interval:
        raise OutlookResponseError("Outlook 日历缓存事件超出覆盖范围")
    event_id = raw.get("id")
    subject = raw.get("subject")
    if not isinstance(event_id, str) or not event_id or not isinstance(subject, str) or not subject:
        raise OutlookResponseError("Outlook 日历缓存事件字段不完整")
    return OutlookEvent(event_id, subject, interval, _show_as(raw.get("showAs")), raw.get("isAllDay") is True)


def _event_document(event: OutlookEvent) -> dict[str, Any]:
    return {
        "id": event.event_id,
        "subject": event.subject,
        "startAt": event.interval.start.isoformat(),
        "endAt": event.interval.end.isoformat(),
        "showAs": event.show_as,
        "isAllDay": event.is_all_day,
    }


def _timestamp(raw: Any, zone: ZoneInfo) -> datetime:
    if not isinstance(raw, str):
        raise OutlookResponseError("Outlook 日历数据缺少时间戳")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as error:
        raise OutlookResponseError("Outlook 日历时间戳不正确") from error
    if parsed.tzinfo is None:
        raise OutlookResponseError("Outlook 日历时间戳必须包含时区")
    return parsed.astimezone(zone)


def _show_as(raw: Any) -> str:
    return raw if isinstance(raw, str) and raw in ALLOWED_SHOW_AS else "unknown"

