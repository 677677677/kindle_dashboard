"""本地同步、快照落盘与只读运行时 HTTP 服务。"""

from __future__ import annotations

import json
import hashlib
import os
import sqlite3
import subprocess
import sys
import threading
from time import sleep
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from time_mirror.collectors.activitywatch import (
    ActivityWatchResponseError,
    ActivityWatchUnavailable,
    AppSummary,
    AppInterval,
    read_app_intervals,
)
from time_mirror.collectors.activitywatch_android import (
    AndroidActivityWatchUnavailable,
    AndroidExportError,
    PhoneUsage,
    read_android_export,
    read_synced_android,
)
from time_mirror.collectors.koreader import DatabaseSchemaError, KoreaderDatabase, copy_snapshot, validate_database
from time_mirror.collectors.gadgetbridge import GadgetbridgeError, UnsupportedSleepStages, read_sleep, read_sleep_sessions
from time_mirror.collectors.outlook_calendar import (
    OutlookCalendar,
    OutlookEvent,
    OutlookResponseError,
    cache_covers,
    read_calendar_cache,
    synced_on,
)
from time_mirror.core import ReserveRule, TimeRange, pending_reserves, statistical_date_for, statistical_day_bounds, next_bedtime
from time_mirror.core.allocation import range_seconds
from time_mirror.history import update_history
from time_mirror.snapshot import Snapshot, allocation_for_day, build_snapshot, validate_snapshot


DEFAULT_CONFIG: dict[str, Any] = {
    "timezone": "Asia/Shanghai",
    "remainingTime": {
        "reservePlan": [
            {"label": "去程通勤", "minutes": 30, "assumedCompletedBy": "10:00"},
            {"label": "午餐", "minutes": 60, "assumedCompletedBy": "14:00"},
            {"label": "返程通勤", "minutes": 30, "assumedCompletedBy": "20:00"},
            {"label": "晚餐", "minutes": 90, "assumedCompletedBy": "21:00"},
            {"label": "睡前准备", "minutes": 90, "assumedCompletedBy": "00:00"},
        ]
    },
    "activitywatch": {"enabled": True, "baseUrl": "http://127.0.0.1:5600"},
    "classification": {
        "sleepMinutes": 0,
        "gameApps": ["tlou-ii.exe"],
        "courseKeywords": ["课程", "上课", "lecture", "tutorial", "sectional teaching"],
    },
    "android": {
        "enabled": True,
        "syncEnabled": True,
        "syncDirectory": None,
        "syncExecutable": None,
        "exportPath": None,
        "staleAfterMinutes": 1440,
        "ignorePackages": [],
    },
    "outlook": {
        "enabled": False,
        "syncAfter": "07:00",
    },
    "gadgetbridge": {"enabled": False, "databasePath": None},
    "kindle": {
        "hosts": ["kindle.local"],
        "port": 2222,
        "user": "root",
        "privateKey": "%USERPROFILE%/.ssh/kindle_dashboard_rsa",
        "statisticsDatabase": "/mnt/us/koreader/settings/statistics.sqlite3",
    },
}


@dataclass(frozen=True, slots=True)
class SyncResult:
    status: str
    path: Path | None
    collected_at: datetime | None


@dataclass(frozen=True, slots=True)
class ComputerResult:
    status: str
    apps: tuple[AppSummary, ...]
    collected_at: datetime | None
    segments: tuple[AppInterval, ...] = ()


@dataclass(frozen=True, slots=True)
class PhoneResult:
    status: str
    usage: PhoneUsage | None
    collected_at: datetime | None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class CalendarResult:
    status: str
    events: tuple[OutlookEvent, ...]
    collected_at: datetime | None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class HealthResult:
    status: str
    intervals: tuple[TimeRange, ...] = ()
    collected_at: datetime | None = None
    latest_event_at: datetime | None = None
    error_code: str | None = None
    sessions: tuple[TimeRange, ...] = ()


def runtime_root() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "TimeMirror"
    if os.name == "nt":
        return Path.home() / "AppData" / "Local" / "TimeMirror"
    return Path.home() / ".time-mirror"


