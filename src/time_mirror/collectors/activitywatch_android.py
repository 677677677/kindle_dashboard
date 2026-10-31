"""读取 ActivityWatch Android 导出，只保留 App 会话事实。"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import urlopen

from time_mirror.core import TimeRange, merge_ranges


SESSION_GAP = timedelta(seconds=2)
HIDDEN_PACKAGES = frozenset(
    {
        "com.android.settings",
        "com.miui.home",
        "com.miui.packageinstaller",
        "com.miui.securitycenter",
        "net.activitywatch.android",
    }
)


class AndroidExportError(ValueError):
    """Android 导出无法通过结构或字段校验。"""


class AndroidActivityWatchUnavailable(ConnectionError):
    """本机 ActivityWatch 服务没有运行。"""


@dataclass(frozen=True, slots=True)
class PhoneAppSummary:
    name: str
    package: str
    seconds: int
    sessions: int
    longest_session: TimeRange | None

    @property
    def longest_session_seconds(self) -> int:
        if self.longest_session is None:
            return 0
        return round((self.longest_session.end - self.longest_session.start).total_seconds())


@dataclass(frozen=True, slots=True)
class PhoneSegment:
    name: str
    interval: TimeRange
    reading: bool = False


TIMELINE_APP_NAMES = {
    "com.xingin.xhs": "rednote",
    "tv.danmaku.bili": "bilibili",
}


@dataclass(frozen=True, slots=True)
class PhoneUsage:
    total_seconds: int
    apps: tuple[PhoneAppSummary, ...]
    latest_event_at: datetime | None
    segments: tuple[PhoneSegment, ...] = ()
    activity_segments: tuple[PhoneSegment, ...] = ()


@dataclass(frozen=True, slots=True)
class SyncedPhoneUsage:
    usage: PhoneUsage
    collected_at: datetime | None


@dataclass(frozen=True, slots=True)
class _AppEvent:
    name: str
    package: str
    interval: TimeRange


def validate_android_export(path: Path) -> None:
    """完整解析导出；用于在替换可信运行文件前拒绝坏数据。"""
    read_android_export(
        path,
        TimeRange(datetime(1970, 1, 1, tzinfo=UTC), datetime(2100, 1, 1, tzinfo=UTC)),
    )


def read_android_export(
    path: Path,
    window: TimeRange,
    ignored_packages: Iterable[str] = (),
) -> PhoneUsage:
    """读取单个 `aw-watcher-android` bucket，并按查询窗口裁剪。"""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise AndroidExportError("Android 导出不是有效 JSON") from error

    bucket = _android_bucket(document)
    return summarize_android_events(_event_list(bucket), window, ignored_packages)


def read_synced_android(
    base_url: str,
    window: TimeRange,
    ignored_packages: Iterable[str] = (),
    timeout_seconds: float = 3,
) -> SyncedPhoneUsage | None:
    """读取 aw-sync 导入的 Android bucket；没有同步 bucket 时返回 ``None``。"""
    buckets = _request_json(f"{base_url.rstrip('/')}/api/0/buckets/", timeout_seconds)
    if not isinstance(buckets, dict):
        raise AndroidExportError("ActivityWatch bucket 列表结构不正确")
    candidates = [
        bucket_id
        for bucket_id, metadata in buckets.items()
        if _is_synced_android_bucket(bucket_id, metadata)
    ]
    if not candidates:
        return None

    # 同一手机重复导入时，保留更新时间最新的 bucket，避免累计时长被重复计算。
    selected = max(candidates, key=lambda bucket_id: _bucket_updated_at(buckets[bucket_id]))
    query = urlencode({"start": window.start.isoformat(), "end": window.end.isoformat()})
    events = _request_json(
        f"{base_url.rstrip('/')}/api/0/buckets/{quote(selected, safe='')}/events?{query}",
        timeout_seconds,
    )
    if not isinstance(events, list):
        raise AndroidExportError("Android 同步 bucket 事件结构不正确")
    return SyncedPhoneUsage(
        summarize_android_events(events, window, ignored_packages),
        _bucket_updated_at(buckets[selected]),
    )


def summarize_android_events(
    raw_events: Iterable[Any],
    window: TimeRange,
    ignored_packages: Iterable[str] = (),
) -> PhoneUsage:
    """把 Android currentwindow 事件归并为不泄露包名的 App 使用摘要。"""
    events = tuple(
        event
        for raw in raw_events
        if (event := _parse_event(raw, window)) is not None
    )
    hidden_packages = HIDDEN_PACKAGES | frozenset(ignored_packages)
    visible_events = tuple(event for event in events if event.package not in hidden_packages)
    total_seconds = _union_seconds([event.interval for event in visible_events])
    sessions = _coalesce_sessions(events)
    apps = _summarize_apps(visible_events, sessions)
    latest = max((event.interval.end for event in events), default=None)
    # 在完整会话流归并后筛选，退出到桌面或其他 App 仍会截断连续使用。
    segments = tuple(
        PhoneSegment(TIMELINE_APP_NAMES[event.package], event.interval)
        for event in sessions
        if event.package in TIMELINE_APP_NAMES and event.package not in hidden_packages
    )
    activity_segments = tuple(
        PhoneSegment(
            {"com.tencent.weread": "微信读书", "com.linkedin.android": "LinkedIn",
             "com.openai.chatgpt": "ChatGPT"}.get(event.package, event.name),
            event.interval,
            reading=event.package == "com.tencent.weread",
        )
        for event in sessions
        if event.package not in hidden_packages
    )
    return PhoneUsage(total_seconds, apps, latest, segments, activity_segments)


def _request_json(url: str, timeout_seconds: float) -> Any:
    try:
        with urlopen(url, timeout=timeout_seconds) as response:
            return json.load(response)
    except HTTPError as error:
        raise AndroidExportError("ActivityWatch Android 查询失败") from error
    except (URLError, TimeoutError, OSError) as error:
        raise AndroidActivityWatchUnavailable from error
    except (json.JSONDecodeError, UnicodeError) as error:
        raise AndroidExportError("ActivityWatch Android 返回的不是有效 JSON") from error


def _is_synced_android_bucket(bucket_id: Any, metadata: Any) -> bool:
    return (
        isinstance(bucket_id, str)
        and bucket_id.startswith("aw-watcher-android")
        and "-synced-from-" in bucket_id
        and isinstance(metadata, dict)
        and metadata.get("type") == "currentwindow"
    )


def _bucket_updated_at(metadata: Any) -> datetime:
    if not isinstance(metadata, dict):
        return datetime.min.replace(tzinfo=UTC)
    event_range = metadata.get("metadata")
    event_range_end = event_range.get("end") if isinstance(event_range, dict) else None
    for value in (metadata.get("last_updated"), event_range_end, metadata.get("created")):
        try:
            return _parse_timestamp(value)
        except AndroidExportError:
            continue
    return datetime.min.replace(tzinfo=UTC)


def _android_bucket(document: Any) -> dict[str, Any]:
    if not isinstance(document, dict) or not isinstance(document.get("buckets"), dict):
        raise AndroidExportError("Android 导出缺少 buckets")
    bucket = document["buckets"].get("aw-watcher-android")
    if not isinstance(bucket, dict) or bucket.get("type") != "currentwindow":
        raise AndroidExportError("Android 导出缺少 currentwindow bucket")
    return bucket


def _event_list(bucket: dict[str, Any]) -> list[Any]:
    events = bucket.get("events")
    if not isinstance(events, list):
        raise AndroidExportError("Android bucket 缺少 events")
    return events


def _parse_event(raw: Any, window: TimeRange) -> _AppEvent | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("data"), dict):
        return None
    name = raw["data"].get("app")
    package = raw["data"].get("package")
    duration = raw.get("duration")
    if not isinstance(name, str) or not name.strip() or not isinstance(package, str) or not package.strip():
        return None
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration):
        return None
    if duration <= 0:
        return None
    start = _parse_timestamp(raw.get("timestamp")).astimezone(window.start.tzinfo)
    end = start + timedelta(seconds=duration)
    clipped_start = max(start, window.start)
    clipped_end = min(end, window.end)
    if clipped_start >= clipped_end:
        return None
    return _AppEvent(name.strip(), package.strip(), TimeRange(clipped_start, clipped_end))


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise AndroidExportError("Android 事件缺少时间戳")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise AndroidExportError("Android 事件时间戳不正确") from error
    if parsed.tzinfo is None:
        raise AndroidExportError("Android 事件时间戳必须包含时区")
    return parsed


def _coalesce_sessions(events: tuple[_AppEvent, ...]) -> tuple[_AppEvent, ...]:
    sessions: list[_AppEvent] = []
    for event in sorted(events, key=lambda item: (item.interval.start, item.interval.end)):
        previous = sessions[-1] if sessions else None
        if previous and previous.package == event.package and event.interval.start <= previous.interval.end + SESSION_GAP:
            sessions[-1] = _AppEvent(
                previous.name,
                previous.package,
                TimeRange(previous.interval.start, max(previous.interval.end, event.interval.end)),
            )
        else:
            sessions.append(event)
    return tuple(sessions)


def _summarize_apps(
    events: tuple[_AppEvent, ...],
    sessions: tuple[_AppEvent, ...],
) -> tuple[PhoneAppSummary, ...]:
    packages = {event.package for event in events}
    summaries: list[PhoneAppSummary] = []
    for package in packages:
        app_events = [event for event in events if event.package == package]
        app_sessions = [event for event in sessions if event.package == package]
        longest_session = max(
            (event.interval for event in app_sessions),
            key=lambda interval: interval.end - interval.start,
            default=None,
        )
        latest_event = max(app_events, key=lambda event: event.interval.end)
        summaries.append(
            PhoneAppSummary(
                name=latest_event.name,
                package=package,
                seconds=_union_seconds([event.interval for event in app_events]),
                sessions=len(app_sessions),
                longest_session=longest_session,
            )
        )
    return tuple(sorted(summaries, key=lambda item: (-item.seconds, item.name.casefold(), item.package)))


def _union_seconds(intervals: list[TimeRange]) -> int:
    seconds = sum((item.end - item.start).total_seconds() for item in merge_ranges(intervals))
    return max(0, round(seconds))

