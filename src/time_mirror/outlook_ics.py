"""Outlook 已发布 ICS 的加密配置、展开与每日同步。"""

from __future__ import annotations

import hashlib
import os
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import recurring_ical_events
from icalendar import Calendar
from msal_extensions import FilePersistence, FilePersistenceWithDataProtection

from time_mirror.collectors.outlook_calendar import OutlookCalendar, OutlookEvent, OutlookResponseError
from time_mirror.core import TimeRange, clip_range


MAX_ICS_BYTES = 5 * 1024 * 1024
ALLOWED_HOSTS = frozenset({"outlook.office365.com", "outlook.office.com"})


def default_secret_root() -> Path:
    """返回仓库外、交互进程与计划任务都可见的密钥目录。"""
    return Path.home() / ".time-mirror"


def configure_ics_url(url: str, root: Path) -> Path:
    """校验并用当前 Windows 用户保护 ICS bearer URL。"""
    _validate_ics_url(url)
    path = _secret_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    _persistence(path).save(url.strip())
    return path


def synchronize_outlook_ics(
    config_path: Path | None = None,
    root: Path | None = None,
    *,
    secret_root: Path | None = None,
    force: bool = False,
) -> OutlookCalendar | None:
    """在每日窗口到期时下载、最小化并替换可信日历缓存。"""
    from time_mirror.runtime import (
        _outlook_cache_path,
        load_config,
        outlook_sync_window,
        runtime_root,
    )
    from time_mirror.collectors.outlook_calendar import write_calendar_cache

    destination = root or runtime_root()
    window = outlook_sync_window(config_path, destination, force=force)
    if window is None:
        return None
    config = load_config(config_path)
    url = _persistence(_secret_path(secret_root or default_secret_root())).load()
    payload = _download_ics(url)
    calendar = read_ics(payload, window, config["timezone"])
    write_calendar_cache(_outlook_cache_path(destination), calendar)
    return calendar


def read_ics(payload: bytes, coverage: TimeRange, timezone: str) -> OutlookCalendar:
    """展开循环日程，并丢弃位置、正文、参会人和会议链接。"""
    try:
        calendar = Calendar.from_ical(payload)
        components = recurring_ical_events.of(calendar).between(coverage.start, coverage.end)
    except Exception as error:
        raise OutlookResponseError("Outlook ICS 无法解析") from error

    zone = ZoneInfo(timezone)
    events: list[OutlookEvent] = []
    for component in components:
        event = _component_event(component, coverage, zone)
        if event is not None:
            events.append(event)
    return OutlookCalendar(
        datetime.now(zone),
        coverage,
        tuple(sorted(events, key=lambda item: (item.interval.start, item.interval.end, item.subject))),
    )


def _component_event(component: Any, coverage: TimeRange, zone: ZoneInfo) -> OutlookEvent | None:
    if str(component.get("STATUS", "")).upper() == "CANCELLED":
        return None
    try:
        start_raw = component.decoded("DTSTART")
    except (KeyError, ValueError) as error:
        raise OutlookResponseError("Outlook ICS 日程缺少开始时间") from error
    end_raw = component.decoded("DTEND", None)
    start, is_all_day = _local_time(start_raw, zone)
    if end_raw is None:
        end = start + (timedelta(days=1) if is_all_day else timedelta(seconds=1))
    else:
        end, _ = _local_time(end_raw, zone)
    interval = TimeRange(start, end)
    clipped = clip_range(interval, coverage)
    if clipped is None:
        return None

    uid = str(component.get("UID", ""))
    recurrence = str(component.get("RECURRENCE-ID", start.isoformat()))
    event_id = hashlib.sha256(f"{uid}|{recurrence}|{start.isoformat()}".encode()).hexdigest()[:16]
    private = str(component.get("CLASS", "")).upper() == "PRIVATE"
    subject = "私人日程" if private else _subject(component.get("SUMMARY"))
    return OutlookEvent(event_id, subject, clipped, _show_as(component), is_all_day)


def _local_time(raw: date | datetime, zone: ZoneInfo) -> tuple[datetime, bool]:
    if isinstance(raw, datetime):
        value = raw.replace(tzinfo=zone) if raw.tzinfo is None else raw.astimezone(zone)
        return value, False
    if isinstance(raw, date):
        return datetime.combine(raw, time.min, zone), True
    raise OutlookResponseError("Outlook ICS 日程时间类型不受支持")


def _subject(raw: Any) -> str:
    subject = str(raw).strip() if raw is not None else ""
    return (subject or "未命名日程")[:120]


def _show_as(component: Any) -> str:
    raw = str(component.get("X-MICROSOFT-CDO-BUSYSTATUS", "")).upper()
    mapping = {
        "FREE": "free",
        "TENTATIVE": "tentative",
        "BUSY": "busy",
        "OOF": "oof",
        "WORKINGELSEWHERE": "workingElsewhere",
    }
    if raw in mapping:
        return mapping[raw]
    return "free" if str(component.get("TRANSP", "")).upper() == "TRANSPARENT" else "busy"


def _download_ics(url: str) -> bytes:
    _validate_ics_url(url)
    request = Request(url, headers={"User-Agent": "TimeMirror/0.1", "Accept": "text/calendar"})
    try:
        with urlopen(request, timeout=30) as response:
            payload = response.read(MAX_ICS_BYTES + 1)
    except HTTPError as error:
        raise OutlookResponseError(f"Outlook ICS 下载失败：HTTP {error.code}") from error
    except (URLError, TimeoutError) as error:
        raise OutlookResponseError("Outlook ICS 下载失败") from error
    if len(payload) > MAX_ICS_BYTES:
        raise OutlookResponseError("Outlook ICS 超过大小限制")
    return payload


def _validate_ics_url(url: str) -> None:
    parsed = urlparse(url.strip())
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS or not parsed.path.endswith("/calendar.ics"):
        raise ValueError("不是受支持的 Outlook ICS 发布地址")


def _secret_path(root: Path) -> Path:
    return root / "secrets" / "outlook-ics-url.bin"


def _persistence(path: Path):
    if os.name == "nt":
        return FilePersistenceWithDataProtection(str(path))
    return FilePersistence(str(path))