def load_config(path: Path | None = None) -> dict[str, Any]:
    config_path = path or runtime_root() / "config.json"
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    if not config_path.exists():
        return config
    supplied = json.loads(config_path.read_text(encoding="utf-8"))
    _merge(config, supplied)
    return config


def refresh_snapshot(config_path: Path | None = None, root: Path | None = None) -> Snapshot:
    destination = root or runtime_root()
    config = load_config(config_path)
    zone = ZoneInfo(config["timezone"])
    now = datetime.now(zone)
    day = statistical_day_bounds(statistical_date_for(now), str(zone))
    reserve_plan = _reserve_rules(config)
    reserves = pending_reserves(now, day, reserve_plan)
    sync = _sync_koreader(config, destination, zone)
    computer = _read_computer(config, now)
    _pull_android_sync(config, destination)
    phone = _read_phone(config, destination, now, zone)
    calendar = _read_calendar(config, destination, now)
    health = _read_health(config, destination, now, day)
    device_sources = {
        "computer_status": computer.status,
        "computer_collected_at": computer.collected_at,
        "computer_apps": computer.apps,
        "computer_segments": computer.segments,
        "phone_status": phone.status,
        "phone_collected_at": phone.collected_at,
        "phone_error_code": phone.error_code,
        "phone_usage": phone.usage,
        "calendar_status": calendar.status,
        "calendar_collected_at": calendar.collected_at,
        "calendar_events": calendar.events,
        "calendar_error_code": calendar.error_code,
        "reserve_plan": reserve_plan,
        "sleep_ranges": health.intervals,
        "health_status": health.status,
        "health_collected_at": health.collected_at,
        "health_latest_event_at": health.latest_event_at,
        "health_error_code": health.error_code,
        "sleep_sessions": health.sessions,
        **_classification_options(config),
    }
    try:
        snapshot = build_snapshot(
            now,
            sync.path,
            sync.status,
            sync.collected_at,
            sum(item.minutes for item in reserves),
            reserve_breakdown=reserves,
            **device_sources,
        )
    except (DatabaseSchemaError, sqlite3.DatabaseError, ValueError):
        snapshot = build_snapshot(
            now,
            None,
            "error",
            sync.collected_at,
            sum(item.minutes for item in reserves),
            reserve_breakdown=reserves,
            **device_sources,
        )
    validate_snapshot(snapshot)
    snapshot["history"] = _refresh_history(config, destination, now, sync, snapshot, computer, phone)
    _write_runtime_files(snapshot, destination / "web")
    return snapshot


def _read_health(config: dict, root: Path, now: datetime, day: TimeRange) -> HealthResult:
    settings = config.get("gadgetbridge", {})
    if not settings.get("enabled"):
        return HealthResult("not_connected")
    path = Path(os.path.expandvars(settings.get("databasePath") or str(root / "HealthData" / "Gadgetbridge.db")))
    try:
        collected = datetime.fromtimestamp(path.stat().st_mtime, now.tzinfo)
        sessions = read_sleep_sessions(path, day, now)
        try:
            data = read_sleep(path, day, now)
        except UnsupportedSleepStages:
            data = None
        if data is None or not data.intervals:
            overlapping = read_sleep_sessions(path, day, now, overlap=True)
            if overlapping:
                stale = now - collected > timedelta(hours=36)
                return HealthResult("cached" if stale else "ok", overlapping, collected,
                                    max(item.end for item in overlapping), "sleep_session_span", sessions)
            return HealthResult("cached", (), collected, None, "no_sleep_record", sessions)
        stale = now - collected > timedelta(hours=36)
        return HealthResult("cached" if stale else "ok", data.intervals, collected,
                            data.latest_event_at, "stale_export" if stale else None, sessions)
    except FileNotFoundError:
        return HealthResult("unavailable", error_code="missing_export")
    except (OSError, sqlite3.DatabaseError, GadgetbridgeError, ValueError):
        return HealthResult("error", error_code="invalid_sleep_export")


