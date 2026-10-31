"""通过 ActivityWatch 本机只读 API 聚合活跃 App，不读取窗口标题。"""

from __future__ import annotations

import json
import math
import os
import sqlite3
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from time_mirror.core import TimeRange, clip_range, merge_ranges


class ActivityWatchUnavailable(ConnectionError):
    """本机 ActivityWatch 服务没有运行。"""


class ActivityWatchResponseError(RuntimeError):
    """ActivityWatch 返回了无法安全消费的结果。"""


@dataclass(frozen=True, slots=True)
class AppSummary:
    name: str
    seconds: int


@dataclass(frozen=True, slots=True)
class AppInterval:
    name: str
    interval: TimeRange


def summarize_apps(
    window: TimeRange,
    base_url: str = "http://127.0.0.1:5600",
    timeout_seconds: float = 3,
    *,
    database_path: Path | None = None,
) -> tuple[AppSummary, ...]:
    """返回 AFK 过滤后的 App 汇总；请求与结果均不包含窗口标题。"""
    by_app: dict[str, list[TimeRange]] = defaultdict(list)
    for event in read_app_intervals(window, base_url, timeout_seconds, database_path=database_path):
        by_app[event.name].append(event.interval)
    summaries = []
    for name, intervals in by_app.items():
        seconds = round(sum((item.end - item.start).total_seconds() for item in merge_ranges(intervals)))
        if seconds:
            summaries.append(AppSummary(name, seconds))
    return tuple(sorted(summaries, key=lambda item: (-item.seconds, item.name.casefold())))


def read_app_intervals(
    window: TimeRange,
    base_url: str = "http://127.0.0.1:5600",
    timeout_seconds: float = 3,
    *,
    database_path: Path | None = None,
) -> tuple[AppInterval, ...]:
    """只投影本机 App 名，再由 API 返回反向相交的 AFK 区间，窗口标题不离开 AW。"""
    window_bucket, afk_bucket, names = _read_app_names(window, database_path)
    result = _query_intervals(window, window_bucket, afk_bucket, names, base_url, timeout_seconds)
    intervals = []
    for name in names:
        events = result.get(name)
        if not isinstance(events, list):
            raise ActivityWatchResponseError("ActivityWatch 缺少 App 区间结果")
        for event in events:
            if interval := _parse_interval(event, window):
                intervals.append(AppInterval(name, interval))
    return tuple(sorted(intervals, key=lambda item: (item.interval.start, item.interval.end, item.name)))


def _read_app_names(window: TimeRange, database_path: Path | None) -> tuple[str, str, tuple[str, ...]]:
    path = database_path or _default_database_path()
    if not path.is_file():
        raise ActivityWatchUnavailable("ActivityWatch 本机数据库不可用")
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=3)) as connection:
            connection.execute("PRAGMA query_only = ON")
            buckets = connection.execute(
                "SELECT w.id, w.name, a.name FROM buckets w JOIN buckets a ON a.hostname = w.hostname "
                "WHERE w.name GLOB 'aw-watcher-window_*' AND a.name GLOB 'aw-watcher-afk_*' "
                "AND instr(w.name, '-synced-from-') = 0 AND instr(a.name, '-synced-from-') = 0 "
                "ORDER BY w.id, a.id LIMIT 1"
            ).fetchone()
            if buckets is None:
                raise ActivityWatchUnavailable("ActivityWatch 缺少本机窗口或 AFK 桶")
            rows = connection.execute(
                "SELECT DISTINCT json_extract(data, '$.app') FROM events "
                "WHERE bucketrow = ? AND starttime < ? AND endtime > ? "
                "AND json_valid(data) AND json_type(data, '$.app') = 'text'",
                (buckets[0], _epoch_nanos(window.end), _epoch_nanos(window.start)),
            ).fetchall()
    except sqlite3.Error as error:
        raise ActivityWatchResponseError("ActivityWatch 数据库不能安全投影 App 名") from error
    names = tuple(sorted({name for (name,) in rows if isinstance(name, str) and name.strip()}))
    return buckets[1], buckets[2], names


def _default_database_path() -> Path:
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    else:
        root = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
    return root / "activitywatch" / "aw-server-rust" / "sqlite.db"


def _epoch_nanos(value: datetime) -> int:
    difference = value.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (difference.days * 86400 + difference.seconds) * 1_000_000_000 + difference.microseconds * 1000


def _query_intervals(
    window: TimeRange,
    window_bucket: str,
    afk_bucket: str,
    names: tuple[str, ...],
    base_url: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    # AW 查询语言没有字段投影函数；返回 AFK 事件一侧，避免携带窗口 data。
    projections = ",".join(
        f"{json.dumps(name, ensure_ascii=False)}:filter_period_intersect(not_afk, filter_keyvals(events, \"app\", [{json.dumps(name, ensure_ascii=False)}]))"
        for name in names
    )
    payload = {
        "timeperiods": [f"{_utc(window.start)}/{_utc(window.end)}"],
        "query": [
            f"events = flood(query_bucket({json.dumps(window_bucket, ensure_ascii=False)}));",
            f"not_afk = flood(query_bucket({json.dumps(afk_bucket, ensure_ascii=False)}));",
            'not_afk = filter_keyvals(not_afk, "status", ["not-afk"]);',
            f"RETURN = {{{projections}}};",
        ],
    }
    request = Request(
        f"{base_url.rstrip('/')}/api/0/query/",
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            result = json.load(response)
    except HTTPError as error:
        raise ActivityWatchResponseError("ActivityWatch 查询失败") from error
    except (URLError, TimeoutError, OSError) as error:
        raise ActivityWatchUnavailable from error
    except (json.JSONDecodeError, UnicodeError) as error:
        raise ActivityWatchResponseError("ActivityWatch 返回的不是有效 JSON") from error

    if not isinstance(result, list) or len(result) != 1 or not isinstance(result[0], dict):
        raise ActivityWatchResponseError("ActivityWatch 查询结果结构不正确")
    return result[0]


def _parse_interval(event: Any, window: TimeRange) -> TimeRange | None:
    if not isinstance(event, dict) or event.get("data") != {"status": "not-afk"}:
        raise ActivityWatchResponseError("ActivityWatch 区间必须仅包含 AFK 状态")
    duration, timestamp = event.get("duration"), event.get("timestamp")
    if (isinstance(duration, bool) or not isinstance(duration, (int, float))
            or not math.isfinite(duration) or not isinstance(timestamp, str)):
        raise ActivityWatchResponseError("ActivityWatch 区间时间字段不正确")
    if duration <= 0:
        return None
    try:
        start = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if start.tzinfo is None:
            raise ValueError("区间必须包含时区")
        interval = TimeRange(start.astimezone(window.start.tzinfo), (start + timedelta(seconds=duration)).astimezone(window.start.tzinfo))
    except (ValueError, OverflowError) as error:
        raise ActivityWatchResponseError("ActivityWatch 区间时间戳不正确") from error
    return clip_range(interval, window)


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

