"""KOReader statistics.sqlite3 的只读快照与查询适配器。"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import shlex
from uuid import uuid4
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from time_mirror.core import TimeRange
from time_mirror.core import merge_ranges


REQUIRED_COLUMNS = {
    "book": {"id", "title", "authors"},
    "page_stat_data": {"id_book", "start_time", "duration"},
}


class DatabaseSchemaError(RuntimeError):
    """数据库缺少适配器所需的稳定字段。"""


@dataclass(frozen=True, slots=True)
class ReadingEvent:
    book_id: int
    title: str
    authors: str
    interval: TimeRange


@dataclass(frozen=True, slots=True)
class BookSummary:
    book_id: int
    title: str
    authors: str
    seconds: int
    last_read_at: datetime


def validate_database(path: Path) -> None:
    """在替换上一份快照前验证完整性与必要 schema。"""
    uri = f"{path.resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise DatabaseSchemaError("KOReader 数据库完整性检查失败")
        KoreaderDatabase._validate_schema(connection)


def copy_snapshot(
    host: str,
    port: int,
    user: str,
    private_key: Path,
    remote_path: str,
    target: Path,
    *,
    host_key_alias: str | None = None,
) -> Path:
    """经SSH传输只读SQLite备份，包含WAL提交；失败保留旧快照。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f"{target.name}.{uuid4().hex}.partial")
    command = _snapshot_command(host, port, user, private_key, remote_path, host_key_alias)
    try:
        with partial.open('wb') as output:
            subprocess.run(command, check=True, stdout=output, stderr=subprocess.PIPE, timeout=20)
        validate_database(partial)
        os.replace(partial, target)
    finally:
        partial.unlink(missing_ok=True)
    return target


def _snapshot_command(
    host: str,
    port: int,
    user: str,
    private_key: Path,
    remote_path: str,
    host_key_alias: str | None,
) -> list[str]:
    temporary = f"/tmp/time-mirror-{uuid4().hex}.sqlite3"
    # SQLite备份读取已提交的WAL；临时文件只在/tmp，退出时清理。
    remote = (f"set -e; trap {shlex.quote('rm -f ' + shlex.quote(temporary))} EXIT; "
              f"sqlite3 -readonly {shlex.quote(remote_path)} {shlex.quote('.backup ' + temporary)}; "
              f"cat {shlex.quote(temporary)}")
    command = [
        "ssh",
        "-i",
        str(private_key),
        "-p",
        str(port),
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=8",
        "-o",
        "StrictHostKeyChecking=yes",
    ]
    if host_key_alias:
        command.extend(["-o", f"HostKeyAlias={host_key_alias}"])
    return command + [f"{user}@{host}", remote]


class KoreaderDatabase:
    def __init__(self, path: Path, timezone_name: str = "Asia/Shanghai") -> None:
        self.path = path
        self.zone = ZoneInfo(timezone_name)

    def reading_events(self, window: TimeRange) -> tuple[ReadingEvent, ...]:
        with closing(self._connect()) as connection:
            self._validate_schema(connection)
            rows = connection.execute(self._event_query(), self._query_bounds(window)).fetchall()
        return tuple(self._row_to_event(row) for row in rows)

    def summarize(self, window: TimeRange) -> tuple[BookSummary, ...]:
        intervals: dict[tuple[int, str, str], list[TimeRange]] = defaultdict(list)
        for event in self.reading_events(window):
            intervals[(event.book_id, event.title, event.authors)].append(event.interval)
        summaries = (
            BookSummary(
                *key,
                sum(_clip_seconds(item, window) for item in merge_ranges(items)),
                min(max(item.end for item in items), window.end),
            )
            for key, items in intervals.items()
        )
        return tuple(sorted(summaries, key=lambda item: (-item.seconds, item.title)))

    def latest_event(self, before: datetime | None = None) -> ReadingEvent | None:
        with closing(self._connect()) as connection:
            self._validate_schema(connection)
            query = """
                SELECT p.id_book, b.title, b.authors, p.start_time, p.duration
                FROM page_stat_data AS p
                JOIN book AS b ON b.id = p.id_book
                WHERE p.duration > 0
            """
            parameters: tuple[int, ...] = ()
            if before is not None:
                query += " AND p.start_time < ?"
                parameters = (int(before.timestamp()),)
            query += """
                ORDER BY p.start_time DESC
                LIMIT 1
            """
            row = connection.execute(query, parameters).fetchone()
        return self._row_to_event(row) if row else None

    def _connect(self) -> sqlite3.Connection:
        uri = f"{self.path.resolve().as_uri()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _validate_schema(connection: sqlite3.Connection) -> None:
        for table, required in REQUIRED_COLUMNS.items():
            columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
            missing = required - columns
            if missing:
                names = ", ".join(sorted(missing))
                raise DatabaseSchemaError(f"KOReader 表 {table} 缺少字段：{names}")

    @staticmethod
    def _event_query() -> str:
        return """
            SELECT p.id_book, b.title, b.authors, p.start_time, p.duration
            FROM page_stat_data AS p
            JOIN book AS b ON b.id = p.id_book
            WHERE p.start_time < ?
              AND p.start_time + p.duration > ?
              AND p.duration > 0
            ORDER BY p.start_time, p.id_book
        """

    @staticmethod
    def _query_bounds(window: TimeRange) -> tuple[int, int]:
        return int(window.end.timestamp()), int(window.start.timestamp())

    def _row_to_event(self, row: sqlite3.Row) -> ReadingEvent:
        start = datetime.fromtimestamp(row["start_time"], timezone.utc).astimezone(self.zone)
        end = start + timedelta(seconds=row["duration"])
        return ReadingEvent(
            row["id_book"],
            row["title"] or "未命名书籍",
            row["authors"] or "",
            TimeRange(start, end),
        )


def _clip_seconds(item: TimeRange, window: TimeRange) -> int:
    start = max(item.start, window.start)
    end = min(item.end, window.end)
    return max(0, round((end - start).total_seconds()))