def configure_outlook(config_path: Path | None = None, *, source: str | None = None) -> Path:
    """启用 Outlook 缓存读取，并记录仓库外采集来源。"""
    path = config_path or runtime_root() / "config.json"
    supplied: dict[str, Any] = {}
    if path.exists():
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("私有配置必须是 JSON 对象")
        supplied = loaded
    outlook = {
        **(supplied.get("outlook") if isinstance(supplied.get("outlook"), dict) else {}),
        "enabled": True,
        "syncAfter": "07:00",
    }
    if source is not None:
        outlook["source"] = source
    supplied["outlook"] = outlook
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, f"{json.dumps(supplied, ensure_ascii=False, indent=2)}\n")
    return path


def outlook_sync_window(
    config_path: Path | None = None,
    root: Path | None = None,
    *,
    force: bool = False,
) -> TimeRange | None:
    """返回本次日历同步窗口；不需要同步时返回 ``None``。"""
    destination = root or runtime_root()
    config = load_config(config_path)
    settings = config["outlook"]
    zone = ZoneInfo(config["timezone"])
    now = datetime.now(zone)
    cache_path = _outlook_cache_path(destination)
    cached = _try_calendar_cache(cache_path, config["timezone"])
    sync_after = _outlook_sync_time(settings.get("syncAfter"))
    if not force and now.time() < sync_after and cached is not None:
        return None
    if not force and cached is not None and synced_on(cached, now.date()):
        return None

    day = statistical_day_bounds(statistical_date_for(now), str(zone))
    return TimeRange(day.start, day.end + timedelta(days=1))


def _reserve_rules(config: dict[str, Any]) -> tuple[ReserveRule, ...]:
    plan = config.get("remainingTime", {}).get("reservePlan", [])
    if not isinstance(plan, list):
        raise ValueError("remainingTime.reservePlan 必须是数组")

    rules: list[ReserveRule] = []
    for item in plan:
        if not isinstance(item, dict):
            raise ValueError("预留计划必须是对象")
        try:
            completed_by = time.fromisoformat(str(item["assumedCompletedBy"]))
            rules.append(ReserveRule(str(item["label"]), int(item["minutes"]), completed_by))
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("预留计划字段不正确") from error
    return tuple(rules)


def serve_runtime(
    config_path: Path | None = None,
    root: Path | None = None,
    port: int = 8765,
    interval_seconds: int = 300,
) -> None:
    destination = root or runtime_root()
    refresh_snapshot(config_path, destination)
    stop_event = threading.Event()
    worker = threading.Thread(
        target=_refresh_loop,
        args=(stop_event, config_path, destination, interval_seconds),
        daemon=True,
    )
    worker.start()
    handler = _handler_for(destination / "web")
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    try:
        server.serve_forever()
    finally:
        stop_event.set()
        server.server_close()


def _sync_koreader(config: dict[str, Any], root: Path, zone: ZoneInfo) -> SyncResult:
    settings = config["kindle"]
    target = root / "imports" / "koreader-statistics.sqlite3"
    private_key = Path(os.path.expanduser(os.path.expandvars(settings["privateKey"])))
    for host in settings["hosts"]:
        try:
            copy_snapshot(
                host,
                int(settings["port"]),
                settings["user"],
                private_key,
                settings["statisticsDatabase"],
                target,
                host_key_alias=settings.get("hostKeyAlias"),
            )
            return SyncResult("ok", target, _modified_at(target, zone))
        except (OSError, subprocess.SubprocessError, DatabaseSchemaError, sqlite3.DatabaseError):
            continue
    if target.exists():
        try:
            validate_database(target)
            return SyncResult("cached", target, _modified_at(target, zone))
        except (DatabaseSchemaError, sqlite3.DatabaseError):
            return SyncResult("error", None, _modified_at(target, zone))
    return SyncResult("unavailable", None, None)


