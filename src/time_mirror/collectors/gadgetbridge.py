"""只读解析 Gadgetbridge 华为睡眠起止标记，不推测未知阶段格式。"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from time_mirror.core import TimeRange, clip_range, merge_ranges


class GadgetbridgeError(ValueError):
    """数据库缺失、损坏或睡眠格式无法可信读取。"""


class UnsupportedSleepStages(GadgetbridgeError):
    """有睡眠记录，但当前无法识别阶段格式。"""


@dataclass(frozen=True, slots=True)
class SleepData:
    intervals: tuple[TimeRange, ...]
    latest_event_at: datetime | None


SLEEP_KINDS = (6, 7, 0x5656, 0x5659)
REQUIRED_COLUMNS = {"TIMESTAMP", "OTHER_TIMESTAMP", "SOURCE", "RAW_KIND", "DEVICE_ID", "USER_ID"}


def read_sleep_sessions(path: Path, window: TimeRange, now: datetime, *, overlap: bool = False) -> tuple[TimeRange, ...]:
    """按醒来日期读取完整实测寝起；不从活动阶段或分类碎片拼接。"""
    if now.tzinfo is None:
        raise GadgetbridgeError("当前时间必须包含时区")
    try:
        if not path.is_file() or path.stat().st_size == 0:
            raise GadgetbridgeError("尚未收到有效的 Gadgetbridge 数据库")
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
            connection.execute("PRAGMA query_only = ON")
            if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise GadgetbridgeError("Gadgetbridge 数据库完整性检查失败")
            rows = _session_rows(connection)
        return _complete_sessions(rows, window, now, overlap=overlap)
    except (sqlite3.Error, OSError, OverflowError) as error:
        raise GadgetbridgeError("无法读取 Gadgetbridge 完整睡眠记录") from error


def _session_rows(connection: sqlite3.Connection) -> list[tuple]:
    columns = {row[1].upper() for row in connection.execute('PRAGMA table_info("HUAWEI_SLEEP_STATS_SAMPLE")')}
    if not columns:
        return []
    required = {"TIMESTAMP", "WAKEUP_TIME", "DEVICE_ID", "USER_ID"}
    if not required.issubset(columns):
        raise GadgetbridgeError("不支持的 Gadgetbridge 完整睡眠表结构")
    # 官方 HuaweiSleepSessionProvider 使用入睡时间和醒来时间；BED_TIME 不等于入睡。
    return connection.execute(
        'SELECT TIMESTAMP, WAKEUP_TIME, DEVICE_ID, USER_ID FROM HUAWEI_SLEEP_STATS_SAMPLE'
    ).fetchall()


def _complete_sessions(rows: list[tuple], window: TimeRange, now: datetime, *, overlap: bool = False) -> tuple[TimeRange, ...]:
    candidates = [(_session_interval(start, end, now), device, user) for start, end, device, user in rows]
    selected = [(interval, device, user) for interval, device, user in candidates
                if (interval.start < window.end and interval.end > window.start
                    if overlap else window.start <= interval.end < window.end)]
    if len({(device, user) for _, device, user in selected}) > 1:
        raise GadgetbridgeError("存在多个设备或用户的完整睡眠记录，需要明确选择来源")
    accepted: list[TimeRange] = []
    # 官方先取最长记录，忽略同一完整夜间记录内部的部分记录；不连接间隔。
    for interval, _, _ in sorted(selected, key=lambda value: value[0].end - value[0].start, reverse=True):
        if not any(value.start <= interval.start and interval.end <= value.end for value in accepted):
            accepted.append(interval)
    return tuple(sorted(accepted, key=lambda value: value.start))


def _session_interval(start: object, end: object, now: datetime) -> TimeRange:
    if not all(type(value) is int and value > 0 for value in (start, end)) or start >= end:
        raise GadgetbridgeError("完整睡眠起止时间无效")
    try:
        begin = datetime.fromtimestamp(start / 1000, now.tzinfo)
        finish = datetime.fromtimestamp(end / 1000, now.tzinfo)
    except (ValueError, OverflowError, OSError) as error:
        raise GadgetbridgeError("完整睡眠时间戳超出支持范围") from error
    # 华为穿戴记录不会早于2000年；秒误传为毫秒时会落在1970年。
    if begin.year < 2000 or finish > now:
        raise GadgetbridgeError("完整睡眠包含未来时间或非毫秒时间戳")
    return TimeRange(begin, finish)


def read_sleep(path: Path, window: TimeRange, now: datetime) -> SleepData:
    """返回裁到查询窗口的睡眠并集；空数据不证明实际睡眠为零。"""
    if now.tzinfo is None:
        raise GadgetbridgeError("当前时间必须包含时区")
    try:
        if not path.is_file() or path.stat().st_size == 0:
            raise GadgetbridgeError("尚未收到有效的 Gadgetbridge 数据库")
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
            connection.execute("PRAGMA query_only = ON")
            _validate_schema(connection)
            rows = connection.execute(
                'SELECT TIMESTAMP, OTHER_TIMESTAMP, DEVICE_ID, USER_ID '
                'FROM HUAWEI_ACTIVITY_SAMPLE WHERE SOURCE IN (10, 13) '
                'AND RAW_KIND IN (?, ?, ?, ?)', SLEEP_KINDS,
            ).fetchall()
            has_new_sleep_data = _has_new_sleep_data(connection)
        result = _sleep_intervals(rows, window, now)
        if not result.intervals and has_new_sleep_data:
            raise UnsupportedSleepStages("当前睡眠阶段格式尚未支持")
        return result
    except (sqlite3.Error, OSError, OverflowError) as error:
        raise GadgetbridgeError("无法读取 Gadgetbridge 睡眠数据库") from error


def _validate_schema(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
        raise GadgetbridgeError("Gadgetbridge 数据库完整性检查失败")
    columns = {row[1].upper() for row in connection.execute('PRAGMA table_info("HUAWEI_ACTIVITY_SAMPLE")')}
    if not REQUIRED_COLUMNS.issubset(columns):
        raise GadgetbridgeError("不支持的 Gadgetbridge 华为活动表结构")


def _has_new_sleep_data(connection: sqlite3.Connection) -> bool:
    tables = {row[0].upper() for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for name in ("HUAWEI_SLEEP_STAGE_SAMPLE", "HUAWEI_SLEEP_STATS_SAMPLE"):
        if name in tables and connection.execute(f'SELECT 1 FROM "{name}" LIMIT 1').fetchone():
            return True
    return False


def _sleep_intervals(rows: list[tuple], window: TimeRange, now: datetime) -> SleepData:
    identities = {(row[2], row[3]) for row in rows}
    if len(identities) > 1:
        raise GadgetbridgeError("存在多个设备或用户的睡眠记录，需要明确选择来源")
    intervals: list[TimeRange] = []
    latest: datetime | None = None
    for start, end, _, _ in rows:
        begin, finish = _timestamps(start, end, now)
        if begin > finish:  # 官方存储同时包含交换起止值的结束标记。
            continue
        latest = max(latest, finish) if latest else finish
        clipped = clip_range(TimeRange(begin, finish), window)
        if clipped:
            intervals.append(clipped)
    return SleepData(merge_ranges(intervals), latest)


def _timestamps(start: object, end: object, now: datetime) -> tuple[datetime, datetime]:
    if not all(type(value) is int and value > 0 for value in (start, end)) or start == end:
        raise GadgetbridgeError("睡眠起止时间无效")
    try:
        begin = datetime.fromtimestamp(start, now.tzinfo)
        finish = datetime.fromtimestamp(end, now.tzinfo)
    except (ValueError, OverflowError, OSError) as error:
        raise GadgetbridgeError("睡眠时间戳超出支持范围") from error
    if max(begin, finish) > now:
        raise GadgetbridgeError("睡眠记录包含未来时间或非秒级时间戳")
    return begin, finish

