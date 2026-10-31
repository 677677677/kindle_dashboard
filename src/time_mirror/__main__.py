"""时间镜子本地运行入口。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from time_mirror.collectors.activitywatch_android import validate_android_export
from time_mirror.core import TimeRange
from time_mirror.outlook_ics import configure_ics_url, default_secret_root, synchronize_outlook_ics
from time_mirror.runtime import (
    configure_outlook,
    outlook_sync_window,
    refresh_snapshot,
    serve_runtime,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Kindle 时间 Dashboard 本地运行时")
    parser.add_argument("--config", type=Path, help="仓库外的私有 JSON 配置")
    parser.add_argument("--runtime-root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--secret-root", type=Path, help=argparse.SUPPRESS)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("refresh", help="同步数据并生成一次真实快照")
    validate_phone = subparsers.add_parser("validate-phone", help="校验 Android ActivityWatch 导出")
    validate_phone.add_argument("path", type=Path)
    subparsers.add_parser("configure-outlook", help="在私有配置中启用 Outlook 日历")
    subparsers.add_parser("configure-outlook-ics", help="从标准输入加密保存 Outlook ICS 地址")
    outlook_window = subparsers.add_parser("outlook-window", help="输出本次日历同步查询窗口")
    outlook_window.add_argument("--force", action="store_true")
    sync_outlook = subparsers.add_parser("sync-outlook", help="同步已发布的 Outlook ICS 日历")
    sync_outlook.add_argument("--force", action="store_true")
    sync_outlook.add_argument("--quiet", action="store_true", help="后台运行时不写控制台")
    serve = subparsers.add_parser("serve", help="定时同步并提供只读快照")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--interval", type=int, default=300)
    arguments = parser.parse_args()
    runtime_root = arguments.runtime_root
    secret_root = arguments.secret_root or default_secret_root()
    config_path = arguments.config or (runtime_root / "config.json" if runtime_root else None)

    if arguments.command == "refresh":
        snapshot = refresh_snapshot(config_path, runtime_root)
        kindle = snapshot["sources"]["koreader"]["status"]
        print(f"快照已生成：Kindle 数据状态 {kindle}")
        return
    if arguments.command == "validate-phone":
        validate_android_export(arguments.path)
        return
    if arguments.command == "configure-outlook":
        configure_outlook(config_path)
        print("Outlook 日历已在仓库外私有配置中启用")
        return
    if arguments.command == "configure-outlook-ics":
        configure_ics_url(sys.stdin.read().strip(), secret_root)
        configure_outlook(config_path, source="ics")
        print("Outlook ICS 地址已使用当前 Windows 用户加密保存")
        return
    if arguments.command == "outlook-window":
        window = outlook_sync_window(config_path, runtime_root, force=arguments.force)
        print(json.dumps({"due": window is not None, **_window_document(window)}, separators=(",", ":")))
        return
    if arguments.command == "sync-outlook":
        configure_outlook(config_path, source="ics")
        calendar = synchronize_outlook_ics(
            config_path,
            runtime_root,
            secret_root=secret_root,
            force=arguments.force,
        )
        if calendar is None:
            if not arguments.quiet:
                print("今日已完成自动同步，跳过重复读取。")
            return
        refresh_snapshot(config_path, runtime_root)
        if not arguments.quiet:
            print(f"Outlook 日历已刷新：{len(calendar.events)} 项")
        return
    serve_runtime(config_path, runtime_root, port=arguments.port, interval_seconds=arguments.interval)


def _window_document(window: TimeRange | None) -> dict[str, str]:
    if window is None:
        return {}
    return {
        "coverageStart": window.start.isoformat(),
        "coverageEnd": window.end.isoformat(),
    }


if __name__ == "__main__":
    main()