def _read_computer(config: dict[str, Any], now: datetime, window: TimeRange | None = None) -> ComputerResult:
    settings = config["activitywatch"]
    if not settings.get("enabled", True):
        return ComputerResult("not_connected", (), None)
    day = statistical_day_bounds(statistical_date_for(now), str(now.tzinfo))
    try:
        database = settings.get("databasePath")
        path = Path(os.path.expandvars(os.path.expanduser(database))) if isinstance(database, str) and database else None
        segments = read_app_intervals(window or TimeRange(day.start, now), settings["baseUrl"], database_path=path)
        names = {item.name for item in segments}
        period = window or TimeRange(day.start, now)
        apps = tuple(sorted((AppSummary(name, round(range_seconds(
            tuple(item.interval for item in segments if item.name == name), period))) for name in names),
            key=lambda item: (-item.seconds, item.name.casefold())))
        return ComputerResult("ok", apps, now, segments)
    except ActivityWatchUnavailable:
        return ComputerResult("not_connected", (), None)
    except ActivityWatchResponseError:
        return ComputerResult("error", (), now)


def _read_phone(config: dict[str, Any], root: Path, now: datetime, zone: ZoneInfo,
                window: TimeRange | None = None) -> PhoneResult:
    settings = config["android"]
    if not settings.get("enabled", True):
        return PhoneResult("not_connected", None, None)
    day = statistical_day_bounds(statistical_date_for(now), str(now.tzinfo))
    window = window or TimeRange(day.start, now)
    ignored_packages = settings.get("ignorePackages", [])
    if not isinstance(ignored_packages, list):
        ignored_packages = []
    visible_packages = tuple(item for item in ignored_packages if isinstance(item, str))
    stale_after = _positive_int(settings.get("staleAfterMinutes"), 1440)

    if settings.get("syncEnabled", True) and config["activitywatch"].get("enabled", True):
        try:
            synced = read_synced_android(
                config["activitywatch"]["baseUrl"],
                window,
                visible_packages,
            )
            if synced is not None:
                collected_at = synced.collected_at or synced.usage.latest_event_at or now
                return _phone_result(now, window, synced.usage, collected_at, stale_after)
        except AndroidActivityWatchUnavailable:
            pass
        except AndroidExportError:
            return PhoneResult("error", None, now)

    path = _android_export_path(settings.get("exportPath"), root)
    if not path.exists():
        return PhoneResult("unavailable", None, None)
    collected_at = _modified_at(path, zone)
    try:
        usage = read_android_export(
            path,
            window,
            visible_packages,
        )
        return _phone_result(now, window, usage, collected_at, stale_after)
    except AndroidExportError:
        return PhoneResult("error", None, collected_at)


def _phone_result(now: datetime, window: TimeRange, usage: PhoneUsage,
                  collected_at: datetime, stale_after: int) -> PhoneResult:
    collected_at = collected_at.astimezone(now.tzinfo)
    day = statistical_day_bounds(statistical_date_for(now), str(now.tzinfo))
    missing_today = (window.start == day.start and window.end == now
                     and usage.latest_event_at is None and collected_at < day.start)
    if missing_today:
        return PhoneResult("cached", usage, collected_at, "awaiting_today_sync")
    return PhoneResult(_freshness_status(now, collected_at, stale_after), usage, collected_at)


def _classification_options(config: dict[str, Any]) -> dict[str, Any]:
    settings = config.get("classification", {})
    minutes = settings.get("sleepMinutes", 0)
    if isinstance(minutes, bool) or not isinstance(minutes, int) or not 0 <= minutes <= 1440:
        raise ValueError("classification.sleepMinutes 必须在0至1440之间")
    result = {"sleep_minutes": minutes}
    for public, private in (("game_apps", "gameApps"), ("course_keywords", "courseKeywords")):
        values = settings.get(private, DEFAULT_CONFIG["classification"][private])
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise ValueError(f"classification.{private} 必须是字符串数组")
        result[public] = tuple(values)
    return result


def _refresh_history(config, root, now, sync, snapshot, computer, phone) -> dict:
    options = _classification_options(config)
    policy = {**options, "reserve_plan": config.get("remainingTime", {}), "dayBoundary": "00:00"}
    fingerprint = hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest()
    today = statistical_day_bounds(statistical_date_for(now), str(now.tzinfo))
    period = TimeRange(today.start - timedelta(days=7), today.start)
    past_computer = _read_computer(config, now, period)
    past_phone = _read_phone(config, root, now, ZoneInfo(str(now.tzinfo)), period)
    calendar = _try_calendar_cache(_outlook_cache_path(root), str(now.tzinfo))
    records = {}
    for index in range(7, -1, -1):
        date = statistical_date_for(now) - timedelta(days=index)
        day = statistical_day_bounds(date, str(now.tzinfo))
        cutoff = now if index == 0 else day.end
        pc = computer if index == 0 else past_computer
        mobile = phone if index == 0 else past_phone
        events, kindle_available = _history_reading(sync, day, str(now.tzinfo))
        courses = tuple(item for item in calendar.events if _clip_calendar_event(item, day)) if calendar else ()
        rules = _reserve_rules(config)
        allocation = snapshot["allocation"] if index == 0 else allocation_for_day(
            day, cutoff, pc.segments, mobile.usage, events, courses, rules,
            sleep_ranges=_read_health(config, root, now, day).intervals, **options)
        available = {"phone": mobile.usage is not None,
                     "computer": pc.status == "ok", "koreader": kindle_available}
        phone_seconds = range_seconds(tuple(item.interval for item in mobile.usage.activity_segments),
                                      day) if mobile.usage else None
        reading_seconds = next(item["seconds"] for item in allocation["categories"] if item["id"] == "reading")
        records[date.isoformat()] = {
            "phoneSeconds": phone_seconds,
            "readingSeconds": reading_seconds if all(available.values()) else None,
            "inputsAvailable": available,
            "plan": {"fixedMinutes": allocation["fixedMinutes"],
                     "calendarCovered": bool(calendar and cache_covers(calendar, day)),
                     "sleepMinutes": options["sleep_minutes"]},
        }
    try:
        return update_history(root, now, records, _atomic_write, fingerprint)
    except OSError:
        # 历史写入失败不影响当日快照，保留已有可信汇总。
        return update_history(root, now, {}, lambda path, text: None, fingerprint)


def _history_reading(sync: SyncResult, day: TimeRange, timezone: str) -> tuple[tuple, bool]:
    if sync.path is None or sync.status not in {"ok", "cached"}:
        return (), False
    try:
        return KoreaderDatabase(sync.path, timezone).reading_events(day), True
    except (DatabaseSchemaError, sqlite3.DatabaseError, OSError):
        return (), False


def _read_calendar(config: dict[str, Any], root: Path, now: datetime) -> CalendarResult:
    settings = config["outlook"]
    if not settings.get("enabled", False):
        return CalendarResult("not_connected", (), None)
    calendar = _try_calendar_cache(_outlook_cache_path(root), str(now.tzinfo))
    if calendar is None:
        return CalendarResult("unavailable", (), None, "missing_cache")
    day = statistical_day_bounds(statistical_date_for(now), str(now.tzinfo))
    query = TimeRange(day.start, max(day.end, next_bedtime(now)))
    events = tuple(
        OutlookEvent(item.event_id, item.subject, clipped, item.show_as, item.is_all_day)
        for item in calendar.events
        if (clipped := _clip_calendar_event(item, query)) is not None
    )
    if not cache_covers(calendar, query):
        return CalendarResult("cached", (), calendar.collected_at, "coverage_mismatch")
    status = "ok" if synced_on(calendar, now.date()) else "cached"
    error_code = None if status == "ok" else "daily_cache"
    return CalendarResult(status, events, calendar.collected_at, error_code)


def _clip_calendar_event(event: OutlookEvent, window: TimeRange) -> TimeRange | None:
    start = max(event.interval.start, window.start)
    end = min(event.interval.end, window.end)
    return TimeRange(start, end) if start < end else None


def _try_calendar_cache(path: Path, timezone: str) -> OutlookCalendar | None:
    if not path.exists():
        return None
    try:
        return read_calendar_cache(path, timezone)
    except OutlookResponseError:
        return None


def _outlook_cache_path(root: Path) -> Path:
    return root / "imports" / "outlook-calendar.json"


def _outlook_sync_time(value: Any) -> time:
    try:
        return time.fromisoformat(str(value or "07:00"))
    except ValueError as error:
        raise ValueError("outlook.syncAfter 必须是 HH:MM") from error


def _pull_android_sync(config: dict[str, Any], root: Path) -> None:
    settings = config["android"]
    if not settings.get("enabled", True) or not settings.get("syncEnabled", True):
        return
    executable = _configured_path(settings.get("syncExecutable"), _default_aw_sync(root), root)
    sync_directory = _configured_path(settings.get("syncDirectory"), root / "ActivityWatchSync", root)
    if not executable.is_file() or not sync_directory.is_dir():
        return
    activitywatch = config["activitywatch"]
    base_url = str(activitywatch.get("baseUrl", "http://127.0.0.1:5600"))
    host, port = _activitywatch_address(base_url)
    try:
        subprocess.run(
            [
                str(executable),
                "--host",
                host,
                "--port",
                str(port),
                "--sync-dir",
                str(sync_directory),
                "sync",
                "--mode",
                "pull",
            ],
            check=True,
            timeout=30,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        # 同步失败不得覆盖最后一份可信数据；随后仍会读取本机缓存或手动导出。
        return


def _android_export_path(configured: Any, root: Path) -> Path:
    if not configured:
        return root / "imports" / "activitywatch-android.json"
    expanded = Path(os.path.expanduser(os.path.expandvars(str(configured))))
    return expanded if expanded.is_absolute() else root / expanded


def _default_aw_sync(root: Path) -> Path:
    return root / "tools" / "activitywatch-v0.14.1" / "activitywatch" / "aw-server-rust" / "aw-sync.exe"


def _configured_path(configured: Any, default: Path, root: Path) -> Path:
    if not configured:
        return default
    expanded = Path(os.path.expanduser(os.path.expandvars(str(configured))))
    return expanded if expanded.is_absolute() else root / expanded


def _activitywatch_address(base_url: str) -> tuple[str, int]:
    without_scheme = base_url.split("://", 1)[-1].rstrip("/")
    host, separator, raw_port = without_scheme.rpartition(":")
    if separator and raw_port.isdigit():
        return host or "127.0.0.1", int(raw_port)
    return without_scheme or "127.0.0.1", 5600


def _write_runtime_files(snapshot: Snapshot, directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
    _atomic_write(directory / "snapshot.json", f"{serialized}\n")
    _atomic_write(directory / "snapshot.js", f"window.__TIME_MIRROR_SNAPSHOT__={serialized};\n")


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8", newline="\n")
        for attempt in range(5):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                sleep(0.01 * (attempt + 1))
    finally:
        temporary.unlink(missing_ok=True)


def _refresh_loop(stop: threading.Event, config_path: Path | None, root: Path, interval: int) -> None:
    while not stop.wait(interval):
        try:
            refresh_snapshot(config_path, root)
        except Exception as error:
            print(f"快照刷新失败：{type(error).__name__}", file=sys.stderr, flush=True)


def _handler_for(directory: Path) -> type[BaseHTTPRequestHandler]:
    class RuntimeHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/health":
                self._send(b"ok\n", "text/plain; charset=utf-8")
                return
            files = {"/snapshot.js": "snapshot.js", "/snapshot.json": "snapshot.json"}
            filename = files.get(self.path)
            if filename is None or not (directory / filename).exists():
                self.send_error(404)
                return
            content_type = "application/javascript; charset=utf-8" if filename.endswith(".js") else "application/json"
            self._send((directory / filename).read_bytes(), content_type)

        def _send(self, content: bytes, content_type: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, format_string: str, *args: object) -> None:
            return

    return RuntimeHandler


def _modified_at(path: Path, zone: ZoneInfo) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, zone)


def _positive_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _freshness_status(now: datetime, collected_at: datetime, stale_after_minutes: int) -> str:
    age = max(timedelta(), now - collected_at.astimezone(now.tzinfo))
    return "ok" if age <= timedelta(minutes=stale_after_minutes) else "cached"


def _merge(target: dict[str, Any], supplied: dict[str, Any]) -> None:
    for key, value in supplied.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge(target[key], value)
        else:
            target[key] = value

